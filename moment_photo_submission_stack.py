import argparse
from base64 import b64encode
from datetime import timedelta
from hashlib import sha256
import os
import secrets
import time
import unittest
from uuid import UUID, uuid4

from docker_stack import ROOT
from photo_submission_stack import FIRST_FACT, PHOTO_FACT, PhotoSubmissionStackTests
from submission_stack import NOTICE


MOMENT_CAPTION = 'Legenda sintética de um momento com fotos'


class MomentPhotoSubmissionStackTests(PhotoSubmissionStackTests):
    def draft_moment(self, code, photos, caption=MOMENT_CAPTION):
        return {'category': 'moment', 'format': 'photo', 'identification': self.draft(code)['identification'],
            'caption': caption, 'files': [{
                'fileKey': str(uuid4()), 'position': position, 'declaredMediaType': media_type,
                'sizeBytes': len(data), 'sha256': b64encode(sha256(data).digest()).decode('ascii'),
            } for position, (data, media_type) in enumerate(photos, 1)]}

    def verify_manifest_rejections(self, code, image):
        self.set_time(self.admission.isoformat())
        draft = self.draft_moment(code, [image] * 2)
        first, second = draft['files']
        malformed = (
            draft | {'files': []}, self.draft_moment(code, [image] * 11),
            draft | {'caption': '🎉' * 501}, draft | {'files': [first, second | {'fileKey': first['fileKey']}]},
            draft | {'files': [second, first]}, draft | {'files': [first, second | {'position': 1}]},
            draft | {'files': [first, second | {'sizeBytes': 20_000_001}]},
        )
        for item in malformed:
            status, _, details = self.post(code, item, str(uuid4()), 'Bearer ' + secrets.token_hex(32))
            self.assertEqual((400, 'REQUEST_VALIDATION_FAILED'), (status, details.get('code')))

    def upload_group(self, path, bearer, draft, photos):
        return [self.upload(data, self.authorize_upload(path, bearer, item['fileKey']))
            for item, (data, _) in zip(draft['files'][:len(photos)], photos, strict=True)]

    def send_evidence(self, upload, photo, received_at):
        self.notify_uploaded(*upload, received_at, len(photo[0]))
        self.wait_for_evidence(*upload)

    def assert_pending(self, path, bearer, positions):
        status, headers, state = self.get_status(path, bearer)
        self.assertEqual(200, status)
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertEqual(('receiving', positions), (state['state'], state['pendingPositions']))

    def confirmed_rows(self, path):
        attempt = str(UUID(path.rsplit('/', 1)[1]))
        return self.stack.app_sql(f'''
            SELECT record.received_at, record.caption, record.expected_file_count,
                   string_agg(file.position::text || ':' || encode(file.sha256, 'hex'), ',' ORDER BY file.position),
                   count(*) FILTER (WHERE original.state='stored'),
                   (SELECT count(*) FROM scrapbook.outbox_messages
                    WHERE type='{PHOTO_FACT}' AND payload->>'recordId'=record.id::text)
            FROM scrapbook.records AS record
            JOIN scrapbook.idempotent_operations AS operation ON operation.idempotent_operations_records_id=record.id
            JOIN scrapbook.files AS file ON file.files_records_id=record.id
            JOIN scrapbook.storage_objects AS original ON original.storage_objects_files_id=file.id AND original.kind='original'
            WHERE operation.key='{attempt}' AND record.state='confirmed'
            GROUP BY record.id;
        ''')

    def verify_confirmation(self, event, code, other, photos, caption, at_deadline):
        self.set_time(self.admission.isoformat())
        draft = self.draft_moment(code, photos, caption)
        path, bearer = self.admit(code, draft)
        count = len(photos)
        self.assert_pending(path, bearer, list(range(1, count + 1)))
        with_other_event = path.replace(code, other, 1)
        self.assertEqual(404, self.authorize_upload(with_other_event, bearer, draft['files'][0]['fileKey'])[0])
        self.assertEqual(404, self.authorize_upload(path, bearer, str(uuid4()))[0])
        uploads = self.upload_group(path, bearer, draft, photos[:-1])
        for index in reversed(range(count - 1)):
            self.send_evidence(uploads[index], photos[index], self.admission)
        if count > 1:
            self.assert_pending(path, bearer, [count])
            status, _, details = self.authorize_upload(path, bearer, draft['files'][0]['fileKey'])
            self.assertEqual((409, 'UPLOAD_NOT_AVAILABLE'), (status, details.get('code')))
            self.assertEqual('', self.confirmed_rows(path))
            status, _, state = self.request(path + '/completion', anonymous=True, draft={}, headers={'Authorization': bearer})
            self.assertEqual((202, 'receiving', [count]), (status, state['state'], state['pendingPositions']))

        uploads.append(self.upload(photos[-1][0], self.authorize_upload(path, bearer, draft['files'][-1]['fileKey'])))
        self.assertEqual(count, len({key for key, _ in uploads}))
        last_arrival = self.deadline if at_deadline else self.admission
        if at_deadline:
            self.set_time(self.after_deadline.isoformat())
        self.send_evidence(uploads[-1], photos[-1], last_arrival)
        receipt = self.wait_for_status(path, bearer, 'confirmed')
        self.assertEqual({'state', 'confirmedAt'}, set(receipt))
        self.notify_uploaded(*uploads[-1], last_arrival, len(photos[-1][0]))
        r, _, replay = self.post(code, draft, path.rsplit('/', 1)[1], bearer)
        self.assertEqual((200, receipt), (r, replay))
        changed = dict(draft, caption=caption[:-1] + '.')
        self.assertEqual('IDEMPOTENCY_KEY_REUSED', self.post(code, changed, path.rsplit('/', 1)[1], bearer)[2]['code'])
        joined = self.confirmed_rows(path).split('|')
        self.assertEqual([caption, str(count), ','.join(f'{i}:{sha256(photo[0]).hexdigest()}'
            for i, photo in enumerate(photos, 1)), str(count), str(count)], joined[1:])
        if at_deadline:
            self.assertEqual(self.received_at, joined[0])
        self.assertEqual('1', self.stack.app_sql(f"SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={event} AND type='{FIRST_FACT}';"))
        return self.protected_values(code, draft, bearer, uploads)

    def verify_failed_group(self, event, code, valid, invalid, missing_at_deadline=False):
        self.set_time(self.admission.isoformat())
        photos = [valid, invalid]
        draft = self.draft_moment(code, photos)
        path, bearer = self.admit(code, draft)
        uploads = self.upload_group(path, bearer, draft, photos)
        self.send_evidence(uploads[0], valid, self.admission)
        self.assert_pending(path, bearer, [2])
        if missing_at_deadline:
            self.set_time(self.after_deadline.isoformat())
            self.send_evidence(uploads[1], invalid, self.deadline + timedelta(milliseconds=1))
            self.set_time(self.after_reconciliation.isoformat())
            terminal = self.wait_for_status(path, bearer, 'expired')
        else:
            self.send_evidence(uploads[1], invalid, self.admission)
            terminal = self.wait_for_status(path, bearer, 'rejected')
        reason = 'submission-deadline-exceeded' if missing_at_deadline else 'photo-invalid'
        self.assertEqual([{'position': 2, 'code': reason}], terminal['problems'])
        self.assertEqual('', self.confirmed_rows(path))
        self.assertEqual('0|0', self.stack.app_sql(f'''
            SELECT (SELECT count(*) FROM scrapbook.records WHERE records_events_id={event} AND confirmed_at IS NOT NULL),
                   (SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={event} AND type='{PHOTO_FACT}');
        '''))
        self.assertEqual('0', self.stack.app_sql(f'''
            SELECT count(*) FROM scrapbook.storage_objects AS object
            JOIN scrapbook.files AS file ON file.id=object.storage_objects_files_id
            JOIN scrapbook.records AS record ON record.id=file.files_records_id
            WHERE record.records_events_id={event} AND object.kind='original' AND object.state='stored';
        '''))
        return uploads, self.protected_values(code, draft, bearer, uploads)

    def verify_rejected_temporary_cleanup(self, uploads):
        self.set_time((self.after_reconciliation + timedelta(days=2)).isoformat())
        for key, version in uploads:
            deadline = time.monotonic() + 25
            while version in self.object_versions(key):
                if time.monotonic() >= deadline:
                    self.fail('A versão temporária elegível do conjunto rejeitado permaneceu na fixture.')
                time.sleep(0.4)

    @staticmethod
    def protected_values(code, draft, bearer, uploads):
        return (code, draft['caption'], draft['identification']['consentProof'], bearer.split()[1],
            *(key for key, _ in uploads))

    def test_moment_photo_submission(self):
        self.configure_photos()
        self.prepare()
        self.set_photo_clock()
        first_event, first = self.create_photo_event('Momento sintético A')
        second_event, second = self.create_photo_event('Momento sintético B')
        self.seed_notice(NOTICE, '2020-01-01T00:00:00Z')
        files = ROOT / 'photos' / 'fixtures'
        png = ((files / 'palette.png').read_bytes(), 'image/png')
        jpeg = ((files / 'palette.jpg').read_bytes(), 'image/jpeg')
        self.verify_manifest_rejections(first, png)
        single = self.verify_confirmation(first_event, first, second, [png], MOMENT_CAPTION, False)
        ordered = self.verify_confirmation(first_event, first, second, [jpeg, png] * 5, '🎉' * 500, True)
        rejected, invalid = self.verify_failed_group(second_event, second, png, (b'conteudo de teste sem foto', 'image/png'))
        late, unavailable = self.verify_failed_group(second_event, second, png, jpeg, missing_at_deadline=True)
        self.verify_rejected_temporary_cleanup(rejected + late)
        logs = self.stack.logs('service') + self.stack.logs('ui')
        if any(marker in logs for marker in (*single, *ordered, *invalid, *unavailable, 'X-Amz-Signature')):
            self.fail('A telemetria expôs conteúdo da fixture.')
        print('Ordem, recepção parcial, confirmação integral e limpeza conferidas.', flush=True)


if __name__ == '__main__':
    argparse.ArgumentParser(description='Valida conjuntos de fotos em PostgreSQL e S3 versionado descartáveis.').parse_args()
    suite = unittest.TestSuite([MomentPhotoSubmissionStackTests('test_moment_photo_submission')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

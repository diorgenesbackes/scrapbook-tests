import argparse
from base64 import b64encode
from datetime import timedelta
from hashlib import sha256
import json
import os
import secrets
import subprocess
import unittest
from uuid import uuid4
import wave

from docker_stack import ROOT
from photo_submission_stack import FIRST_FACT, PHOTO_FACT, PhotoSubmissionStackTests, utc_timestamp
from submission_stack import NOTICE


AUDIO_FIXTURES = ROOT / "media" / "fixtures"
AUDIO_TYPES = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
}
MAX_AUDIO_BYTES = 50_000_000


class AudioSubmissionStackTests(PhotoSubmissionStackTests):
    include_audio_browser = False

    def audio_manifest(self, code, content, media_type, *, caption=None):
        return {
            "category": "testimonial",
            "format": "audio",
            "identification": self.draft(code)["identification"],
            "caption": caption,
            "files": [{
                "fileKey": str(uuid4()),
                "position": 1,
                "declaredMediaType": media_type,
                "sizeBytes": len(content),
                "sha256": b64encode(sha256(content).digest()).decode("ascii"),
            }],
        }

    def audio_wav(self, name, *, frames, rate, channels):
        path = self.stack.env_file.parent / name
        with wave.open(str(path), "wb") as audio:
            audio.setparams((channels, 2, rate, frames, "NONE", "not compressed"))
            remaining = frames * 2 * channels
            silence = bytes(64 * 1024)
            while remaining:
                chunk = min(remaining, len(silence))
                audio.writeframesraw(silence[:chunk])
                remaining -= chunk
        return path.read_bytes()

    def upload_status(self, path, bearer, file_key):
        return self.request(f"{path}/files/{file_key}/upload-status", anonymous=True,
            headers={"Authorization": bearer})

    def verify_manifest_rejections(self, code):
        content = (AUDIO_FIXTURES / "tone.wav").read_bytes()
        draft = self.audio_manifest(code, content, "audio/wav")
        file, = draft["files"]
        for invalid in (
            draft | {"category": "moment"},
            draft | {"caption": "🎵" * 501},
            draft | {"files": [file | {"sizeBytes": 0}]},
            draft | {"files": [file | {"sizeBytes": MAX_AUDIO_BYTES + 1}]},
            draft | {"files": [file | {"declaredMediaType": "video/webm"}]},
            draft | {"files": [file | {"partSha256": [file["sha256"]]}]},
            draft | {"files": [file, file | {"fileKey": str(uuid4()), "position": 2}]},
        ):
            status, _, result = self.post(code, invalid, str(uuid4()), "Bearer " + secrets.token_hex(32))
            self.assertEqual((400, "REQUEST_VALIDATION_FAILED"), (status, result.get("code")))

    def verify_audio(self, code, other, content, media_type, *, state="confirmed", receipt_at="open", caption=None):
        self.set_time(self.admission.isoformat())
        draft = self.audio_manifest(code, content, media_type, caption=caption)
        path, bearer = self.admit(code, draft)
        file_key = draft["files"][0]["fileKey"]
        wrong = "Bearer " + secrets.token_hex(32)
        self.assertEqual(404, self.upload_status(path, wrong, file_key)[0])
        self.assertEqual(404, self.authorize_upload(path.replace(code, other, 1), bearer, file_key)[0])
        self.assertEqual(404, self.authorize_upload(path, bearer, str(uuid4()))[0])

        status, _, upload = self.upload_status(path, bearer, file_key)
        self.assertEqual((200, "put", []), (status, upload["mode"], upload["uploadedParts"]))
        grant = self.authorize_upload(path, bearer, file_key)
        self.assertEqual(200, grant[0])
        key, version = self.upload(content, self.authorize_upload(path, bearer, file_key))
        self.assertEqual(grant[2]["headers"]["x-amz-checksum-sha256"], draft["files"][0]["sha256"])
        _, _, reply = self.request(path + "/completion", anonymous=True, draft={}, headers={"Authorization": bearer})
        self.assertNotEqual("confirmed", reply["state"])
        confirmation_count = self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.outbox_messages AS event
            JOIN scrapbook.files AS file ON event.payload->>'fileId' = file.id::text
            WHERE file.client_key = '{file_key}' AND event.type = '{PHOTO_FACT}';
        """)
        self.assertEqual("0", confirmation_count)

        event_time = self.admission
        if receipt_at != "open":
            self.set_time(self.after_deadline.isoformat())
            event_time = self.deadline if receipt_at == "cutoff" else self.deadline + timedelta(milliseconds=1)
            self.assertEqual((409, "UPLOAD_COMPLETION_DEADLINE_EXCEEDED"),
                self.problem(path + "/files/" + file_key + "/upload-authorizations", anonymous=True,
                    draft={}, headers={"Authorization": bearer}))
        self.notify_uploaded(key, version, event_time, len(content))
        self.wait_for_evidence(key, version)
        if receipt_at == "late":
            self.set_time(self.after_reconciliation.isoformat())
        receipt = self.wait_for_status(path, bearer, state)
        self.notify_uploaded(key, version, event_time, len(content))
        retry_status, _, replay = self.post(code, draft, path.rsplit("/", 1)[1], bearer)
        self.assertEqual((200, receipt), (retry_status, replay))

        row = self.stack.app_sql(f"""
            SELECT record.state, file.kind, file.validation_state, coalesce(file.media_type, ''),
                coalesce(file.audio_codec, ''), coalesce(file.duration::text, ''), coalesce(file.actual_size::text, ''),
                coalesce(encode(file.sha256, 'hex'), ''), original.state,
                coalesce(original.version_id, ''), original.object_key,
                (SELECT count(*) FROM scrapbook.outbox_messages AS event
                WHERE event.payload->>'fileId' = file.id::text AND event.type = '{PHOTO_FACT}'),
                coalesce(char_length(record.caption)::text, '')
            FROM scrapbook.files AS file
            JOIN scrapbook.records AS record ON record.id = file.files_records_id
            JOIN scrapbook.storage_objects AS original ON original.storage_objects_files_id = file.id
                AND original.kind = 'original'
            WHERE file.client_key = '{file_key}';
        """).split("|")
        if state != "confirmed":
            self.assertEqual((state, "audio", "0"), (row[0], row[1], row[11]))
            self.assertEqual(set(), self.object_versions(row[10]))
            failure = self.stack.app_sql(f"""
                SELECT session.last_error_code, session.terminal_at IS NOT NULL,
                    session.request_correlation_id IS NOT NULL
                FROM scrapbook.upload_sessions AS session
                JOIN scrapbook.files AS file ON file.files_records_id = session.upload_sessions_records_id
                WHERE file.client_key = '{file_key}';
            """)
            expected = "audio-received-late" if receipt_at == "late" else "audio-invalid"
            self.assertEqual(expected + "|t|t", failure)
            return None

        self.assertEqual({"state", "confirmedAt"}, set(receipt))
        self.assertEqual(["confirmed", "audio", "valid", media_type], row[:4])
        self.assertTrue(row[4])
        self.assertGreater(float(row[5]), 0)
        self.assertLessEqual(float(row[5]), 300)
        self.assertEqual([str(len(content)), sha256(content).hexdigest(), "stored"], row[6:9])
        self.assertEqual({row[9]}, self.object_versions(row[10]))
        self.assertEqual(("1", "" if caption is None else str(len(caption))), (row[11], row[12]))
        if receipt_at == "cutoff":
            arrival = self.stack.app_sql(f"""
                SELECT record.received_at FROM scrapbook.records AS record
                JOIN scrapbook.files AS file ON file.files_records_id = record.id
                WHERE file.client_key = '{file_key}';
            """)
            self.assertEqual(self.received_at, arrival)
        return key, row[10], row[9]

    def run_audio_browser(self, first, second, *, grep=None):
        fixture = self.stack.env_file.parent / "audio-fixture.json"
        fixture.write_text(json.dumps(self.storage), encoding="utf-8")
        fixture.chmod(0o600)
        env = self.stack.environment | {
            "PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first,
            "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file),
            "SCRAPBOOK_TEST_AUDIO_FIXTURE": str(fixture),
            "SCRAPBOOK_TEST_AUDIO_OPEN": utc_timestamp(self.admission),
            "SCRAPBOOK_TEST_AUDIO_DEADLINE": utc_timestamp(self.deadline),
        }
        env.pop("NO_COLOR", None)
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.audio-submission.config.ts",
            *(("--grep", grep) if grep else ())],
            cwd=ROOT, env=env, check=True, timeout=300)

    def test_audio_submission(self):
        self.configure_photos(enable_audio=True)
        self.prepare()
        self.set_photo_clock()
        event_id, first = self.create_photo_event("Depoimento em áudio A")
        _, second = self.create_photo_event("Depoimento em áudio B")
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")
        self.verify_manifest_rejections(first)
        for suffix, media_type in AUDIO_TYPES.items():
            self.verify_audio(first, second, (AUDIO_FIXTURES / f"tone.{suffix}").read_bytes(), media_type)

        limit = self.audio_wav("boundary.wav", frames=300 * 8000, rate=8000, channels=1)
        retained = self.verify_audio(first, second, limit, "audio/wav", receipt_at="cutoff", caption="🎵" * 500)
        self.assertIsNotNone(retained)
        excessive = self.audio_wav("over.wav", frames=301 * 8000, rate=8000, channels=1)
        self.verify_audio(second, first, excessive, "audio/wav", state="rejected")
        delayed = (AUDIO_FIXTURES / "audio-timeline.m4a").read_bytes()
        self.verify_audio(second, first, delayed, "audio/mp4", state="rejected")
        maximum = self.audio_wav("size-limit.wav", frames=(MAX_AUDIO_BYTES - 44) // 4, rate=48000, channels=2)
        self.assertEqual(MAX_AUDIO_BYTES, len(maximum))
        self.verify_audio(first, second, maximum, "audio/wav")
        self.verify_audio(second, first, (AUDIO_FIXTURES / "short.webm").read_bytes(), "audio/webm", state="rejected")
        self.verify_audio(second, first, b"conteudo sintetico sem faixa de audio", "audio/mpeg", state="rejected")
        self.verify_audio(second, first, (AUDIO_FIXTURES / "tone.wav").read_bytes(), "audio/wav",
            state="rejected", receipt_at="late")
        self.verify_cleanup(*retained)
        self.assertEqual("1", self.stack.app_sql(
            f"SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={event_id} AND type='{FIRST_FACT}';"))
        self.set_time(self.admission.isoformat())
        if self.include_audio_browser:
            self.run_audio_browser(first, second)
        print("Áudio, limites, versão privada, prazo, recibo, limpeza e Outbox verificados.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    parser = argparse.ArgumentParser(description="Confere depoimentos em áudio em uma stack local descartável.")
    parser.add_argument("--with-browser", action="store_true", help="Inclui as jornadas Chromium.")
    AudioSubmissionStackTests.include_audio_browser = parser.parse_args().with_browser
    suite = unittest.TestSuite([AudioSubmissionStackTests("test_audio_submission")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import secrets
import subprocess
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import urlopen
from uuid import UUID, uuid4

from docker_stack import ROOT
from moment_photo_submission_stack import MomentPhotoSubmissionStackTests
from photo_stack import photo_fixture
from photo_submission_stack import PHOTO_FACT
from submission_stack import NOTICE


FAILURE_FACT = "record.media-preparation.failed.v1"
PROMOTION_GATE = 581001
LOCATION_MARKERS = (b"Exif\0\0", b"http://ns.adobe.com/xap/1.0/", b"Photoshop 3.0\0")


@dataclass(frozen=True)
class Photo:
    path: str
    media_type: str
    image: tuple[int, int]
    thumbnail: tuple[int, int]

    def sample(self):
        return (ROOT / self.path).read_bytes(), self.media_type


ORIENTED = Photo("media-processing/fixtures/orientation-gps.jpg", "image/jpeg", (1024, 2048), (200, 400))
TRANSPARENT = Photo("media-processing/fixtures/transparent-small.png", "image/png", (128, 64), (128, 64))
HEIC = Photo("photos/fixtures/palette.heic", "image/heic", (32, 32), (32, 32))
SMALL = Photo("photos/fixtures/palette.jpg", "image/jpeg", (32, 32), (32, 32))


class MediaProcessingStackTests(MomentPhotoSubmissionStackTests):
    include_browser = False

    def start_identity(self, suffix, origin, account, authorization):
        if suffix == "other":
            super().start_identity(suffix, origin, account, authorization)

    def configure_processing(self):
        self.configure_photos()
        self.topic = self.aws(("sns", "create-topic"), "--name", "fixture-f1001")["TopicArn"]
        self.queue = self.subscribe("fixture-f1001-photos")
        self.assertEqual(self.storage["endpoint"], f"{urlsplit(self.queue).scheme}://{urlsplit(self.queue).netloc}")
        internal_endpoint = self.storage["environment"]["PhotoSubmissions__QueueServiceUrl"]
        override = self.stack.env_file.parent / "media-processing.compose.json"
        override.write_text(json.dumps({"services": {"service": {"environment": {
            "ConnectionStrings__ScrapBook": "Host=postgres;Port=5432;Database=scrapbook;Username=scrapbook_app;"
                "Password=${SCRAPBOOK_DB_APP_PASSWORD};Timeout=2;Command Timeout=60",
            "MediaProcessing__Enabled": "true",
            "MediaProcessing__Region": self.storage["environment"]["PhotoSubmissions__Region"],
            "MediaProcessing__TopicArn": self.topic,
            "MediaProcessing__QueueUrl": internal_endpoint + urlsplit(self.queue).path,
            "MediaProcessing__SnsServiceUrl": internal_endpoint,
            "MediaProcessing__SqsServiceUrl": internal_endpoint,
        }}}}), encoding="utf-8")
        self.stack.compose.extend(("--file", str(override)))
        self.prepare()
        self.set_photo_clock()
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")

    def subscribe(self, name):
        queue = self.aws(("sqs", "create-queue"), "--queue-name", name,
            "--attributes", "VisibilityTimeout=10")["QueueUrl"]
        arn = self.aws(("sqs", "get-queue-attributes"), "--queue-url", queue,
            "--attribute-names", "QueueArn")["Attributes"]["QueueArn"]
        policy = {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"}, "Action": "sqs:SendMessage",
            "Resource": arn, "Condition": {"ArnEquals": {"aws:SourceArn": self.topic}},
        }]}
        self.aws(("sqs", "set-queue-attributes"), "--queue-url", queue,
            "--attributes", json.dumps({"Policy": json.dumps(policy)}))
        self.aws(("sns", "subscribe"), "--topic-arn", self.topic, "--protocol", "sqs", "--notification-endpoint", arn,
            "--attributes", json.dumps({"RawMessageDelivery": "true", "FilterPolicyScope": "MessageAttributes",
                "FilterPolicy": json.dumps({"type": [PHOTO_FACT]})}))
        return queue

    def wait_for(self, action, message, timeout=35):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if result := action():
                return result
            time.sleep(0.2)
        self.fail(message)

    def wait_queue_drained(self):
        def drained():
            counts = self.aws(("sqs", "get-queue-attributes"), "--queue-url", self.queue,
                "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed")["Attributes"]
            return sum(map(int, counts.values())) == 0

        self.wait_for(drained, "A fila local de preparação não esvaziou.")

    def submit_photos(self, code, draft, samples):
        path, bearer = self.admit(code, draft)
        uploads = self.upload_group(path, bearer, draft, samples)
        for upload, sample in zip(uploads, samples, strict=True):
            self.notify_uploaded(*upload, self.admission, len(sample[0]))
        receipt = self.wait_for_status(path, bearer, "confirmed")
        self.assertEqual({"state", "confirmedAt"}, set(receipt))
        return path, bearer, receipt

    def assert_receipt(self, code, draft, submission):
        path, bearer, receipt = submission
        status, headers, stored = self.get_status(path, bearer)
        self.assertEqual((200, "no-store", receipt), (status, headers["Cache-Control"], stored))
        status, _, replay = self.post(code, draft, path.rsplit("/", 1)[1], bearer)
        self.assertEqual((200, receipt), (status, replay))

    def file_ids(self, path):
        attempt = UUID(path.rsplit("/", 1)[1])
        return [int(file) for file in self.stack.app_sql(f"""
            SELECT file.id FROM scrapbook.files AS file
            JOIN scrapbook.idempotent_operations AS operation
                ON operation.idempotent_operations_records_id = file.files_records_id
            WHERE operation.key = '{attempt}' ORDER BY file.position;
        """).splitlines()]

    def file_facts(self, file):
        return self.stack.app_sql(f"""
            SELECT fact.message_id, job.state, job.attempt_count, file.processing_state,
                   count(attempt.id), bool_and(attempt.processing_attempts_files_id = file.id)
            FROM scrapbook.files AS file
            JOIN scrapbook.records AS record ON record.id = file.files_records_id
            JOIN scrapbook.outbox_messages AS fact ON fact.payload->>'fileId' = file.id::text
                 AND fact.payload->>'recordId' = record.id::text AND fact.state = 'published'
                 AND fact.payload->>'eventId' = record.records_events_id::text AND fact.type = '{PHOTO_FACT}'
            JOIN scrapbook.processing_jobs AS job ON job.processing_jobs_files_id = file.id
                 AND job.kind = 'photo_prepare' AND job.profile_version = file.profile_version
                 AND job.confirmation_message_id = fact.message_id
                 AND job.confirmation_correlation_id = fact.correlation_id
                 AND job.confirmed_at = (fact.payload->>'confirmedAt')::timestamptz
            JOIN scrapbook.processing_attempts AS attempt ON attempt.processing_attempts_processing_jobs_id = job.id
            WHERE file.id = {file} AND record.state = 'confirmed' AND file.validation_state = 'valid'
            GROUP BY fact.id, job.id, file.id;
        """).splitlines()

    def variant_rows(self, file):
        output = self.stack.app_sql(f"""
            SELECT variant.kind, variant.width, variant.height, object.bucket, object.object_key, object.version_id,
                   object.size, encode(object.sha256, 'hex'), object.storage_objects_processing_attempts_id,
                   variant.xmin::text
            FROM scrapbook.file_variants AS variant
            JOIN scrapbook.files AS file ON file.id = variant.file_variants_files_id
                AND file.profile_version = variant.profile_version AND file.processing_state = 'available'
                AND file.xmin = variant.xmin
            JOIN scrapbook.storage_objects AS object ON object.id = variant.file_variants_storage_objects_id
                AND object.storage_objects_files_id = file.id AND object.kind = 'derived' AND object.state = 'stored'
            JOIN scrapbook.processing_attempts AS attempt ON attempt.id = object.storage_objects_processing_attempts_id
                AND attempt.processing_attempts_files_id = file.id AND attempt.state = 'succeeded'
            JOIN scrapbook.processing_jobs AS job ON job.id = attempt.processing_attempts_processing_jobs_id
                AND job.processing_jobs_files_id = file.id AND job.profile_version = variant.profile_version
                AND job.kind = 'photo_prepare' AND job.state = 'completed'
            WHERE file.id = {file} AND variant.state = 'ready' AND variant.media_type = 'image/jpeg'
                AND variant.location_removed IS TRUE AND variant.ready_at IS NOT NULL
            ORDER BY variant.kind;
        """)
        return [line.split("|") for line in output.splitlines()]

    def read_object(self, bucket, key, version):
        self.assertEqual(self.storage["bucket"], bucket)
        self.assertTrue(version and version != "null")
        path = self.stack.env_file.parent / "inspected-object"
        reply = self.aws(("s3api", "get-object"), "--bucket", bucket, "--key", key,
            "--version-id", version, str(path))
        self.assertEqual(version, reply["VersionId"])
        return reply, path.read_bytes()

    def assert_private(self, bucket, key, version):
        query = urlencode({"versionId": version})
        url = f"{self.storage['endpoint']}/{bucket}/{quote(key)}?{query}"
        with self.assertRaises(HTTPError) as denied:
            urlopen(url, timeout=5)
        self.assertIn(denied.exception.code, (403, 404))
        denied.exception.close()

    def original(self, file):
        return self.stack.app_sql(f"""
            SELECT bucket, object_key, version_id
            FROM scrapbook.storage_objects
            WHERE storage_objects_files_id = {file} AND kind = 'original' AND state = 'stored';
        """).split("|")

    def assert_original(self, file, body):
        bucket, key, version = self.original(file)
        self.assertRegex(key, r"^submissions/original/[0-9a-f]{32}$")
        self.assertEqual({version}, self.object_versions(key))
        _, stored = self.read_object(bucket, key, version)
        self.assertEqual(sha256(body).digest(), sha256(stored).digest(), "O original confirmado mudou.")
        self.assert_private(bucket, key, version)

    def assert_jpeg(self, body, dimensions, photo):
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
            "-of", "json", "pipe:0"], input=body, capture_output=True, check=True, timeout=10)
        (stream,) = json.loads(probe.stdout)["streams"]
        self.assertEqual(("mjpeg", *dimensions), (stream["codec_name"], stream["width"], stream["height"]))
        for marker in LOCATION_MARKERS:
            self.assertNotIn(marker, body, "A variante reteve um segmento de metadados privados.")
        side = 16
        decode = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-vf", f"scale={side}:{side}:flags=neighbor",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            input=body, capture_output=True, check=True, timeout=10)
        self.assertEqual(side * side * 3, len(decode.stdout))
        offsets = [3 * (side * y + x) for x, y in ((3, 3), (12, 3), (3, 12))]
        top, right, bottom = (decode.stdout[offset:offset + 3] for offset in offsets)
        if photo == ORIENTED:
            self.assertGreater(top[0], 180)
            self.assertLess(max(top[1:]), 75)
            self.assertGreater(bottom[2], 180)
            self.assertLess(max(bottom[:2]), 75)
        if photo == TRANSPARENT:
            self.assertGreater(min(top), 230)
            self.assertGreater(right[1], 150)
            self.assertLess(max(right[0], right[2]), 100)

    def assert_ready(self, file, photo, *, attempt_count=1):
        rows = self.wait_for(lambda: self.variant_rows(file),
            "A foto não ficou disponível com as duas variantes prontas na mesma transação.", timeout=55)
        self.assertEqual(["image", "thumbnail"], [row[0] for row in rows])
        self.assertEqual(1, len({row[8] for row in rows}))
        self.assertEqual(1, len({row[9] for row in rows}))
        facts = self.wait_for(lambda: self.file_facts(file), "A confirmação de mídia não foi publicada.")
        self.assertEqual(1, len(facts))
        _, *state = facts[0].split("|")
        self.assertEqual(["completed", str(attempt_count), "available", str(attempt_count), "t"], state)
        for row, expected in zip(rows, (photo.image, photo.thumbnail), strict=True):
            kind, width, height, bucket, key, version, size, digest, *_ = row
            self.assertEqual(expected, (int(width), int(height)))
            self.assertRegex(key, rf"^submissions/derived/{file}/[a-f0-9]{{32}}/{kind}\.jpg$")
            reply, body = self.read_object(bucket, key, version)
            self.assertEqual(("image/jpeg", int(size), digest),
                (reply["ContentType"], len(body), sha256(body).hexdigest()))
            self.assertEqual({version}, self.object_versions(key))
            self.assert_private(bucket, key, version)
            self.assert_jpeg(body, expected, photo)
        self.assert_original(file, photo.sample()[0])
        print(f"Foto {photo.media_type} pronta com {photo.image} e {photo.thumbnail}, {attempt_count} tentativa(s).",
            flush=True)
        return rows

    def verify_receipts_and_formats(self):
        _, first = self.create_photo_event("Preparação das fotos de um momento")
        negative_event, negative_code = self.create_photo_event("Texto e momento rejeitado")
        _, third = self.create_photo_event("Preparação de HEIC")
        samples = [ORIENTED.sample(), TRANSPARENT.sample()]
        draft = self.draft_moment(first, samples)
        submission = self.submit_photos(first, draft, samples)
        files = self.file_ids(submission[0])
        self.assertEqual(2, len(files))
        rows = [self.assert_ready(file, photo)
            for file, photo in zip(files, (ORIENTED, TRANSPARENT), strict=True)]
        self.assert_receipt(first, draft, submission)
        self.assertEqual(4, len({variant[4] for pair in rows for variant in pair}))

        heic_draft = self.photo_draft(third, *HEIC.sample())
        heic = self.submit_photos(third, heic_draft, [HEIC.sample()])
        heic_file, = self.file_ids(heic[0])
        self.assert_ready(heic_file, HEIC)
        self.assert_receipt(third, heic_draft, heic)

        self.verify_failed_group(negative_event, negative_code, TRANSPARENT.sample(),
            (b"fixture sintetica sem imagem", "image/png"))
        text = self.post(negative_code, self.draft(negative_code), str(uuid4()), "Bearer " + secrets.token_hex(32))
        self.assertEqual((201, "confirmed"), (text[0], text[2]["state"]))
        self.wait_queue_drained()
        self.assertEqual("0|0", self.stack.app_sql(f"""
            SELECT (SELECT count(*) FROM scrapbook.outbox_messages
                    WHERE type = '{PHOTO_FACT}' AND aggregate_id = {negative_event}),
                   (SELECT count(*) FROM scrapbook.processing_jobs AS job
                    JOIN scrapbook.files AS file ON file.id = job.processing_jobs_files_id
                    JOIN scrapbook.records AS record ON record.id = file.files_records_id
                    WHERE record.records_events_id = {negative_event});
        """))
        self.assertEqual("3", self.stack.app_sql("SELECT count(*) FROM scrapbook.processing_jobs;"))

    @contextmanager
    def pause_promotion(self):
        self.admin_sql(f"""
            CREATE FUNCTION scrapbook.fixture_f1001_hold_promotion() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_advisory_xact_lock({PROMOTION_GATE});
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER fixture_f1001_hold_promotion
            BEFORE INSERT OR UPDATE ON scrapbook.file_variants FOR EACH ROW
            WHEN (NEW.kind = 'thumbnail' AND NEW.state = 'ready')
            EXECUTE FUNCTION scrapbook.fixture_f1001_hold_promotion();
        """)
        try:
            with subprocess.Popen([*self.stack.compose, "exec", "-T", "postgres", "psql", "-qXAt",
                    "-U", "postgres", "-d", "scrapbook", "-v", "ON_ERROR_STOP=1"], env=self.stack.environment,
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True) as lock:
                lock.stdin.write(f"SELECT pg_advisory_lock({PROMOTION_GATE});\n")
                lock.stdin.flush()
                try:
                    self.wait_for(lambda: self.promotion_sessions(granted=True),
                        "O banco não estabeleceu a pausa controlada da promoção.", timeout=10)
                    yield
                finally:
                    lock.stdin.close()
                    lock.wait(timeout=10)
        finally:
            self.admin_sql("""
                DROP TRIGGER fixture_f1001_hold_promotion ON scrapbook.file_variants;
                DROP FUNCTION scrapbook.fixture_f1001_hold_promotion();
            """)

    def promotion_sessions(self, *, granted):
        return int(self.stack.app_sql(f"""
            SELECT count(*) FROM pg_catalog.pg_locks
            WHERE locktype = 'advisory' AND classid = 0 AND objid = {PROMOTION_GATE}
                AND granted IS {str(granted).upper()};
        """))

    def capture_envelope(self, queue, file):
        def receive():
            reply = self.aws(("sqs", "receive-message"), "--queue-url", queue, "--wait-time-seconds", "1")
            return next(iter(reply.get("Messages", [])), None)

        message = self.wait_for(receive, "A confirmação F08 não foi observada na assinatura estrita F10.")
        body = message["Body"]
        envelope = json.loads(body)
        self.assertEqual((PHOTO_FACT, str(file)), (envelope["type"], envelope["data"]["fileId"]))
        return body, str(UUID(envelope["messageId"]))

    def kill_replicas(self):
        replica = self.stack.project + "-other"
        one_off = subprocess.check_output(["docker", "inspect", "--format", "{{.Id}}", replica],
            env=self.stack.environment, text=True, timeout=10).strip()
        services = self.stack.compose_command("ps", "-q", "service").stdout.splitlines()
        primary, = [service for service in services if not one_off.startswith(service)]
        subprocess.run(["docker", "kill", primary, one_off], env=self.stack.environment,
            capture_output=True, check=True, timeout=20)

    def terminate_disconnected_promotion_sessions(self):
        self.admin_sql(f"""
            SELECT pg_terminate_backend(activity.pid, 5000)
            FROM pg_catalog.pg_locks AS waiting
            JOIN pg_catalog.pg_stat_activity AS activity ON activity.pid = waiting.pid
            WHERE waiting.locktype = 'advisory' AND waiting.classid = 0
                AND waiting.objid = {PROMOTION_GATE} AND NOT waiting.granted
                AND activity.usename = 'scrapbook_app' AND activity.datname = current_database();
        """)

    def start_replicas(self):
        self.stack.compose_command("start", "--wait", "service")
        subprocess.run(["docker", "start", self.stack.project + "-other"], env=self.stack.environment,
            capture_output=True, check=True, timeout=20)
        self.stack.wait_for_health("/health/ready", 200, "up", port=self.stack.api_port)
        self.stack.wait_for_health("/health/ready", 200, "up", port=int(self.second_origin.rpartition(":")[2]))

    def verify_restart_and_replay(self):
        _, code = self.create_photo_event("Retomada de duas réplicas de fotos")
        observer = self.subscribe("fixture-f1001-observer")
        draft = self.photo_draft(code, *SMALL.sample())
        stopping = False
        try:
            with self.pause_promotion():
                submission = self.submit_photos(code, draft, [SMALL.sample()])
                file, = self.file_ids(submission[0])
                body, message_id = self.capture_envelope(observer, file)
                self.wait_for(lambda: self.promotion_sessions(granted=False),
                    "Nenhuma réplica aguardou a promoção depois das duas gravações no S3.")
                observed = self.aws(("s3api", "list-object-versions"), "--bucket", self.storage["bucket"],
                    "--prefix", f"submissions/derived/{file}/").get("Versions", [])
                self.assertEqual(["image.jpg", "thumbnail.jpg"], sorted(item["Key"].rsplit("/", 1)[1] for item in observed))
                self.assertTrue(all(item["VersionId"] and item["VersionId"] != "null" for item in observed))
                self.assertEqual("processing|0", self.stack.app_sql(f"""
                    SELECT file.processing_state,
                           (SELECT count(*) FROM scrapbook.file_variants AS variant
                            WHERE variant.file_variants_files_id = file.id AND variant.state = 'ready')
                    FROM scrapbook.files AS file WHERE file.id = {file};
                """))
                original_ref = self.original(file)
                self.assert_receipt(code, draft, submission)
                stopping = True
                self.kill_replicas()
                self.terminate_disconnected_promotion_sessions()
                self.wait_for(lambda: self.promotion_sessions(granted=False) == 0,
                    "A queda das réplicas não encerrou a promoção interrompida.", timeout=15)
        finally:
            if stopping:
                self.start_replicas()

        delivery_type = json.dumps({"type": {"DataType": "String", "StringValue": PHOTO_FACT}})
        for _ in range(2):
            self.aws(("sns", "publish"), "--topic-arn", self.topic, "--message", body,
                "--message-attributes", delivery_type)
        self.set_time((self.admission + timedelta(minutes=3)).isoformat())

        ready = self.assert_ready(file, SMALL, attempt_count=2)
        self.wait_queue_drained()
        self.assertEqual(ready, self.variant_rows(file))
        self.assertEqual(["1|failed|MEDIA_ATTEMPT_INTERRUPTED", "2|succeeded|"], self.stack.app_sql(f"""
            SELECT number, state, error_code FROM scrapbook.processing_attempts
            WHERE processing_attempts_files_id = {file} ORDER BY number;
        """).splitlines())
        self.assertEqual(original_ref, self.original(file))
        self.assert_receipt(code, draft, submission)
        self.assertEqual("1", self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.processed_messages
            WHERE message_id = '{message_id}' AND state = 'completed';
        """))
        print("Duas réplicas retomaram após gravação no S3 e receberam o mesmo envelope sem duplicar variantes.", flush=True)

    def set_queue_delay(self, seconds):
        self.aws(("sqs", "set-queue-attributes"), "--queue-url", self.queue,
            "--attributes", f"DelaySeconds={seconds}")

    def replace_source_checksum(self, file, digest):
        self.stack.app_sql(f"""
            WITH source AS (
                UPDATE scrapbook.files SET sha256 = decode('{digest}', 'hex') WHERE id = {file} RETURNING id
            )
            UPDATE scrapbook.storage_objects SET sha256 = decode('{digest}', 'hex')
            WHERE storage_objects_files_id IN (SELECT id FROM source) AND kind = 'original';
        """)

    def failure_facts(self, file):
        return self.stack.app_sql(f"""
            SELECT failure.payload->>'stage', failure.payload->>'code', file.processing_state,
                   (SELECT count(*) FROM scrapbook.file_variants
                    WHERE file_variants_files_id = file.id AND state = 'ready')
            FROM scrapbook.files AS file
            JOIN scrapbook.records AS record ON record.id = file.files_records_id
            JOIN scrapbook.outbox_messages AS confirmation ON confirmation.type = '{PHOTO_FACT}'
                 AND confirmation.payload->>'fileId' = file.id::text
            JOIN scrapbook.outbox_messages AS failure ON failure.type = '{FAILURE_FACT}'
                 AND failure.correlation_id = confirmation.correlation_id
                 AND failure.causation_id = confirmation.message_id
                 AND failure.payload->>'fileId' = file.id::text
                 AND failure.payload->>'recordId' = record.id::text
                 AND failure.aggregate_type = 'event' AND failure.aggregate_id = record.records_events_id
            WHERE file.id = {file};
        """).splitlines()

    def verify_permanent_failure_preserves_receipt(self):
        self.set_time(self.admission.isoformat())
        _, code = self.create_photo_event("Falha sintética de integridade")
        draft = self.photo_draft(code, *SMALL.sample())
        self.set_queue_delay(20)
        try:
            submission = self.submit_photos(code, draft, [SMALL.sample()])
            file, = self.file_ids(submission[0])
            self.assertEqual("0", self.stack.app_sql(f"""
                SELECT count(*) FROM scrapbook.processing_jobs WHERE processing_jobs_files_id = {file};
            """))
            original = self.original(file)
            self.replace_source_checksum(file, "00" * 32)
        finally:
            self.set_queue_delay(0)
        try:
            self.assertEqual(["prepare|MEDIA_SOURCE_INTEGRITY|failed|0"], self.wait_for(
                lambda: self.failure_facts(file), "A falha definitiva correlacionada não foi registrada.", timeout=55))
        finally:
            self.replace_source_checksum(file, sha256(SMALL.sample()[0]).hexdigest())
        self.assertEqual(original, self.original(file))
        self.assert_original(file, SMALL.sample()[0])
        self.assert_receipt(code, draft, submission)

    def verify_browser_receipts_and_readiness(self):
        self.set_time(self.admission.isoformat())
        first_event, first = self.create_photo_event("Recebimento no navegador com foto selecionada")
        second_event, second = self.create_photo_event("Recebimento no navegador com câmera emulada")
        representative = self.stack.env_file.parent / "media-processing-browser.png"
        photo_fixture(representative)
        self.run_photo_browser(first, second, representative)
        for event in (first_event, second_event):
            files = [int(row) for row in self.stack.app_sql(f"""
                SELECT file.id FROM scrapbook.files AS file
                JOIN scrapbook.records AS record ON record.id = file.files_records_id
                WHERE record.records_events_id = {event} AND record.state = 'confirmed'
                ORDER BY file.id;
            """).splitlines()]
            self.assertEqual(2, len(files), "Desktop e Android devem confirmar cada jornada F08.")
            for file in files:
                rows = self.wait_for(lambda: self.variant_rows(file), "A foto do navegador não ficou disponível.")
                self.assertEqual(["image", "thumbnail"], [row[0] for row in rows])
                self.assertEqual(1, len(self.file_facts(file)))

    def test_media_processing(self):
        self.configure_processing()
        with self.subTest(criterio="CA1, CA2 e CA3"):
            self.verify_receipts_and_formats()
        with self.subTest(criterio="CA4, queda entre S3 e commit"):
            self.verify_restart_and_replay()
        with self.subTest(criterio="CA4, falha definitiva para F13"):
            self.verify_permanent_failure_preserves_receipt()
        if self.include_browser:
            with self.subTest(criterio="T09, Chromium"):
                self.verify_browser_receipts_and_readiness()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida preparação local de fotos em PostgreSQL, SNS/SQS e S3.")
    parser.add_argument("--browser", action="store_true", help="Inclui jornadas da F08 em Chromium desktop e Android emulado.")
    MediaProcessingStackTests.include_browser = parser.parse_args().browser
    suite = unittest.TestSuite([MediaProcessingStackTests("test_media_processing")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

import argparse
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as civil_time, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from docker_stack import INFRASTRUCTURE, ROOT
from photo_stack import photo_fixture
from submission_stack import NAME, NOTICE, SubmissionStackTests


CAPTION = "Legenda sintética de confirmação de foto"
PHOTO_FACT = "record.media-confirmed.v1"
FIRST_FACT = "event.first-record-confirmed.v1"
AWS_FIXTURE_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_DEFAULT_REGION")


def utc_timestamp(instant):
    return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PhotoSubmissionStackTests(SubmissionStackTests):
    include_browser = False
    include_load = False

    def set_photo_clock(self):
        brasilia = ZoneInfo("America/Sao_Paulo")
        epoch = self.stack.compose_command("exec", "-T", "service", "date", "-u", "+%s").stdout.strip()
        self.admission = datetime.fromtimestamp(int(epoch), timezone.utc)
        self.event_start = self.admission.astimezone(brasilia).replace(tzinfo=None)
        civil_deadline = datetime.combine(self.event_start.date() + timedelta(days=1), civil_time(13), brasilia)
        self.deadline = civil_deadline.astimezone(timezone.utc)
        self.close = self.deadline - timedelta(hours=1)
        self.after_deadline = self.deadline + timedelta(minutes=1)
        self.after_reconciliation = self.after_deadline + timedelta(days=2)
        self.received_at = civil_deadline.strftime("%Y-%m-%d %H:%M:%S")
        self.set_time(self.admission.isoformat())

    def create_photo_event(self, name):
        status, _, event = self.request(draft={"name": name, "startsAt": self.event_start.isoformat(),
            "openingPolicy": {"mode": "immediate", "opensOn": None}})
        self.assertEqual(201, status)
        event_id = int(event["id"])
        code = self.stack.app_sql(f"SELECT code FROM scrapbook.events WHERE id={event_id};")
        self.assertRegex(code, r"^[A-Za-z0-9_-]{43}$")
        return event_id, code

    def configure_photos(self, *, enable_video=False, enable_audio=False):
        self.configure_submissions()
        self.stack.compose_command("up", "-d", "--wait", "postgres")
        fixture = subprocess.run([
            sys.executable, str(INFRASTRUCTURE / "scripts" / "photo_media_fixture.py"), "start",
            "--ui-origin", self.origin, "--network", self.stack.project + "_default", "--manual-events",
            *(("--video",) if enable_video else ()),
        ], env=self.stack.environment, text=True, capture_output=True, timeout=120)
        self.assertEqual(0, fixture.returncode, fixture.stderr)
        self.storage = json.loads(fixture.stdout)
        self.addCleanup(self.stop_photo_fixture, self.storage["container"])
        endpoint = urlsplit(self.storage["endpoint"])
        self.assertEqual(("http", "127.0.0.1"), (endpoint.scheme, endpoint.hostname))
        self.assertIsNotNone(endpoint.port)
        self.assertEqual("", endpoint.path)
        override = self.stack.env_file.parent / "photo-submissions.compose.json"
        override.write_text(json.dumps({"services": {
            "service": {"environment": self.storage["environment"] | {
                "PhotoSubmissions__AudioEnabled": str(enable_audio).lower(),
            }},
            "ui": {"build": {"args": {"VITE_SUBMISSION_UPLOAD_ORIGIN": self.storage["endpoint"]}}},
        }}), encoding="utf-8")
        override.chmod(0o600)
        self.stack.compose.extend(("--file", str(override)))

    def stop_photo_fixture(self, name):
        result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=35)
        self.assertEqual(0, result.returncode)

    def aws(self, operation, *arguments):
        credentials = {key: value for key, value in self.storage["environment"].items() if key in AWS_FIXTURE_KEYS}
        region = self.storage["environment"]["PhotoSubmissions__Region"]
        command = ["aws", "--endpoint-url", self.storage["endpoint"], "--region", region, "--output", "json",
            "--no-cli-pager", *operation, *arguments]
        environment = credentials | {"PATH": os.environ["PATH"], "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull}
        result = subprocess.run(command, env=environment,
            text=True, capture_output=True, timeout=15)
        if result.returncode:
            self.fail(f"Operação {operation[0]} {operation[1]} falhou na fixture de mídia, código {result.returncode}.")
        return json.loads(result.stdout or "{}")

    def photo_draft(self, code, photo, media_type):
        identification = self.draft(code)["identification"]
        return {"category": "testimonial", "format": "photo", "identification": identification, "caption": CAPTION,
            "files": [{"fileKey": str(uuid4()), "position": 1, "declaredMediaType": media_type,
                "sizeBytes": len(photo), "sha256": b64encode(sha256(photo).digest()).decode("ascii")}]}

    def admit(self, code, draft):
        attempt = str(uuid4())
        bearer = "Bearer " + secrets.token_hex(32)
        path = self.path(code, "submissions/" + attempt)
        status, headers, data = self.post(code, draft, attempt, bearer)
        self.assertEqual((201, "receiving"), (status, data.get("state")))
        self.assertTrue(headers["Location"].endswith("/" + attempt))
        self.assertEqual("no-store", headers["Cache-Control"])
        retry, _, replay = self.post(code, draft, attempt, bearer)
        self.assertEqual((200, data), (retry, replay))
        return path, bearer

    def authorize_upload(self, path, bearer, file_key):
        return self.request(path + "/files/" + file_key + "/upload-authorizations", anonymous=True, draft={},
            headers={"Authorization": bearer})

    def upload(self, body, authorization):
        status, headers, data = authorization
        self.assertEqual(200, status)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("PUT", data["method"])
        url = urlsplit(data["url"])
        self.assertEqual(self.storage["endpoint"], f"{url.scheme}://{url.netloc}")
        bucket, key = unquote(url.path).lstrip("/").split("/", 1)
        self.assertEqual(self.storage["bucket"], bucket)
        self.assertTrue(key.startswith("submissions/temporary/"))
        signature = {name.lower(): value for name, value in parse_qs(url.query).items()}
        self.assertIn(int(signature["x-amz-expires"][0]), range(1, 601))
        self.assertTrue({"content-length", "content-type", "x-amz-checksum-sha256"} <=
            set(signature["x-amz-signedheaders"][0].split(";")))
        try:
            with urlopen(Request(data["url"], data=body, headers=data["headers"], method="PUT"), timeout=12) as response:
                self.assertEqual(200, response.status)
                version = response.headers.get("x-amz-version-id")
        except HTTPError as error:
            self.fail(f"O PUT assinado retornou HTTP {error.code} na fixture de mídia.")
        except (URLError, TimeoutError):
            self.fail("O PUT assinado falhou na fixture de mídia.")
        self.assertTrue(version and version != "null")
        return key, version

    def notify_uploaded(self, key, version, event_time, size, *, operation="Put"):
        bucket = self.storage["bucket"]
        envelope = {"Records": [{"eventVersion": "2.1", "eventSource": "aws:s3",
            "awsRegion": self.storage["environment"]["PhotoSubmissions__Region"], "eventTime": utc_timestamp(event_time),
            "eventName": "ObjectCreated:" + operation, "s3": {"s3SchemaVersion": "1.0", "configurationId": "photo-fixture",
                "bucket": {"name": bucket, "arn": "arn:aws:s3:::" + bucket}, "object": {
                    "key": quote_plus(key), "versionId": version, "size": size, "sequencer": "0000000000000001"}}}]}
        self.aws(("sqs", "send-message"), "--queue-url", self.storage["queueUrl"], "--message-body", json.dumps(envelope))

    def object_versions(self, key):
        result = self.aws(("s3api", "list-object-versions"), "--bucket", self.storage["bucket"], "--prefix", key)
        return {item["VersionId"] for item in result.get("Versions", []) if item["Key"] == key}

    def wait_for_evidence(self, key, version):
        self.assertIsNotNone(re.fullmatch(r"submissions/temporary/[0-9a-f]{32}", key))
        version = str(UUID(version))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            found = self.stack.app_sql(f"""
                SELECT count(*) FROM scrapbook.photo_upload_receipts AS receipt
                JOIN scrapbook.storage_objects AS object ON object.id=receipt.storage_object_id
                WHERE object.object_key='{key}' AND receipt.version_id='{version}';
            """)
            if found == "1":
                return
            time.sleep(0.4)
        self.fail("A prova de recepção esperada não foi registrada.")

    def get_status(self, path, bearer):
        return self.request(path, anonymous=True, headers={"Authorization": bearer})

    def wait_for_status(self, path, bearer, wanted):
        deadline = time.monotonic() + 25
        data = {}
        while time.monotonic() < deadline:
            status, _, data = self.get_status(path, bearer)
            self.assertEqual(200, status)
            if data["state"] == wanted:
                return data
            if data["state"] in ("confirmed", "rejected", "expired"):
                break
            time.sleep(0.35)
        self.fail(f"O estado esperado era {wanted}. O último estado foi {data.get('state')} e código {data.get('code')}.")

    def verify_photo_confirmation(self, event_id, code, other, photo, media_type):
        self.set_time(self.admission.isoformat())
        draft = self.photo_draft(code, photo, media_type)
        path, bearer = self.admit(code, draft)
        format_id = draft["files"][0]["fileKey"]
        wrong_secret = "Bearer " + secrets.token_hex(32)
        for inaccessible, secret in ((path, wrong_secret), (path.replace(code, other, 1), bearer)):
            self.assertEqual(404, self.get_status(inaccessible, secret)[0])
            self.assertEqual(404, self.authorize_upload(inaccessible, secret, format_id)[0])
        self.assertEqual(404, self.authorize_upload(path, bearer, str(uuid4()))[0])
        with ThreadPoolExecutor(max_workers=2) as senders:
            versions = list(senders.map(lambda _: self.upload(photo, self.authorize_upload(path, bearer, format_id)), range(2)))
        first, next_version = versions
        self.assertEqual(first[0], next_version[0])
        self.assertNotEqual(first[1], next_version[1])
        self.set_time(self.after_deadline.isoformat())
        denied, _, details = self.authorize_upload(path, bearer, format_id)
        self.assertEqual((409, "UPLOAD_COMPLETION_DEADLINE_EXCEEDED"), (denied, details["code"]))
        start = time.monotonic()
        self.notify_uploaded(*first, self.deadline, len(photo))
        receipt = self.wait_for_status(path, bearer, "confirmed")
        print(f"Foto PNG de {len(photo)} bytes, evidência até recibo observado: {(time.monotonic() - start) * 1000:.0f} ms.", flush=True)
        self.assertEqual({"state", "confirmedAt"}, set(receipt))
        self.notify_uploaded(*first, self.deadline, len(photo))
        self.notify_uploaded(*next_version, self.after_deadline, len(photo))
        self.wait_for_evidence(*next_version)
        self.assertEqual(receipt, self.get_status(path, bearer)[2])
        state = self.stack.app_sql(f"""
            SELECT record.received_at, file.actual_size, file.validation_state, temporary.version_id,
                   original.object_key, original.version_id, encode(file.sha256,'hex')
            FROM scrapbook.records AS record JOIN scrapbook.files AS file ON file.files_records_id=record.id
            JOIN scrapbook.storage_objects AS temporary ON temporary.storage_objects_files_id=file.id AND temporary.kind='temporary'
            JOIN scrapbook.storage_objects AS original ON original.storage_objects_files_id=file.id AND original.kind='original'
            WHERE record.records_events_id={event_id} AND record.state='confirmed' AND record.caption='{CAPTION}';
        """).split("|")
        self.assertEqual([self.received_at, str(len(photo)), "valid", first[1]], state[:4])
        self.assertTrue(state[4].startswith("submissions/original/"))
        original_path = self.stack.env_file.parent / "confirmed-original"
        self.aws(("s3api", "get-object"), "--bucket", self.storage["bucket"], "--key", state[4],
            "--version-id", state[5], str(original_path))
        self.assertEqual(sha256(photo).hexdigest(), state[6])
        self.assertTrue(original_path.read_bytes() == photo, "Os bytes do original diferem da versão confirmada.")
        self.assertEqual("1|1|1", self.stack.app_sql(f"""
            SELECT (SELECT count(*) FROM scrapbook.records WHERE records_events_id={event_id}),
                   (SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={event_id} AND type='{FIRST_FACT}'),
                   (SELECT count(*) FROM scrapbook.outbox_messages WHERE type='{PHOTO_FACT}');
        """))
        self.assertEqual(2, len(self.object_versions(first[0])), "O saneamento precisa respeitar a elegibilidade.")
        return first[0], state[4], state[5], (bearer.split()[1], format_id, first[0], state[4],
            draft["identification"]["consentProof"])

    def verify_formats_and_recovery(self, event_id, code):
        jpeg = (ROOT / "photos" / "fixtures" / "palette.jpg").read_bytes()
        heic = (ROOT / "photos" / "fixtures" / "palette.heic").read_bytes()
        self.set_time(self.admission.isoformat())
        for media_type, photo in (("image/jpeg", jpeg), ("image/heic", heic)):
            draft = self.photo_draft(code, photo, media_type)
            path, bearer = self.admit(code, draft)
            file_key = draft["files"][0]["fileKey"]
            key, version = self.upload(photo, self.authorize_upload(path, bearer, file_key))
            original_name = self.stack.app_sql(f"""
                SELECT object.object_key FROM scrapbook.storage_objects AS object
                JOIN scrapbook.files AS file ON file.id=object.storage_objects_files_id
                WHERE file.client_key='{file_key}' AND object.kind='original';
            """)
            self.assertTrue(original_name.startswith("submissions/original/"))
            if media_type == "image/heic":
                self.notify_uploaded(key, version, self.admission, len(photo))
                self.wait_for_status(path, bearer, "confirmed")
                continue
            self.admin_sql(f"""
                ALTER TABLE scrapbook.outbox_messages ADD CONSTRAINT ck_fixture_photo_commit_failure
                CHECK (type <> '{PHOTO_FACT}') NOT VALID;
            """)
            try:
                self.notify_uploaded(key, version, self.admission, len(photo))
                stop_at = time.monotonic() + 25
                versions = set()
                while time.monotonic() < stop_at and not versions:
                    versions = self.object_versions(original_name)
                self.assertEqual(1, len(versions), "A cópia precisa ocorrer antes da falha de commit.")
                expected = 'violates check constraint "ck_fixture_photo_commit_failure"'
                while expected not in self.stack.logs("postgres"):
                    if time.monotonic() >= stop_at:
                        self.fail("O PostgreSQL não registrou a falha de commit injetada.")
                    time.sleep(0.4)
                self.assertNotEqual("confirmed", self.get_status(path, bearer)[2]["state"])
                self.assertEqual("0", self.stack.app_sql(f"""
                    SELECT count(*) FROM scrapbook.records WHERE records_events_id={event_id} AND confirmed_at IS NOT NULL;
                """))
            finally:
                self.admin_sql("ALTER TABLE scrapbook.outbox_messages DROP CONSTRAINT ck_fixture_photo_commit_failure;")
            self.set_time((self.admission + timedelta(minutes=6)).isoformat())
            self.notify_uploaded(key, version, self.admission, len(photo))
            self.wait_for_status(path, bearer, "confirmed")
            self.assertTrue(versions == self.object_versions(original_name), "A recuperação deve reaproveitar a cópia fixada.")
            self.set_time(self.admission.isoformat())

    def verify_cleanup(self, temporary, original, original_version):
        versions = self.object_versions(temporary)
        self.set_time(self.after_reconciliation.isoformat())
        stop_at = time.monotonic() + 30
        while time.monotonic() < stop_at and versions:
            versions = self.object_versions(temporary)
        self.assertEqual(0, len(versions), "O saneamento deve remover as versões temporárias elegíveis.")
        self.assertTrue({original_version} == self.object_versions(original), "O original deve sobreviver ao saneamento.")

    def verify_invalid_and_late(self, code, photo):
        self.set_time(self.admission.isoformat())
        draft = self.photo_draft(code, photo, "image/png")
        for invalid in (draft | {"files": []}, draft | {"caption": "🎉" * 501},
                draft | {"files": [draft["files"][0] | {"sizeBytes": 20_000_001}]}):
            status, _, details = self.post(code, invalid, str(uuid4()), "Bearer " + secrets.token_hex(32))
            self.assertEqual((400, "REQUEST_VALIDATION_FAILED"), (status, details.get("code")))

        invalid_bytes = b"conteudo sintetico sem imagem"
        invalid = self.photo_draft(code, invalid_bytes, "image/png")
        path, bearer = self.admit(code, invalid)
        key, version = self.upload(invalid_bytes, self.authorize_upload(path, bearer, invalid["files"][0]["fileKey"]))
        self.notify_uploaded(key, version, self.admission, len(invalid_bytes))
        self.wait_for_status(path, bearer, "rejected")

        late_attempt, late_bearer = self.admit(code, draft)
        late_key, late_version = self.upload(photo, self.authorize_upload(late_attempt, late_bearer, draft["files"][0]["fileKey"]))
        self.set_time(self.close.isoformat())
        status, _, details = self.post(code, draft, str(uuid4()), "Bearer " + secrets.token_hex(32))
        self.assertEqual((409, "UPLOAD_WINDOW_CLOSED"), (status, details["code"]))
        self.set_time(self.after_deadline.isoformat())
        self.notify_uploaded(late_key, late_version, self.deadline + timedelta(milliseconds=1), len(photo))
        self.wait_for_evidence(late_key, late_version)
        self.set_time(self.after_reconciliation.isoformat())
        self.wait_for_status(late_attempt, late_bearer, "expired")

    def run_photo_browser(self, first, second, representative):
        fixture = self.stack.env_file.parent / "photo-fixture.json"
        fixture.write_text(json.dumps(self.storage), encoding="utf-8")
        fixture.chmod(0o600)
        environment = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first, "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file), "SCRAPBOOK_TEST_PHOTO_FIXTURE": str(fixture),
            "SCRAPBOOK_TEST_PHOTO_PERFORMANCE_FILE": str(representative),
            "SCRAPBOOK_TEST_PHOTO_OPEN": utc_timestamp(self.admission), "SCRAPBOOK_TEST_PHOTO_CLOSE": utc_timestamp(self.close),
            "SCRAPBOOK_TEST_PHOTO_DEADLINE": utc_timestamp(self.deadline)}
        environment.pop("NO_COLOR", None)
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.photo-submission.config.ts"],
            cwd=ROOT, env=environment, check=True, timeout=210)

    def run_photo_load(self, first, second, representative):
        self.set_time(self.admission.isoformat())
        before = int(self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        environment = self.stack.environment | {"SCRAPBOOK_LOAD_ORIGIN": self.origin,
            "SCRAPBOOK_LOAD_STORAGE_ORIGIN": self.storage["endpoint"], "SCRAPBOOK_LOAD_PUBLIC_CODE_A": first,
            "SCRAPBOOK_LOAD_PUBLIC_CODE_B": second, "SCRAPBOOK_LOAD_NOTICE_CODE": NOTICE,
            "SCRAPBOOK_LOAD_PHOTO": str(representative), "K6_NO_USAGE_REPORT": "true"}
        subprocess.run(["k6", "run", "--quiet", "--no-color", "load/photo-submission.js"], cwd=ROOT, env=environment,
            timeout=70, check=True)
        self.assertEqual(str(before + 200), self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))

    def test_photo_submission(self):
        self.configure_photos()
        self.prepare()
        self.set_photo_clock()
        event, first = self.create_photo_event("Depoimento sintético com foto A")
        second_event, second = self.create_photo_event("Depoimento sintético com foto B")
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")
        representative = self.stack.env_file.parent / "photo-sample.png"
        photo_fixture(representative)
        sample = representative.read_bytes()
        temporary, original, version, markers = self.verify_photo_confirmation(event, first, second, sample, "image/png")
        self.verify_formats_and_recovery(second_event, second)
        self.verify_invalid_and_late(second, sample)
        self.verify_cleanup(temporary, original, version)
        if self.include_browser:
            self.run_photo_browser(first, second, representative)
        if self.include_load:
            self.run_photo_load(first, second, representative)
        logs = self.stack.logs("service") + self.stack.logs("ui")
        if any(marker in logs for marker in (first, second, NAME, CAPTION, *markers, "X-Amz-Signature")):
            self.fail("A telemetria expôs um valor privado da fixture.")
        print("Manifesto, versões S3, prazo, isolamento, recibo e Outbox conferidos.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    parser = argparse.ArgumentParser(description="Valida o depoimento com foto em uma stack local descartável.")
    parser.add_argument("--browser", action="store_true", help="Acrescenta as jornadas Playwright.")
    parser.add_argument("--load", action="store_true", help="Acrescenta o ensaio de 200 envios no k6.")
    options = parser.parse_args()
    PhotoSubmissionStackTests.include_browser = options.browser
    PhotoSubmissionStackTests.include_load = options.load
    suite = unittest.TestSuite([PhotoSubmissionStackTests("test_photo_submission")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

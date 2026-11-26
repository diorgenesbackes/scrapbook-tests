import argparse
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from hashlib import file_digest, sha256
import json
import secrets
from struct import pack
import subprocess
import time
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4
import unittest

from docker_stack import ROOT
from photo_submission_stack import PHOTO_FACT, PhotoSubmissionStackTests, utc_timestamp
from submission_stack import NOTICE


PART_BYTES = 16 * 1024 * 1024
MULTIPART_MIN_BYTES = 2 * PART_BYTES
MAX_BYTES = 500_000_000
SYNTHETIC_CAPTION = "Envio sintético de vídeo"


def digest(body):
    return b64encode(sha256(body).digest()).decode("ascii")


class VideoSubmissionStackTests(PhotoSubmissionStackTests):
    include_video_browser = False

    def video_fixture(self, extension, duration, *, padded_size=None, audio=False, rotation=None):
        name = f"f084-{duration}.{extension}"
        output = f"/tmp/{name}"
        codec = "libvpx-vp9" if extension == "webm" else "libx264"
        audio_input = ("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000") if audio else ()
        audio_codec = ("-c:a", "libopus" if extension == "webm" else "aac") if audio else ("-an",)
        self.stack.compose_command(
            "exec", "-T", "service", "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=c=blue:size=160x90:rate=5", *audio_input, "-t", str(duration),
            "-threads", "1", "-c:v", codec, *audio_codec, "-pix_fmt", "yuv420p", output,
        )
        if rotation is not None:
            rotated = f"/tmp/rotated-{name}"
            self.stack.compose_command("exec", "-T", "service", "ffmpeg", "-v", "error", "-y",
                "-display_rotation", str(rotation), "-i", output, "-c", "copy", rotated)
            output = rotated
        saved = self.stack.env_file.parent / name
        with saved.open("wb") as video:
            subprocess.run([*self.stack.compose, "exec", "-T", "service", "cat", output],
                env=self.stack.environment, stdout=video, stderr=subprocess.PIPE, check=True, timeout=15)
        if padded_size is not None:
            padding = padded_size - saved.stat().st_size
            if extension != "mp4" or padding < 8:
                raise ValueError("A fixture MP4 precisa de espaço para uma caixa free.")
            with saved.open("r+b") as file:
                file.seek(0, 2)
                file.write(pack(">I4s", padding, b"free"))
                file.truncate(padded_size)
        return saved

    def video_draft(self, code, sample, category):
        with sample.open("rb") as file:
            full_hash = sha256()
            hashes = []
            while part := file.read(PART_BYTES):
                full_hash.update(part)
                hashes.append(digest(part))
        media_type = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}
        size = sample.stat().st_size
        manifest = {"fileKey": str(uuid4()), "position": 1, "declaredMediaType": media_type[sample.suffix[1:]],
            "sizeBytes": size, "sha256": b64encode(full_hash.digest()).decode("ascii")}
        if size >= MULTIPART_MIN_BYTES:
            manifest["partSha256"] = hashes
        return {"category": category, "format": "video", "identification": self.draft(code)["identification"],
            "caption": SYNTHETIC_CAPTION, "files": [manifest]}

    def upload_status(self, path, bearer, file_key):
        return self.request(f"{path}/files/{file_key}/upload-status", anonymous=True, headers={"Authorization": bearer})

    def authorize_part(self, path, bearer, file_key, number):
        return self.request(f"{path}/files/{file_key}/upload-authorizations", anonymous=True,
            draft={"partNumber": number}, headers={"Authorization": bearer})

    def upload_part(self, body, number, grant):
        status, headers, data = grant
        self.assertEqual((200, "no-store"), (status, headers["Cache-Control"]))
        self.assertEqual({"x-amz-checksum-sha256": digest(body)},
            {key.lower(): value for key, value in data["headers"].items()})
        url = urlsplit(data["url"])
        self.assertEqual(self.storage["endpoint"], f"{url.scheme}://{url.netloc}")
        bucket, key = unquote(url.path).lstrip("/").split("/", 1)
        self.assertEqual(self.storage["bucket"], bucket)
        self.assertRegex(key, r"^submissions/temporary/[0-9a-f]{32}$")
        query = {name.lower(): value for name, value in parse_qs(url.query).items()}
        self.assertEqual(str(number), query["partnumber"][0])
        self.assertTrue({"content-length", "x-amz-checksum-sha256"} <= set(query["x-amz-signedheaders"][0].split(";")))
        signature_at = datetime.strptime(query["x-amz-date"][0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        lifetime = timedelta(seconds=int(query["x-amz-expires"][0]))
        valid_until = datetime.fromisoformat(data["expiresAt"])
        fixture_now = datetime.fromisoformat(self.clock_file.read_text())
        self.assertLessEqual(valid_until, fixture_now + timedelta(minutes=10))
        self.assertGreater(valid_until, fixture_now)
        self.assertLessEqual(abs(valid_until - signature_at - lifetime), timedelta(seconds=1))
        headers = data["headers"] | {"Content-Type": "application/octet-stream"}
        with urlopen(Request(data["url"], data=body, headers=headers, method="PUT"), timeout=35) as response:
            self.assertEqual(200, response.status)
        return key

    def complete_video(self, path, bearer):
        return self.request(path + "/completion", anonymous=True, draft={}, headers={"Authorization": bearer})

    def verify_single_video(self, code, sample, category, expected_state, *, rotation=None):
        self.set_time(self.admission.isoformat())
        draft = self.video_draft(code, sample, category)
        path, bearer = self.admit(code, draft)
        file_key = draft["files"][0]["fileKey"]
        response, _, info = self.upload_status(path, bearer, file_key)
        self.assertEqual((200, "put", []), (response, info["mode"], info["uploadedParts"]))
        body = sample.read_bytes()
        key, version = self.upload(body, self.authorize_upload(path, bearer, file_key))
        self.notify_uploaded(key, version, self.admission, len(body))
        receipt = self.wait_for_status(path, bearer, expected_state)
        if expected_state == "confirmed":
            self.assertEqual({"state", "confirmedAt"}, set(receipt))
        if rotation is not None:
            actual_rotation = self.stack.app_sql(f"SELECT rotation FROM scrapbook.files WHERE client_key = '{file_key}';")
            self.assertEqual(str(rotation), actual_rotation)

    def verify_multipart_video(self, code, other, sample, *, late):
        self.set_time(self.admission.isoformat())
        draft = self.video_draft(code, sample, "testimonial")
        path, bearer = self.admit(code, draft)
        file_key = draft["files"][0]["fileKey"]
        self.assertEqual(404, self.upload_status(path, "Bearer " + secrets.token_hex(32), file_key)[0])
        self.assertEqual(404, self.authorize_part(path.replace(code, other, 1), bearer, file_key, 1)[0])
        response, _, info = self.upload_status(path, bearer, file_key)
        self.assertEqual((200, "multipart", PART_BYTES, []),
            (response, info["mode"], info["partSizeBytes"], info["uploadedParts"]))
        with sample.open("rb") as stream:
            part_one = stream.read(PART_BYTES)
            key = self.upload_part(part_one, 1, self.authorize_part(path, bearer, file_key, 1))
            self.assertEqual(set(), self.object_versions(key))
            self.set_time((self.admission + timedelta(minutes=11)).isoformat())
            response, _, info = self.upload_status(path, bearer, file_key)
            self.assertEqual((200, [1]), (response, info["uploadedParts"]))
            self.assertEqual(409, self.authorize_part(path, bearer, file_key, 1)[0])
            self.assertNotEqual("confirmed", self.complete_video(path, bearer)[2]["state"])
            number = 2
            while part := stream.read(PART_BYTES):
                self.assertEqual(key, self.upload_part(part, number, self.authorize_part(path, bearer, file_key, number)))
                number += 1
        self.assertEqual(list(range(1, number)), self.upload_status(path, bearer, file_key)[2]["uploadedParts"])
        self.assertEqual(set(), self.object_versions(key))
        self.assertEqual(202, self.complete_video(path, bearer)[0])
        versions = self.object_versions(key)
        self.assertEqual(1, len(versions))
        version, = versions
        self.assertEqual(202, self.complete_video(path, bearer)[0])
        self.assertEqual(versions, self.object_versions(key))
        self.assertNotEqual("confirmed", self.get_status(path, bearer)[2]["state"])

        self.set_time(self.after_deadline.isoformat())
        received = self.deadline + timedelta(milliseconds=1) if late else self.deadline
        if late:
            self.notify_uploaded(key, version, received, sample.stat().st_size, operation="CompleteMultipartUpload")
            self.wait_for_evidence(key, version)
            self.set_time(self.after_reconciliation.isoformat())
            self.wait_for_status(path, bearer, "rejected")
            return

        copied = self.verify_copy_before_commit(path, bearer, file_key, key, version, received, sample.stat().st_size)
        try:
            receipt = self.wait_for_status(path, bearer, "confirmed")
        except AssertionError as failure:
            diagnostic = self.stack.app_sql(f"""
                SELECT session.state, coalesce(session.last_error_code, 'sem-código'), session.attempt_count,
                    temporary.state, original.state
                FROM scrapbook.files AS file
                JOIN scrapbook.upload_sessions AS session ON session.upload_sessions_records_id = file.files_records_id
                JOIN scrapbook.storage_objects AS temporary ON temporary.storage_objects_files_id = file.id
                    AND temporary.kind = 'temporary'
                JOIN scrapbook.storage_objects AS original ON original.storage_objects_files_id = file.id
                    AND original.kind = 'original'
                WHERE file.client_key = '{file_key}';
            """)
            self.fail(f"{failure} Estado do worker, código, tentativas e objetos: {diagnostic}")
        self.assertEqual({"state", "confirmedAt"}, set(receipt))
        status, _, replay = self.post(code, draft, path.rsplit("/", 1)[1], bearer)
        self.assertEqual((200, receipt), (status, replay))
        data = self.stack.app_sql(f"""
            SELECT temporary.version_id, temporary.receipt_event_kind,
                encode(file.sha256, 'hex'), original.object_key, original.version_id,
                (SELECT count(*) FROM scrapbook.outbox_messages AS fact
                 WHERE fact.payload->>'fileId' = file.id::text AND fact.type = '{PHOTO_FACT}')
            FROM scrapbook.files AS file
            JOIN scrapbook.storage_objects AS temporary ON temporary.storage_objects_files_id = file.id
                AND temporary.kind = 'temporary'
            JOIN scrapbook.storage_objects AS original ON original.storage_objects_files_id = file.id
                AND original.kind = 'original'
            WHERE file.client_key = '{file_key}' AND file.validation_state = 'valid';
        """).split("|")
        self.assertEqual([version, "complete_multipart"], data[:2])
        with sample.open("rb") as stream:
            self.assertEqual(file_digest(stream, "sha256").hexdigest(), data[2])
        original_key, original_version = data[3:5]
        self.assertRegex(original_key, r"^submissions/original/[0-9a-f]{32}$")
        self.assertEqual({original_version}, copied)
        self.assertEqual(copied, self.object_versions(original_key))
        self.assertEqual("1", data[5])
        self.verify_cleanup(key, original_key, original_version)

    def verify_copy_before_commit(self, path, bearer, file_key, key, version, received, size):
        original = self.stack.app_sql(f"""
            SELECT original.object_key FROM scrapbook.storage_objects AS original
            JOIN scrapbook.files AS file ON file.id = original.storage_objects_files_id
            WHERE file.client_key = '{file_key}' AND original.kind = 'original';
        """)
        self.assertRegex(original, r"^submissions/original/[0-9a-f]{32}$")
        self.admin_sql(f"""
            ALTER TABLE scrapbook.outbox_messages ADD CONSTRAINT ck_fixture_video_commit_failure
            CHECK (type <> '{PHOTO_FACT}') NOT VALID;
        """)
        try:
            self.notify_uploaded(key, version, received, size, operation="CompleteMultipartUpload")
            until = time.monotonic() + 30
            copied = set()
            while not copied and time.monotonic() < until:
                copied = self.object_versions(original)
                if not copied:
                    time.sleep(0.2)
            self.assertEqual(1, len(copied))
            marker = 'violates check constraint "ck_fixture_video_commit_failure"'
            while marker not in self.stack.logs("postgres"):
                if time.monotonic() >= until:
                    self.fail("A falha de commit do cenário não foi observada.")
                time.sleep(0.3)
            self.assertEqual("validating", self.get_status(path, bearer)[2]["state"])
        finally:
            self.admin_sql("ALTER TABLE scrapbook.outbox_messages DROP CONSTRAINT ck_fixture_video_commit_failure;")
        self.set_time((self.after_deadline + timedelta(minutes=6)).isoformat())
        self.notify_uploaded(key, version, received, size, operation="CompleteMultipartUpload")
        return copied

    def verify_multipart_abort(self, code, sample):
        self.set_time(self.admission.isoformat())
        draft = self.video_draft(code, sample, "moment")
        path, bearer = self.admit(code, draft)
        file_key = draft["files"][0]["fileKey"]
        with sample.open("rb") as source:
            key = self.upload_part(source.read(PART_BYTES), 1, self.authorize_part(path, bearer, file_key, 1))

        def open_uploads():
            reply = self.aws(("s3api", "list-multipart-uploads"), "--bucket", self.storage["bucket"], "--prefix", key)
            return [item for item in reply.get("Uploads", []) if item["Key"] == key]

        self.assertEqual(1, len(open_uploads()))
        self.set_time(self.after_reconciliation.isoformat())
        self.wait_for_status(path, bearer, "expired")
        self.assertEqual([], open_uploads())
        self.assertEqual(set(), self.object_versions(key))
        self.assertEqual(set(), self.object_versions(key.replace("temporary", "original", 1)))

    def test_video_submission(self):
        self.configure_photos(enable_video=True)
        self.prepare()
        self.set_photo_clock()
        _, first = self.create_photo_event("Vídeo sintético A")
        _, second = self.create_photo_event("Vídeo sintético B")
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")

        moment = self.video_fixture("webm", 60, audio=True)
        testimonial = self.video_fixture("mov", 300, rotation=-90)
        large = self.video_fixture("mp4", 2, padded_size=MULTIPART_MIN_BYTES + 101)
        self.verify_single_video(first, moment, "moment", "confirmed")
        self.verify_single_video(second, testimonial, "testimonial", "confirmed", rotation=270)
        self.verify_single_video(first, testimonial, "moment", "rejected")
        self.verify_single_video(second, self.video_fixture("webm", 301), "testimonial", "rejected")
        self.verify_multipart_video(first, second, large, late=False)
        self.verify_multipart_video(first, second, large, late=True)
        self.verify_multipart_abort(second, large)

        self.set_time(self.admission.isoformat())
        draft = self.video_draft(first, large, "testimonial")
        manifest, = draft["files"]
        too_large = draft | {"files": [manifest | {"sizeBytes": MAX_BYTES + 1}]}
        status, _, details = self.post(first, too_large, str(uuid4()), "Bearer " + secrets.token_hex(32))
        self.assertEqual((400, "REQUEST_VALIDATION_FAILED"), (status, details["code"]))
        if self.include_video_browser:
            self.run_video_browser(first, second, large)

    def run_video_browser(self, first, second, sample):
        fixture = self.stack.env_file.parent / "video-fixture.json"
        fixture.write_text(json.dumps(self.storage), encoding="utf-8")
        fixture.chmod(0o600)
        environment = self.stack.environment | {
            "PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first,
            "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file),
            "SCRAPBOOK_TEST_VIDEO_FIXTURE": str(fixture),
            "SCRAPBOOK_TEST_VIDEO_OPEN": utc_timestamp(self.admission),
            "SCRAPBOOK_TEST_VIDEO_DEADLINE": utc_timestamp(self.deadline),
            "SCRAPBOOK_TEST_VIDEO_MULTIPART_FILE": str(sample),
        }
        environment.pop("NO_COLOR", None)
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.video-submission.config.ts"],
            cwd=ROOT, env=environment, check=True, timeout=450)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida vídeo em PostgreSQL e S3 descartáveis.")
    parser.add_argument("--with-browser", action="store_true")
    VideoSubmissionStackTests.include_video_browser = parser.parse_args().with_browser
    suite = unittest.TestSuite([VideoSubmissionStackTests("test_video_submission")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
import subprocess
import time
import unittest
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from audio_submission_stack import AUDIO_FIXTURES, AUDIO_TYPES, AudioSubmissionStackTests
from docker_stack import ROOT
from media_processing_stack import MediaProcessingStackTests
from photo_submission_stack import utc_timestamp
from submission_stack import NOTICE


IMAGE = "ghcr.io/getmoto/motoserver@sha256:120b1589c6df7e35d2092935f6ff724486bb2c4189d0e36ce795baba1400ffd2"
FAILURE_FACT = "record.media-preparation.failed.v1"


class AudioMediaProcessingStackTests(AudioSubmissionStackTests, MediaProcessingStackTests):
    include_browser = False

    def configure_audio_processing(self):
        self.configure_photos(enable_audio=True)
        self.topic = self.aws(("sns", "create-topic"), "--name", "fixture-f1002")["TopicArn"]
        self.queue = self.subscribe("fixture-f1002-confirmation")
        self.result_queue = self.aws(("sqs", "create-queue"), "--queue-name",
            "fixture-f1002-results", "--attributes", "VisibilityTimeout=5")["QueueUrl"]
        internal = self.storage["environment"]["PhotoSubmissions__QueueServiceUrl"]
        account = self.topic.split(":")[4]
        region = self.storage["environment"]["PhotoSubmissions__Region"]
        self.job_queue = f"arn:aws:mediaconvert:{region}:{account}:queues/fixture-f1002"
        self.job_role = f"arn:aws:iam::{account}:role/fixture-f1002"
        result_url = internal + urlsplit(self.result_queue).path
        provider_url = self.start_provider(internal, result_url)
        override = self.stack.env_file.parent / "audio-media-processing.compose.json"
        override.write_text(json.dumps({"services": {"service": {"environment": {
            "MediaProcessing__Enabled": "true",
            "MediaProcessing__Region": region,
            "MediaProcessing__TopicArn": self.topic,
            "MediaProcessing__QueueUrl": internal + urlsplit(self.queue).path,
            "MediaProcessing__SnsServiceUrl": internal,
            "MediaProcessing__SqsServiceUrl": internal,
            "MediaAudioProcessing__Enabled": "true",
            "MediaAudioProcessing__Region": region,
            "MediaAudioProcessing__QueueArn": self.job_queue,
            "MediaAudioProcessing__JobRoleArn": self.job_role,
            "MediaAudioProcessing__ResultQueueUrl": result_url,
            "MediaAudioProcessing__MediaConvertServiceUrl": provider_url,
            "MediaAudioProcessing__SqsServiceUrl": internal,
        }}}}), encoding="utf-8")
        self.stack.compose.extend(("--file", str(override)))
        self.prepare()
        self.set_photo_clock()
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")

    def start_provider(self, internal, result_url):
        reference = self.stack.env_file.parent / "fixture-reference.m4a"
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i",
            str(AUDIO_FIXTURES / "tone.m4a"), "-map", "0:a:0", "-map_metadata", "-1", "-c:a", "aac",
            "-profile:a", "aac_low", "-ar", "48000", "-ac", "1", "-b:a", "96k", "-movflags", "+faststart", str(reference)],
            capture_output=True, check=True, timeout=15)
        self.reference_sha = sha256(reference.read_bytes()).hexdigest()
        self.provider_container = self.stack.project + "-mediaconvert"
        environment = self.stack.environment | {
            "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_EC2_METADATA_DISABLED": "true",
            "SCRAPBOOK_FIXTURE_ENDPOINT": internal,
            "SCRAPBOOK_FIXTURE_BUCKET": self.storage["bucket"],
            "SCRAPBOOK_FIXTURE_RESULT_QUEUE": result_url,
            "SCRAPBOOK_FIXTURE_JOB_QUEUE": self.job_queue,
            "SCRAPBOOK_FIXTURE_JOB_ROLE": self.job_role,
        }
        variables = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_EC2_METADATA_DISABLED",
            "SCRAPBOOK_FIXTURE_ENDPOINT", "SCRAPBOOK_FIXTURE_BUCKET", "SCRAPBOOK_FIXTURE_RESULT_QUEUE",
            "SCRAPBOOK_FIXTURE_JOB_QUEUE", "SCRAPBOOK_FIXTURE_JOB_ROLE")
        binary = ROOT / "media-processing" / "mediaconvert_fixture.py"
        command = ["docker", "run", "--rm", "--detach", "--name", self.provider_container,
            "--network", self.stack.project + "_default", "--publish", "127.0.0.1::5000",
            "--read-only", "--tmpfs", "/tmp:size=16m,mode=1777", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--entrypoint", "python"]
        for name in variables:
            command.extend(("--env", name))
        for source, destination in ((binary, "server.py"), (reference, "reference.m4a"), (self.clock_file, "clock.txt")):
            command.extend(("--mount", f"type=bind,src={source},dst=/fixture/{destination},readonly"))
        subprocess.run([*command, IMAGE, "/fixture/server.py"], env=environment, text=True,
            capture_output=True, check=True, timeout=40)
        self.addCleanup(self.stop_provider)
        binding = subprocess.check_output(["docker", "port", self.provider_container, "5000/tcp"],
            env=self.stack.environment, text=True, timeout=10).strip()
        self.assertRegex(binding, r"^127\.0\.0\.1:[0-9]+$")
        self.provider_origin = "http://" + binding

        def ready():
            try:
                return self.provider("/fixture/summary") == []
            except OSError:
                return False

        self.wait_for(ready, "O adaptador local do MediaConvert não iniciou.", timeout=15)
        return f"http://{self.provider_container}:5000"

    def stop_provider(self):
        subprocess.run(["docker", "rm", "-f", self.provider_container], env=self.stack.environment,
            capture_output=True, check=True, timeout=20)

    def provider(self, path, body=None):
        request = Request(self.provider_origin + path, None if body is None else json.dumps(body).encode(),
            {"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def await_audio(self, action, message, timeout=45):
        began = time.monotonic()
        clock_start = datetime.fromisoformat(self.clock_file.read_text().strip())
        until = began + timeout
        while time.monotonic() < until:
            self.set_time(utc_timestamp(clock_start + timedelta(seconds=time.monotonic() - began)))
            if result := action():
                return result
            time.sleep(0.25)
        self.fail(message)

    def submit_audio(self, code, extension):
        self.set_time(utc_timestamp(self.admission))
        content = (AUDIO_FIXTURES / f"tone.{extension}").read_bytes()
        draft = self.audio_manifest(code, content, AUDIO_TYPES[extension])
        path, bearer = self.admit(code, draft)
        key, = [value["fileKey"] for value in draft["files"]]
        uploaded, version = self.upload(content, self.authorize_upload(path, bearer, key))
        self.request(path + "/completion", anonymous=True, draft={}, headers={"Authorization": bearer})
        self.notify_uploaded(uploaded, version, self.admission, len(content))
        receipt = self.wait_for_status(path, bearer, "confirmed")
        self.assertEqual({"state", "confirmedAt"}, set(receipt))
        file, = self.file_ids(path)
        return (path, bearer, receipt), draft, file, content

    def audio_row(self, file):
        return self.stack.app_sql(f"""
            SELECT f.processing_state, j.state, a.state, v.state, a.request_key::text,
                coalesce(derived.version_id, ''), coalesce(encode(derived.sha256, 'hex'), ''),
                coalesce(derived.object_key, ''), coalesce(v.media_type, ''), coalesce(v.audio_codec, ''),
                coalesce(v.duration::text, ''), a.provider_submit_started_at IS NOT NULL,
                coalesce(a.provider_job_id, ''), coalesce(encode(staged.sha256, 'hex'), ''),
                coalesce(staged.version_id, '')
            FROM scrapbook.files AS f JOIN scrapbook.processing_jobs AS j ON j.processing_jobs_files_id = f.id
            JOIN scrapbook.processing_attempts AS a ON a.processing_attempts_processing_jobs_id = j.id
            JOIN scrapbook.file_variants AS v ON v.file_variants_files_id = f.id AND v.kind = 'playback'
            LEFT JOIN scrapbook.storage_objects AS derived ON derived.id = v.file_variants_storage_objects_id
            LEFT JOIN scrapbook.storage_objects AS staged ON staged.storage_objects_processing_attempts_id = a.id
                AND staged.object_key LIKE 'submissions/transcode-input/%'
            WHERE f.id = {file} ORDER BY a.number DESC LIMIT 1;
        """).split("|")

    def ready_audio(self, file):
        row = self.audio_row(file)
        return row if row[:4] == ["available", "completed", "succeeded", "ready"] else None

    def assert_audio_available(self, file, *, normalized=False):
        row = self.await_audio(lambda: self.ready_audio(file), "O áudio confirmado não ficou pronto.")
        _, _, _, _, attempt, version, digest, key, media_type, codec, duration, submitted, provider_id, stage_sha, stage_version = row
        self.assertEqual((self.reference_sha, "audio/mp4", "aac", "t"), (digest, media_type, codec, submitted))
        self.assertGreater(float(duration), 2.9)
        self.assertLess(float(duration), 3.1)
        self.assertEqual(f"submissions/transcode-output/{file}/{UUID(attempt).hex}/playback.m4a", key)
        job, = [entry for entry in self.provider("/fixture/summary") if UUID(entry["attempt"]) == UUID(attempt)]
        self.assertEqual((provider_id, version, stage_sha, stage_version),
            (job["provider_job"], job["output_version"], job["input_sha"], job["input_version"]))
        if normalized:
            self.assertIn(job["input_format"], {"ogg", "wav"}, "WebM deve passar pela preparação privada compatível.")
        count = self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.file_variants WHERE file_variants_files_id = {file} AND state = 'ready';
        """)
        self.assertEqual("1", count)
        return row

    def verify_formats(self, code):
        for extension in AUDIO_TYPES:
            with self.subTest(formato=extension):
                submission, draft, file, content = self.submit_audio(code, extension)
                self.assert_audio_available(file, normalized=extension == "webm")
                self.assert_original(file, content)
                self.assert_receipt(code, draft, submission)
                print(f"{extension}: saída M4A/AAC versionada, origem e recibo preservados.", flush=True)

    def verify_waiting_output(self, code):
        self.provider("/fixture/mode", {"mode": "wait"})
        submission, draft, file, content = self.submit_audio(code, "mp3")

        def submitted():
            row = self.audio_row(file)
            return row if row[0] == "processing" and len(row) > 12 and row[12] else None

        row = self.await_audio(submitted, "O job ainda não teve a intenção e o ID persistidos.")
        self.assertEqual(("t", ""), (row[11], row[5]))
        self.assert_receipt(code, draft, submission)
        self.provider("/fixture/complete", {"attempt": UUID(row[4]).hex})
        self.assert_audio_available(file)
        print("Recibo F08 preservado enquanto o provedor prepara o áudio.", flush=True)

    def verify_lost_response(self, code):
        self.provider("/fixture/mode", {"mode": "lost-response"})
        submission, draft, file, content = self.submit_audio(code, "webm")
        row = self.assert_audio_available(file, normalized=True)
        job, = [entry for entry in self.provider("/fixture/summary") if UUID(entry["attempt"]) == UUID(row[4])]
        self.assertGreaterEqual(job["create_calls"], 1)
        self.assertEqual("1", self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.processing_attempts WHERE processing_attempts_files_id = {file};
        """))
        self.assert_original(file, content)
        self.assert_receipt(code, draft, submission)
        print("Resposta de CreateJob perdida, evento antecipado e duplicado convergiram para uma tentativa.", flush=True)

    def verify_corrupt_output(self, code):
        self.provider("/fixture/mode", {"mode": "corrupt-output"})
        submission, draft, file, content = self.submit_audio(code, "wav")

        def failure():
            state = self.audio_row(file)
            return state if state and state[0] == "failed" else None

        state = self.await_audio(failure, "A saída inválida não foi classificada como falha definitiva.")
        self.assertEqual("failed", state[3])
        self.assertEqual("1", self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.outbox_messages AS failure
            JOIN scrapbook.outbox_messages AS proof ON proof.message_id = failure.causation_id
            WHERE failure.type = '{FAILURE_FACT}' AND failure.correlation_id = proof.correlation_id
                AND failure.payload->>'fileId' = '{file}' AND failure.payload->>'stage' = 'verify';
        """))
        self.assert_original(file, content)
        self.assert_receipt(code, draft, submission)
        print("Saída inválida ficou indisponível com F13 correlacionada e recibo intacto.", flush=True)

    def verify_browser_audio(self):
        self.provider("/fixture/mode", {"mode": "complete"})
        event, first = self.create_photo_event("Áudio transcodificado a partir do navegador")
        retry, second = self.create_photo_event("Áudio retomado no navegador")
        self.run_audio_browser(first, second, grep=r"envia tone\.webm|retoma o PUT")
        records = self.stack.app_sql(f"""
            SELECT f.id FROM scrapbook.files AS f JOIN scrapbook.records AS r ON r.id = f.files_records_id
            WHERE r.records_events_id IN ({event}, {retry}) AND r.state = 'confirmed' ORDER BY f.id;
        """).splitlines()
        self.assertEqual(4, len(records))
        for file in map(int, records):
            self.assert_audio_available(file)

    def test_audio_processing(self):
        self.configure_audio_processing()
        _, code = self.create_photo_event("Preparação de áudio confirmada")
        self.verify_formats(code)
        self.verify_waiting_output(code)
        self.verify_lost_response(code)
        self.verify_corrupt_output(code)
        if self.include_browser:
            self.verify_browser_audio()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida F08→F10 com PostgreSQL e contrato local MediaConvert.")
    parser.add_argument("--browser", action="store_true", help="Inclui envio e retomada de áudio no Playwright.")
    AudioMediaProcessingStackTests.include_browser = parser.parse_args().browser
    suite = unittest.TestSuite([AudioMediaProcessingStackTests("test_audio_processing")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

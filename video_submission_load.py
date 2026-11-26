import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Event
import time
import unittest

from submission_stack import NOTICE
from video_submission_stack import PART_BYTES, VideoSubmissionStackTests


SCRIPT = Path(__file__).resolve().parent / "load" / "video-submission.js"


class VideoSubmissionLoadTests(VideoSubmissionStackTests):
    output: Path

    def start_identity(self, suffix, origin, account, authorization):
        pass

    def publish_receipts(self, stopping):
        before = len(self.published)
        while not stopping.is_set():
            listing = self.aws(("s3api", "list-object-versions"), "--bucket", self.storage["bucket"],
                "--prefix", "submissions/temporary/")
            for entry in listing.get("Versions", []):
                key, version = entry["Key"], entry["VersionId"]
                if (key, version) not in self.published:
                    self.notify_uploaded(key, version, datetime.fromisoformat(entry["LastModified"]), entry["Size"],
                        operation="CompleteMultipartUpload")
                    self.published.add((key, version))
            stopping.wait(0.5)
        return len(self.published) - before

    def measure_profile(self, profile, environment):
        summary = self.output / f"{profile}.summary.json"
        with (self.output / f"{profile}.docker.jsonl").open("wb") as samples, \
                (self.output / f"{profile}.k6.log").open("w", encoding="utf-8") as log:
            components = self.stack.compose_command("ps", "-q", "service", "postgres").stdout.splitlines()
            self.assertEqual(2, len(components))
            with subprocess.Popen(["docker", "stats", "--format", "{{json .}}",
                    *components, self.storage["container"]], env=self.stack.environment, stdout=samples) as monitor:
                try:
                    time.sleep(2)
                    result = subprocess.run(["k6", "run", "--quiet", "--log-output", "none", "--include-system-env-vars",
                        "--no-usage-report", "--config", str(self.stack.env_file.parent / "video-k6.json"),
                        "--summary-export", str(summary),
                        "--out", "json=" + str(self.output / f"{profile}.samples.jsonl"), str(SCRIPT)],
                        env=environment | {"SCRAPBOOK_LOAD_VIDEO_PROFILE": profile},
                        stdout=log, stderr=subprocess.STDOUT, timeout=150)
                    time.sleep(2)
                    return result.returncode
                finally:
                    monitor.terminate()
                    monitor.wait(timeout=10)
                    if summary.exists():
                        data = json.loads(summary.read_text(encoding="utf-8"))
                        data.pop("setup_data", None)
                        summary.write_text(json.dumps(data), encoding="utf-8")

    def run_profile(self, profile, environment):
        if profile == "admission":
            return self.measure_profile(profile, environment)

        stopping = Event()
        with ThreadPoolExecutor(max_workers=1) as observer:
            notifications = observer.submit(self.publish_receipts, stopping)
            try:
                result = self.measure_profile(profile, environment)
            finally:
                stopping.set()
            (self.output / f"{profile}.receipts.json").write_text(
                json.dumps({"syntheticReceiptEvents": notifications.result()}), encoding="utf-8")
        return result

    def record_database(self, phase):
        codes = Counter(re.findall(r"\b(?:ERROR|FATAL|PANIC):\s+([0-9A-Z]{5}):", self.stack.logs("postgres")))
        statuses = self.stack.app_sql("""
            SELECT coalesce(json_agg(snapshot), '[]'::json)
            FROM (
                SELECT session.state, session.last_error_code, count(*) AS attempts
                FROM scrapbook.upload_sessions AS session
                JOIN scrapbook.records AS record ON record.id = session.upload_sessions_records_id
                WHERE record.format = 'video'
                GROUP BY session.state, session.last_error_code
                ORDER BY session.state, session.last_error_code
            ) AS snapshot;
        """)
        (self.output / f"{phase}.database.json").write_text(
            json.dumps({"postgresErrorTotals": codes, "sessionStateTotals": json.loads(statuses)}), encoding="utf-8")

    def test_video_load(self):
        self.configure_photos(enable_video=True)
        self.prepare()
        self.admin_sql("ALTER SYSTEM SET log_error_verbosity = 'verbose'; SELECT pg_reload_conf();")
        self.set_photo_clock()
        _, first = self.create_photo_event("Carga sintética de vídeo A")
        _, second = self.create_photo_event("Carga sintética de vídeo B")
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")
        self.published = set()
        sample = self.video_fixture("mp4", 2, padded_size=3 * PART_BYTES)
        manifest, = self.video_draft(first, sample, "moment")["files"]
        del manifest["fileKey"]
        manifest_file = sample.with_suffix(".json")
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        (self.stack.env_file.parent / "video-k6.json").write_text("{}", encoding="utf-8")
        environment = {
            "PATH": os.environ["PATH"],
            "SCRAPBOOK_LOAD_ORIGIN": self.origin,
            "SCRAPBOOK_LOAD_STORAGE_ORIGIN": self.storage["endpoint"],
            "SCRAPBOOK_LOAD_PUBLIC_CODE_A": first,
            "SCRAPBOOK_LOAD_PUBLIC_CODE_B": second,
            "SCRAPBOOK_LOAD_NOTICE_CODE": NOTICE,
            "SCRAPBOOK_LOAD_VIDEO": str(sample),
            "SCRAPBOOK_LOAD_VIDEO_MANIFEST": str(manifest_file),
        }
        warmup = self.run_profile("warmup", environment)
        self.record_database("warmup")
        resume = self.run_profile("resume", environment) if warmup == 0 else None
        self.record_database("before-admission")
        admission = self.run_profile("admission", environment)
        self.record_database("admission")
        self.assertEqual((0, 0, 0), (warmup, resume, admission),
            "Consulte o relatório de cada perfil. A rodada de retomada depende do aquecimento.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mede retomada multipart em dois eventos descartáveis.")
    parser.add_argument("--output", type=Path, required=True, help="Novo diretório para resultados privados do ensaio.")
    VideoSubmissionLoadTests.output = parser.parse_args().output.resolve()
    VideoSubmissionLoadTests.output.mkdir(mode=0o700)
    suite = unittest.TestSuite([VideoSubmissionLoadTests("test_video_load")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

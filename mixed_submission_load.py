import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
from threading import Event
import unittest
from urllib.parse import quote_plus

from docker_stack import ROOT
from photo_stack import photo_fixture
from photo_submission_stack import FIRST_FACT, PHOTO_FACT, PhotoSubmissionStackTests, utc_timestamp
from submission_stack import NOTICE


class MixedSubmissionLoadTests(PhotoSubmissionStackTests):
    output: Path

    def start_identity(self, suffix, origin, account, authorization):
        pass

    def collect_receipts(self, stopping):
        published = set()
        while not stopping.is_set():
            versions = self.aws(("s3api", "list-object-versions"), "--bucket", self.storage["bucket"],
                "--prefix", "submissions/temporary/").get("Versions", [])
            records = [item for item in versions if (item["Key"], item["VersionId"]) not in published]
            for offset in range(0, len(records), 10):
                batch = records[offset:offset + 10]
                self.publish_receipts(batch)
                published.update((item["Key"], item["VersionId"]) for item in batch)
            stopping.wait(0.4)
        return len(published)

    def publish_receipts(self, batch, *, observed_at=None):
        envelope = {"Records": [{
            "eventVersion": "2.1",
            "eventSource": "aws:s3",
            "eventTime": observed_at or utc_timestamp(datetime.fromisoformat(item["LastModified"])),
            "eventName": "ObjectCreated:Put",
            "s3": {
                "bucket": {"name": self.storage["bucket"]},
                "object": {"key": quote_plus(item["Key"]), "versionId": item["VersionId"], "size": item["Size"]},
            },
        } for item in batch]}
        self.aws(("sqs", "send-message"), "--queue-url", self.storage["queueUrl"],
            "--message-body", json.dumps(envelope))

    def run_k6(self, environment):
        config = self.stack.env_file.parent / "mixed-k6.json"
        config.write_text("{}", encoding="utf-8")
        summary = self.output / "summary.json"
        samples = self.output / "samples.jsonl"
        service_ids = self.stack.compose_command("ps", "-q", "service", "postgres").stdout.splitlines()
        self.assertEqual(2, len(service_ids))
        with (self.output / "resources.jsonl").open("w", encoding="utf-8") as resources, \
                (self.output / "k6.log").open("w", encoding="utf-8") as log:
            monitor = subprocess.Popen(["docker", "stats", "--format", "{{json .}}",
                *service_ids, self.storage["container"]], env=self.stack.environment, stdout=resources)
            try:
                return subprocess.run(["k6", "run", "--quiet", "--log-output", "none", "--no-usage-report",
                    "--include-system-env-vars", "--config", str(config), "--summary-export", str(summary),
                    "--out", f"json={samples}", str(ROOT / "load" / "mixed-submission.js")],
                    env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=150).returncode
            finally:
                monitor.terminate()
                monitor.wait(timeout=10)
                if summary.exists():
                    data = json.loads(summary.read_text(encoding="utf-8"))
                    data.pop("setup_data", None)
                    summary.write_text(json.dumps(data), encoding="utf-8")

    def record_journeys(self):
        points = []
        path = self.output / "samples.jsonl"
        if not path.exists():
            return
        with path.open(encoding="utf-8") as source:
            for row in map(json.loads, source):
                if row["type"] == "Point" and row["metric"] in ("mixed_started", "mixed_completed", "mixed_confirmed"):
                    points.append((row["data"]["time"], row["metric"], row["data"]["tags"]))
        in_flight, peak = 0, 0
        totals = Counter()
        for _, metric, tags in sorted(points, key=lambda point: point[0]):
            totals[(metric, tags["synthetic_event"], tags["format"])] += 1
            if metric == "mixed_started":
                in_flight += 1
                peak = max(peak, in_flight)
            elif metric == "mixed_completed":
                in_flight -= 1
        journeys = [
            {"metric": key[0], "event": key[1], "format": key[2], "total": count}
            for key, count in sorted(totals.items())
        ]
        (self.output / "journeys.json").write_text(json.dumps({
            "peakLogicalAttemptsInFlight": peak, "journeys": journeys,
        }), encoding="utf-8")
        return peak

    def record_database(self):
        records = self.stack.app_sql("""
            SELECT record.records_events_id, record.format, record.state, count(*)
            FROM scrapbook.records AS record
            GROUP BY record.records_events_id, record.format, record.state
            ORDER BY record.records_events_id, record.format, record.state;
        """)
        facts = self.stack.app_sql(f"""
            SELECT type, count(*) FROM scrapbook.outbox_messages
            WHERE type IN ('{FIRST_FACT}', '{PHOTO_FACT}') GROUP BY type ORDER BY type;
        """)
        (self.output / "database.json").write_text(json.dumps({
            "records": [line.split("|") for line in records.splitlines()],
            "facts": [line.split("|") for line in facts.splitlines()],
        }), encoding="utf-8")
        return records, facts

    def test_mixed_submission_load(self):
        self.configure_photos(enable_video=True, enable_audio=True)
        self.prepare()
        self.set_photo_clock()
        id_a, first = self.create_photo_event("Carga F08 sintética A")
        id_b, second = self.create_photo_event("Carga F08 sintética B")
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")
        representative = self.stack.env_file.parent / "photo-mixed.png"
        photo_fixture(representative)
        environment = {
            "PATH": os.environ["PATH"],
            "SCRAPBOOK_LOAD_ORIGIN": self.origin,
            "SCRAPBOOK_LOAD_STORAGE_ORIGIN": self.storage["endpoint"],
            "SCRAPBOOK_LOAD_PUBLIC_CODE_A": first,
            "SCRAPBOOK_LOAD_PUBLIC_CODE_B": second,
            "SCRAPBOOK_LOAD_NOTICE_CODE": NOTICE,
            "SCRAPBOOK_LOAD_PHOTO": str(representative),
        }
        stop = Event()
        with ThreadPoolExecutor(max_workers=1) as observer:
            receipts = observer.submit(self.collect_receipts, stop)
            try:
                status = self.run_k6(environment)
            finally:
                stop.set()
        receipt_count = receipts.result()
        (self.output / "receipts.json").write_text(json.dumps({"syntheticEvents": receipt_count}), encoding="utf-8")
        peak = self.record_journeys()
        records, facts = self.record_database()
        self.assertEqual(0, status, "Consulte as evidências de status, checks e latência da rodada.")
        self.assertEqual(200, peak)
        self.assertEqual(170, receipt_count)
        self.assertEqual("\n".join(f"{event}|{kind}|confirmed|{amount}"
            for event in (id_a, id_b) for kind, amount in (("audio", 20), ("photo", 35), ("text", 25), ("video", 20))),
            records)
        self.assertEqual(f"{FIRST_FACT}|2\n{PHOTO_FACT}|170", facts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mede 200 envios mistos em dois eventos descartáveis em loopback.")
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    os.umask(0o077)
    output.mkdir(mode=0o700, parents=True)
    MixedSubmissionLoadTests.output = output
    suite = unittest.TestSuite([MixedSubmissionLoadTests("test_mixed_submission_load")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

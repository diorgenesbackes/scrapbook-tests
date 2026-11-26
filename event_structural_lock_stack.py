import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
from select import select
import statistics
import subprocess
import sys
import time
import unittest
from uuid import UUID, uuid4

from docker_stack import INFRASTRUCTURE, ROOT
from event_stack import EVENTS
from invitation_acceptance_stack import ACCEPT, PREVIEW
from photo_submission_stack import FIRST_FACT, PhotoSubmissionStackTests
from submission_stack import NOTICE


LOCK_AUDIT = "event.structural_lock.applied"


class EventStructuralLockStackTests(PhotoSubmissionStackTests):
    output: Path | None = None
    include_browser = False
    visibility_timeout = 30

    def configure_clock(self):
        self.clock_file = None

    def set_time(self, value):
        pass

    def configure_lock(self):
        self.configure_photos()
        self.configure_transport()
        self.prepare()
        self.set_photo_clock()
        self.seed_notice(NOTICE, "2020-01-01T00:00:00Z")

    def configure_transport(self):
        arguments = [
            sys.executable, "-B", str(INFRASTRUCTURE / "scripts" / "event_structural_lock_fixture.py"),
            "--endpoint", self.storage["endpoint"], "--container", self.storage["container"],
        ]
        if self.visibility_timeout != 30:
            arguments.extend(("--visibility-timeout", str(self.visibility_timeout)))
        fixture = subprocess.run(arguments, env=self.stack.environment, text=True, capture_output=True, timeout=45)
        self.assertEqual(0, fixture.returncode, "Falha ao preparar SNS e SQS locais para a F09.")
        self.transport = json.loads(fixture.stdout)
        override = self.stack.env_file.parent / "structural-lock.compose.json"
        override.write_text(json.dumps({"services": {"service": {"environment": self.transport["environment"] | {
            "InvitationProtection__HashKeyId": "fixture-hash", "InvitationProtection__HashKey": secrets.token_hex(32),
            "InvitationProtection__EncryptionKeyId": "fixture-encryption", "InvitationProtection__EncryptionKey": secrets.token_hex(32),
        }}}}), encoding="utf-8")
        override.chmod(0o600)
        self.stack.compose.extend(("--file", str(override)))

    def track_lock_commits(self):
        override = self.stack.env_file.parent / "lock-commit-timing.compose.json"
        override.write_text(json.dumps({"services": {"postgres": {
            "command": ["postgres", "-c", "track_commit_timestamp=on"],
        }}}), encoding="utf-8")
        self.stack.compose.extend(("--file", str(override)))

    def lock_latencies(self):
        return [tuple(map(float, line.split("|"))) for line in self.stack.app_sql(f"""
            SELECT EXTRACT(EPOCH FROM pg_xact_commit_timestamp(event.xmin) - fact.occurred_at) * 1000,
                EXTRACT(EPOCH FROM event.locked_at - fact.occurred_at) * 1000
            FROM scrapbook.events AS event
            JOIN scrapbook.outbox_messages AS fact ON fact.aggregate_id=event.id AND fact.type='{FIRST_FACT}'
            JOIN scrapbook.records AS record ON record.id=event.first_record_id AND record.records_events_id=event.id
            WHERE event.locked_at IS NOT NULL
                AND pg_xact_commit_timestamp(event.xmin) IS NOT NULL
                AND event.first_record_id::text=fact.payload->>'recordId'
                AND event.id::text=fact.payload->>'eventId'
                AND event.first_record_confirmed_at=record.confirmed_at
                AND record.confirmed_at AT TIME ZONE 'America/Sao_Paulo'=fact.occurred_at;
        """).splitlines()]

    def invite(self, event):
        status, _, invitation = self.request(f"{EVENTS}/{int(event)}/invitations", draft={})
        self.assertEqual(201, status)
        return invitation

    def confirm_text(self, code, draft=None):
        receipt = self.post(code, draft if draft is not None else self.draft(code), str(uuid4()), "Bearer " + secrets.token_hex(32))
        self.assertEqual((201, "confirmed"), (receipt[0], receipt[2].get("state")))
        return receipt[2]

    def lock_state(self, event):
        return self.stack.app_sql(f"""
            SELECT e.version, e.access_version, e.locked_at, e.first_record_id,
                e.first_record_confirmed_at, r.confirmed_at, o.payload->>'recordId', o.state
            FROM scrapbook.events AS e
            JOIN scrapbook.records AS r ON r.id=e.first_record_id AND r.records_events_id=e.id
            JOIN scrapbook.outbox_messages AS o ON o.aggregate_id=e.id AND o.type='{FIRST_FACT}'
            WHERE e.id={int(event)} AND e.locked_at IS NOT NULL;
        """).splitlines()

    def wait_for_lock(self, event):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            rows = self.lock_state(event)
            if rows:
                version, access, locked, record_id, first_at, confirmed_at, message_record, state = rows[0].split("|")
                if state == "published":
                    self.assertEqual((record_id, first_at), (message_record, confirmed_at))
                    return version, access, locked, record_id
            time.sleep(0.12)
        self.fail("A trava e a publicação da primeira confirmação não convergiram.")

    def assert_audit(self, event):
        self.assertEqual("1", self.admin_sql(f"""
            SELECT count(*) FROM scrapbook.audit_entries AS a
            JOIN scrapbook.outbox_messages AS o ON o.aggregate_id=a.audit_entries_events_id
                AND o.type='{FIRST_FACT}' AND o.correlation_id=a.correlation_id
            WHERE a.audit_entries_events_id={int(event)} AND a.action='{LOCK_AUDIT}'
                AND a.actor_type='system' AND a.audit_entries_accounts_id IS NULL AND a.outcome='success';
        """))

    def assert_locked_paths(self, event, invitation):
        path = f"{EVENTS}/{int(event)}"
        _, _, detail = self.request(path)
        self.assertFalse(detail["canEditDetails"])
        self.assertTrue(detail["openingPolicy"]["canEdit"])
        self.assertTrue({"lockedAt", "firstRecordId", "firstRecordConfirmedAt"}.isdisjoint(detail))
        change = {"name": "Alteração sintética", "startsAt": detail["startsAt"], "welcomeMessage": None,
                  "expectedVersion": detail["version"]}
        self.assertEqual((409, "EVENT_LOCKED"), self.problem(path + "/details", method="PUT", draft=change))
        self.assertEqual((409, "EVENT_VERSION_CONFLICT"),
            self.problem(path + "/details", method="PUT", draft=change | {"expectedVersion": "0"}))
        self.assertEqual({"state": "locked", "invitation": None}, self.request(path + "/invitations/current")[2])
        self.assertEqual((409, "INVITATION_EVENT_LOCKED"), self.problem(path + "/invitations", draft={}))
        for route in (PREVIEW, ACCEPT):
            self.assertEqual((409, "INVITATION_EVENT_LOCKED"), self.problem(route, draft={"token": invitation["token"]}, other=True))
        self.assertEqual("invalidated|t|t|t|1", self.stack.app_sql(f"""
            SELECT state, invalidated_at IS NOT NULL, token_ciphertext IS NULL, encryption_key_id IS NULL, version
            FROM scrapbook.invitations WHERE id={int(invitation['id'])};
        """))
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(path, outsider=True))
        updated = self.request(path + "/opening-policy", method="PUT", draft={"mode": "scheduled",
            "opensOn": (self.event_start.date() + timedelta(days=4)).isoformat(), "expectedVersion": detail["version"]})
        self.assertEqual((200, "scheduled", False), (updated[0], updated[2]["openingPolicy"]["mode"], updated[2]["canEditDetails"]))

    def repeat_first_fact(self, event):
        stored = json.loads(self.stack.app_sql(f"""
            SELECT json_build_object('messageId', o.message_id, 'type', o.type, 'version', o.protocol_version,
                'eventId', o.aggregate_id::text, 'correlationId', o.correlation_id,
                'occurredAt', o.payload->>'confirmedAt', 'data', o.payload - 'eventId')
            FROM scrapbook.outbox_messages AS o WHERE aggregate_id={int(event)} AND type='{FIRST_FACT}';
        """))
        self.aws(("sns", "publish"), "--topic-arn", self.transport["topicArn"], "--message", json.dumps(stored),
            "--message-attributes", json.dumps({"type": {"DataType": "String", "StringValue": FIRST_FACT}}))
        return str(UUID(stored["messageId"]))

    def wait_queue_drained(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            counts = self.aws(("sqs", "get-queue-attributes"), "--queue-url", self.transport["queueUrl"],
                "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible")["Attributes"]
            if counts == {"ApproximateNumberOfMessages": "0", "ApproximateNumberOfMessagesNotVisible": "0"}:
                return
            time.sleep(0.2)
        self.fail("Ainda existem entregas do travamento pendentes no transporte local.")

    def verify_text(self):
        event, code = self.create_photo_event("Evento sintético de texto")
        invitation = self.invite(event)
        self.assertEqual("pending", self.request(f"{EVENTS}/{event}/invitations/current")[2]["state"])
        self.confirm_text(code)
        version, access, locked, record = self.wait_for_lock(event)
        self.assertEqual(("1", "1"), (version, access))
        self.assert_locked_paths(event, invitation)
        self.confirm_text(code)
        message = self.repeat_first_fact(event)
        self.wait_queue_drained()
        self.assertEqual(("2", "2", locked, record), self.wait_for_lock(event))
        self.assertEqual("1|completed", self.stack.app_sql(f"""
            SELECT count(*), min(state) FROM scrapbook.processed_messages
            WHERE consumer='event-structural-lock' AND source='internal' AND message_id='{message}';
        """))
        self.assert_audit(event)

    def verify_photo(self, valid):
        event, code = self.create_photo_event("Evento sintético de mídia")
        png = (ROOT / "photos" / "fixtures" / "palette.png").read_bytes() if valid else b"fixture sem imagem"
        draft = self.photo_draft(code, png, "image/png")
        path, bearer = self.admit(code, draft)
        file_key = draft["files"][0]["fileKey"]
        temporary, version = self.upload(png, self.authorize_upload(path, bearer, file_key))
        self.assertTrue(self.request(f"{EVENTS}/{event}")[2]["canEditDetails"])
        self.assertEqual([], self.lock_state(event))
        self.notify_uploaded(temporary, version, datetime.now(timezone.utc), len(png))
        self.wait_for_status(path, bearer, "confirmed" if valid else "rejected")
        if valid:
            self.wait_for_lock(event)
            self.assert_audit(event)
        else:
            self.assertTrue(self.request(f"{EVENTS}/{event}")[2]["canEditDetails"])
            self.assertEqual("0", self.stack.app_sql(f"SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={event};"))

    @contextmanager
    def defer_deduplication(self):
        command = [*self.stack.compose, "exec", "-T", "postgres", "psql", "-qXAt", "-U", "postgres",
                   "-d", "scrapbook", "-v", "ON_ERROR_STOP=1"]
        with subprocess.Popen(command, env=self.stack.environment, cwd=INFRASTRUCTURE,
                              text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as lock:
            try:
                lock.stdin.write("BEGIN;\nLOCK TABLE scrapbook.processed_messages IN SHARE MODE;\nSELECT 'ready';\n")
                lock.stdin.flush()
                self.assertTrue(select([lock.stdout], [], [], 6)[0], "O lock controlado da fixture não iniciou.")
                self.assertEqual("ready", lock.stdout.readline().strip())
                yield
            finally:
                _, stderr = lock.communicate("ROLLBACK;\n", timeout=8)
                self.assertEqual(0, lock.returncode, stderr)

    def verify_pre_lock_commit(self):
        event, code = self.create_photo_event("Evento sintético de corrida")
        invitation = self.invite(event)
        path = f"{EVENTS}/{event}"
        with self.defer_deduplication():
            self.confirm_text(code)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                waiting = self.admin_sql("""
                    SELECT EXISTS (SELECT 1 FROM pg_locks WHERE relation='scrapbook.processed_messages'::regclass
                        AND locktype='relation' AND NOT granted);
                """)
                if waiting == "t":
                    break
                time.sleep(0.05)
            else:
                self.fail("O consumidor não aguardou a liberação da deduplicação antes da corrida.")
            detail = self.request(path)[2]
            self.assertTrue(detail["canEditDetails"])
            self.assertEqual(200, self.request(path + "/details", method="PUT", draft={"name": "Edição antes da trava",
                "startsAt": detail["startsAt"], "welcomeMessage": None, "expectedVersion": detail["version"]})[0])
            accepted = self.request(ACCEPT, draft={"token": invitation["token"]}, other=True)
            self.assertEqual((200, "accepted"), (accepted[0], accepted[2].get("state")))
        self.wait_for_lock(event)
        self.assertEqual(accepted[2], self.request(ACCEPT, draft={"token": invitation["token"]}, other=True)[2])
        self.assertEqual("Edição antes da trava", self.request(path, other=True)[2]["name"])
        self.assertEqual("2", self.stack.app_sql(f"SELECT count(*) FROM scrapbook.event_members WHERE event_members_events_id={event};"))
        self.assert_audit(event)

    def run_lock_browser(self):
        events = [self.create_photo_event(f"Evento sintético E2E F09 {index + 1}") for index in range(2)]
        environment = self.stack.environment | {
            "PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_LOCK_EVENTS": json.dumps([{"id": str(event), "code": code} for event, code in events]),
            "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_TEST_OTHER_AUTHORIZATION": self.second_authorization,
            "SCRAPBOOK_TEST_OTHER_ORIGIN": self.second_origin,
        }
        environment.pop("NO_COLOR", None)
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.event-structural-lock.config.ts",
                        "--grep-invert", "@recuperacao"],
                       cwd=ROOT, env=environment, check=True, timeout=210)

    def test_event_structural_lock(self):
        self.configure_lock()
        self.verify_text()
        self.verify_photo(True)
        self.verify_photo(False)
        self.verify_pre_lock_commit()
        print("Trava de texto e foto, rejeição, convites, abertura e precedência sob lock verificadas.", flush=True)
        if self.include_browser:
            self.run_lock_browser()

    def test_event_structural_lock_load(self):
        self.track_lock_commits()
        self.configure_lock()
        self.assertEqual("on", self.stack.app_sql("SHOW track_commit_timestamp;"))
        with ThreadPoolExecutor(max_workers=12) as executor:
            events = list(executor.map(lambda n: self.create_photo_event(f"Carga sintética F09 {n}"), range(100)))
            drafts = list(executor.map(lambda entry: self.draft(entry[1]), events))
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=20) as executor:
            list(executor.map(lambda pair: self.confirm_text(pair[0][1], pair[1]), zip(events, drafts)))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            locked_count = int(self.stack.app_sql("SELECT count(*) FROM scrapbook.events WHERE locked_at IS NOT NULL;"))
            if locked_count == 100:
                break
            time.sleep(0.12)
        elapsed = time.monotonic() - started
        samples = self.lock_latencies()
        latencies = [committed for committed, _ in samples]
        internal_latencies = [internal for _, internal in samples]
        report = {"events": 100, "concurrentClients": 20, "observedLocks": len(latencies), "elapsedSeconds": elapsed}
        if len(latencies) > 1:
            report.update(p50Ms=statistics.median(latencies), p95Ms=statistics.quantiles(latencies, n=20, method="inclusive")[18],
                maxMs=max(latencies), exceededFiveSeconds=sum(value > 5000 for value in latencies),
                internalLockTiming={"p50Ms": statistics.median(internal_latencies),
                    "p95Ms": statistics.quantiles(internal_latencies, n=20, method="inclusive")[18],
                    "maxMs": max(internal_latencies)})
        if self.output:
            self.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        self.assertEqual(100, len(latencies))
        self.assertGreaterEqual(min(latencies), 0)
        self.assertLessEqual(max(latencies), 5000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida a trava eventual do evento em PostgreSQL e SNS/SQS locais.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--load", action="store_true")
    modes.add_argument("--browser", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        os.umask(0o077)
    EventStructuralLockStackTests.output = args.output
    EventStructuralLockStackTests.include_browser = args.browser
    scenario = "test_event_structural_lock_load" if args.load else "test_event_structural_lock"
    suite = unittest.TestSuite([EventStructuralLockStackTests(scenario)])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from uuid import UUID, uuid4

from docker_stack import ROOT
from event_stack import EVENTS
from event_structural_lock_stack import EventStructuralLockStackTests, LOCK_AUDIT
from mixed_submission_load import MixedSubmissionLoadTests
from photo_submission_stack import FIRST_FACT


APPLIED = "event.structural-lock.applied.v1"
FAILED = "event.structural-lock.failed.v1"


def wait_for(action, message, timeout=22):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = action()
        if observed:
            return observed
        time.sleep(0.2)
    raise AssertionError(message)


class EventStructuralLockRecoveryStackTests(EventStructuralLockStackTests):
    visibility_timeout = 2
    drop_lock_ack = True

    @contextmanager
    def reject_writes(self, table, constraint, predicate):
        self.admin_sql(f"ALTER TABLE scrapbook.{table} ADD CONSTRAINT {constraint} CHECK ({predicate}) NOT VALID;")
        try:
            yield
        finally:
            self.admin_sql(f"ALTER TABLE scrapbook.{table} DROP CONSTRAINT {constraint};")

    def first_fact(self, event):
        row = self.stack.app_sql(f"""
            SELECT message_id, state, attempt_count, correlation_id, payload->>'confirmedAt'
            FROM scrapbook.outbox_messages WHERE type='{FIRST_FACT}' AND aggregate_id={int(event)};
        """)
        if not row:
            return None
        message, state, attempts, correlation, confirmed_at = row.split("|")
        return UUID(message), state, int(attempts), UUID(correlation), confirmed_at

    def outcomes(self, event):
        return json.loads(self.stack.app_sql(f"""
            SELECT coalesce(json_agg(json_build_object('type', type, 'status', state, 'version', protocol_version,
                'id', message_id, 'cause', causation_id, 'correlation', correlation_id,
                'occurredAt', occurred_at, 'data', payload) ORDER BY id), '[]'::json)
            FROM scrapbook.outbox_messages
            WHERE aggregate_type='event' AND aggregate_id={int(event)} AND type IN ('{APPLIED}', '{FAILED}');
        """))

    def assert_outcome(self, row, event, fact, kind, stage=None):
        self.assertEqual((kind, "pending", 1, fact[0], fact[3]),
            (row["type"], row["status"], row["version"], UUID(row["cause"]), UUID(row["correlation"])))
        self.assertNotEqual(fact[0], UUID(row["id"]))
        data = row["data"]
        marker = "lockedAt" if stage is None else "failedAt"
        expected_keys = {"eventId", "confirmedAt", marker}
        if stage is not None:
            expected_keys.update(("stage", "code"))
            self.assertEqual(stage, data["stage"])
            self.assertRegex(data["code"], r"^[A-Z_]{1,80}$")
        self.assertEqual(expected_keys, set(data))
        self.assertEqual(str(event), data["eventId"])
        self.assertEqual(datetime.fromisoformat(fact[4]), datetime.fromisoformat(data["confirmedAt"]))
        occurred = datetime.fromisoformat(row["occurredAt"])
        outcome_time = datetime.fromisoformat(data[marker])
        self.assertLess(abs((occurred - outcome_time).total_seconds()), 0.000002)
        self.assertGreaterEqual(outcome_time, datetime.fromisoformat(data["confirmedAt"]))

    def read_deliveries(self, queue, visibility=15, limit=10):
        response = self.aws(("sqs", "receive-message"), "--queue-url", queue,
            "--wait-time-seconds", "1", "--visibility-timeout", str(visibility), "--max-number-of-messages", str(limit))
        return [delivery["Body"] for delivery in response.get("Messages", [])]

    def read_dead_letter(self, message):
        queue = self.transport["deadLetterQueueUrl"]

        def find():
            return next((body for body in self.read_deliveries(queue, visibility=1, limit=1)
                if json.loads(body).get("messageId") == str(message)), None)

        try:
            return wait_for(find, "O SQS emulado não preservou o envelope original na DLQ.", timeout=30)
        except AssertionError:
            for name in ("queueUrl", "deadLetterQueueUrl"):
                counts = self.aws(("sqs", "get-queue-attributes"), "--queue-url", self.transport[name],
                    "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible")["Attributes"]
                print(json.dumps({"queue": name, "counts": counts}), flush=True)
            raise

    def recover(self, stage, message, expected, *, flags=(), body=None):
        sha = subprocess.check_output(["git", "-C", str(ROOT.parent / "scrapbook-service"), "rev-parse", "HEAD"],
                                      text=True, timeout=6).strip()
        result = self.stack.compose_command("exec", "-T", "service", "dotnet", "ScrapBook.Api.dll",
            "event-structural-lock-recover", stage, str(message), "INC-F092-LAB", sha, *flags, stdin=body, check=False)
        self.assertEqual((0, expected), (result.returncode, result.stdout.strip()),
            "O comando autorizado não confirmou a operação esperada para o fato original.")

    def subscribe(self, queue):
        attributes = self.aws(("sqs", "get-queue-attributes"), "--queue-url", queue, "--attribute-names", "QueueArn")["Attributes"]
        self.aws(("sns", "subscribe"), "--topic-arn", self.transport["topicArn"], "--protocol", "sqs", "--notification-endpoint",
            attributes["QueueArn"], "--attributes", json.dumps({"RawMessageDelivery": "true",
                "FilterPolicyScope": "MessageAttributes", "FilterPolicy": json.dumps({"type": [FIRST_FACT]})}))

    def verify_terminal_publication(self):
        event, code = self.create_photo_event("Retomada da publicação terminal")
        topic = self.transport["topicArn"]
        self.aws(("sns", "delete-topic"), "--topic-arn", topic)
        try:
            self.confirm_text(code)
            terminal = wait_for(lambda: self.outcomes(event), "A falha definitiva de publicação não foi registrada.")
            self.assertEqual(1, len(terminal))
            original = self.first_fact(event)
            self.assertEqual("failed", original[1])
            self.assert_outcome(terminal[0], event, original, FAILED, "publish")
            self.assertEqual([], self.lock_state(event))
        finally:
            restored = self.aws(("sns", "create-topic"), "--name", topic.rsplit(":", 1)[1])
            self.assertEqual(topic, restored["TopicArn"])
            self.subscribe(self.transport["queueUrl"])

        self.recover("publish", original[0], "RECOVERY_REOPENED")
        self.wait_for_lock(event)
        self.wait_queue_drained()
        rows = self.outcomes(event)
        self.assertEqual(2, len(rows))
        self.assertEqual(terminal[0], rows[0])
        self.assert_outcome(rows[1], event, self.first_fact(event), APPLIED)
        self.assert_audit(event)

    def verify_publish_after_lease_expiry(self):
        event, code = self.create_photo_event("Recuperação da publicação sem marcação")
        observer = self.aws(("sqs", "create-queue"), "--queue-name", "fixture-f092-observe-" + uuid4().hex)["QueueUrl"]
        self.subscribe(observer)
        with self.reject_writes("outbox_messages", "fixture_f092_publish_failure",
                f"type <> '{FIRST_FACT}' OR aggregate_id <> {int(event)} OR state <> 'published'"):
            self.confirm_text(code)
            wait_for(lambda: self.lock_state(event), "A publicação aceita não chegou ao consumidor.")
            original = self.first_fact(event)
            self.assertEqual(("publishing", 1), original[1:3])
            initial_outcomes = self.outcomes(event)
            self.assertEqual(1, len(initial_outcomes))
            self.assert_outcome(initial_outcomes[0], event, original, APPLIED)

        def ack_failed():
            observed = subprocess.check_output(["docker", "logs", self.storage["container"]],
                env=self.stack.environment, text=True, stderr=subprocess.STDOUT, timeout=10)
            return "F092_LOCK_ACK_DROPPED" in observed

        wait_for(ack_failed, "O emulador não observou a falha controlada de ACK após o commit.")
        self.wait_queue_drained()

        def marked_published():
            current = self.first_fact(event)
            return current if current[1] == "published" else None

        retried = wait_for(marked_published, "A lease expirada não foi recuperada.", timeout=40)
        self.assertEqual(original[0], retried[0])
        self.assertGreaterEqual(retried[2], 2)
        envelopes = []

        def both_published():
            envelopes.extend(self.read_deliveries(observer))
            return len(envelopes) >= 2

        wait_for(both_published, "A fixture não observou a republicação após expirar a lease.", timeout=12)
        self.assertEqual(2, len(envelopes))
        self.assertEqual(envelopes[0], envelopes[1])
        self.assertEqual(str(original[0]), json.loads(envelopes[0])["messageId"])
        self.wait_queue_drained()
        self.assertEqual(initial_outcomes, self.outcomes(event))
        self.assert_audit(event)

    def verify_consume_recovery(self, event, confirm):
        invitation = self.invite(event)
        path = f"{EVENTS}/{int(event)}"
        with self.reject_writes("audit_entries", "fixture_f092_audit_failure",
                f"audit_entries_events_id <> {int(event)} OR action <> '{LOCK_AUDIT}'"):
            confirm()
            terminal = wait_for(lambda: self.outcomes(event), "O resultado terminal de consumo não foi persistido.")
            self.assertEqual(1, len(terminal))
            fact = self.first_fact(event)
            self.assert_outcome(terminal[0], event, fact, FAILED, "consume")
            envelope = self.read_dead_letter(fact[0])
            self.assertEqual(str(fact[3]), json.loads(envelope)["correlationId"])
            self.assertEqual("quarantined", self.stack.app_sql(f"""
                SELECT state FROM scrapbook.processed_messages WHERE consumer='event-structural-lock'
                    AND source='internal' AND message_id='{fact[0]}';
            """))
            self.recover("reconcile-dlq", fact[0], "RECOVERY_ALREADY_TERMINAL", body=envelope)
            self.assertEqual([], self.lock_state(event))
            detail = self.request(path)[2]
            self.assertTrue(detail["canEditDetails"])
            self.assertEqual("pending", self.request(path + "/invitations/current")[2]["state"])
            opens_on = (self.event_start.date() + timedelta(days=4)).isoformat()
            response = self.request(path + "/opening-policy", method="PUT", draft={
                "mode": "scheduled", "opensOn": opens_on, "expectedVersion": detail["version"],
            })
            self.assertEqual(200, response[0])
        self.recover("consume", fact[0], "RECOVERY_REOPENED", flags=("--rearm",))
        locked = self.wait_for_lock(event)
        final = self.outcomes(event)
        self.assertEqual(2, len(final))
        self.assertEqual(terminal[0], final[0])
        self.assert_outcome(final[1], event, self.first_fact(event), APPLIED)
        self.assertEqual(fact[0], self.first_fact(event)[0])
        self.assert_audit(event)
        self.assertEqual("invalidated|1", self.stack.app_sql(f"""
            SELECT state, version FROM scrapbook.invitations WHERE id={int(invitation['id'])};
        """))
        self.assertEqual("completed", self.stack.app_sql(f"""
            SELECT state FROM scrapbook.processed_messages WHERE consumer='event-structural-lock'
                AND source='internal' AND message_id='{fact[0]}';
        """))
        self.assertEqual(opens_on, self.request(path)[2]["openingPolicy"]["opensOn"])
        dlq = self.aws(("sqs", "get-queue-attributes"), "--queue-url", self.transport["deadLetterQueueUrl"],
            "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible")["Attributes"]
        self.assertEqual(1, sum(int(count) for count in dlq.values()))
        self.assertEqual(str(fact[0]), self.repeat_first_fact(event))
        self.wait_queue_drained()
        self.assertEqual(final, self.outcomes(event))
        self.assertEqual(locked, self.wait_for_lock(event))
        self.assertEqual("1", self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.audit_entries
            WHERE audit_entries_events_id={int(event)} AND action='{LOCK_AUDIT}';
        """))
        self.assert_audit(event)

    def test_queue_recovery(self):
        self.configure_lock()
        self.verify_publish_after_lease_expiry()
        self.verify_terminal_publication()
        event, code = self.create_photo_event("Recuperação sintética da trava")
        self.verify_consume_recovery(event, lambda: self.confirm_text(code))

    def test_queue_drain(self):
        self.visibility_timeout = 30
        self.drop_lock_ack = False
        self.track_lock_commits()
        self.configure_lock()
        events = [self.create_photo_event(f"Fila de recuperação sintética {index + 1}") for index in range(20)]
        drafts = [(code, self.draft(code)) for _, code in events]
        with self.defer_deduplication():
            with ThreadPoolExecutor(max_workers=10) as executor:
                list(executor.map(lambda item: self.confirm_text(*item), drafts))

            def queue_depth():
                states = self.aws(("sqs", "get-queue-attributes"), "--queue-url", self.transport["queueUrl"],
                    "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible")["Attributes"]
                return sum(int(count) for count in states.values())

            backlog = wait_for(lambda: (count := queue_depth()) >= 10 and count,
                "A fixture não observou ao menos dez confirmações acumuladas no SQS.", timeout=5)
            self.assertEqual("0", self.stack.app_sql("SELECT count(*) FROM scrapbook.events WHERE locked_at IS NOT NULL;"))

        released = time.monotonic()
        wait_for(lambda: len(self.lock_latencies()) == len(events), "A retomada não drenou todas as confirmações.")
        self.wait_queue_drained()
        self.assertEqual(str(len(events)), self.stack.app_sql(f"SELECT count(*) FROM scrapbook.outbox_messages WHERE type='{APPLIED}';"))
        print(json.dumps({"events": len(events), "sampledBacklog": backlog, "drainSeconds": time.monotonic() - released}),
              flush=True)

    def test_browser_recovery(self):
        self.configure_lock()
        events = [self.create_photo_event(f"Recuperação sintética no navegador {index + 1}") for index in range(2)]
        ready = self.stack.env_file.parent / "browser-confirmed"
        environment = self.stack.environment | {
            "PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_LOCK_EVENTS": json.dumps([{"id": str(event), "code": code} for event, code in events]),
            "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_TEST_OTHER_AUTHORIZATION": self.second_authorization,
            "SCRAPBOOK_TEST_OTHER_ORIGIN": self.second_origin,
            "SCRAPBOOK_TEST_LOCK_RECOVERY_READY": str(ready),
        }
        environment.pop("NO_COLOR", None)
        command = ["npm", "exec", "--", "playwright", "test", "--config", "playwright.event-structural-lock.config.ts",
                   "--grep", "@recuperacao"]

        def run_browser():
            return subprocess.run(command, cwd=ROOT, env=environment, timeout=90, check=True)

        with ThreadPoolExecutor(max_workers=1) as executor:
            def show_receipt():
                nonlocal browser
                browser = executor.submit(run_browser)

                def receipt_displayed():
                    if browser.done():
                        browser.result()
                    return ready.exists()

                wait_for(receipt_displayed, "O navegador não observou o recibo durante a falha.")

            browser = None
            self.verify_consume_recovery(events[0][0], show_receipt)
            self.assertIsNotNone(browser)
            browser.result()


class EventStructuralLockMixedLoadTests(EventStructuralLockStackTests, MixedSubmissionLoadTests):
    def configure_photos(self, *, enable_video=False, enable_audio=False):
        super().configure_photos(enable_video=enable_video, enable_audio=enable_audio)
        self.configure_transport()

    def publish_receipts(self, batch):
        observed_at = self.stack.compose_command("exec", "-T", "service", "date", "-u", "+%Y-%m-%dT%H:%M:%S.%NZ").stdout.strip()
        super().publish_receipts(batch, observed_at=observed_at)

    def test_mixed_submission_load(self):
        self.track_lock_commits()
        super().test_mixed_submission_load()
        wait_for(lambda: len(self.lock_latencies()) == 2, "As duas travas não convergiram no perfil misto.")
        self.wait_queue_drained()
        samples = [durable for durable, _ in self.lock_latencies()]
        self.assertGreaterEqual(min(samples), 0)
        self.assertLessEqual(max(samples), 5000)
        self.assertEqual("2", self.stack.app_sql(f"SELECT count(*) FROM scrapbook.outbox_messages WHERE type='{APPLIED}';"))
        result = {"durableLockMilliseconds": sorted(samples), "events": len(samples), "overFiveSeconds": 0}
        (self.output / "structural-lock.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida a retomada de travas em PostgreSQL e SNS/SQS descartáveis.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--browser", action="store_true")
    modes.add_argument("--drain", action="store_true")
    modes.add_argument("--mixed-output", type=Path)
    args = parser.parse_args()
    if args.mixed_output:
        os.umask(0o077)
        output = args.mixed_output.resolve()
        output.mkdir(mode=0o700, parents=True)
        test = EventStructuralLockMixedLoadTests("test_mixed_submission_load")
        test.output = output
    else:
        scenario = "test_browser_recovery" if args.browser else "test_queue_drain" if args.drain else "test_queue_recovery"
        test = EventStructuralLockRecoveryStackTests(scenario)
    suite = unittest.TestSuite([test])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

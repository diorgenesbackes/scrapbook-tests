from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
import secrets
import subprocess
import unittest
from uuid import uuid4

from docker_stack import ROOT
from event_public_stack import EventPublicStackTests, PUBLIC


OPENING = "2032-02-29T02:00:00Z"
CLOSED = "2032-03-01T15:00:00Z"
NOTICE = "fixture-f08-v1"
BODY = "Aviso sintético de acesso a contribuições."
NAME = "Autoria sintética F08"
MESSAGE = "Depoimento privado sintético F08"
FIRST_FACT = "event.first-record-confirmed.v1"


class SubmissionStackTests(EventPublicStackTests):
    def configure_submissions(self):
        self.configure_clock()
        self.set_time(OPENING)
        override = self.stack.env_file.parent / "submissions.compose.json"
        override.write_text(json.dumps({"services": {"service": {"environment": {
            "Submissions__Enabled": "true", "Submissions__KeyId": "fixture-f08",
            "Submissions__Key": secrets.token_hex(32),
        }}}}), encoding="utf-8")
        override.chmod(0o600)
        self.stack.compose.extend(("--file", str(override)))

    def seed_notice(self, code, effective_at):
        digest = sha256(BODY.encode()).hexdigest()
        self.stack.app_sql(f"""
            INSERT INTO scrapbook.consent_notices (code, body, body_hash, effective_at)
            VALUES ('{code}', '{BODY}', decode('{digest}', 'hex'), '{effective_at}');
        """)

    @staticmethod
    def path(code, suffix):
        return PUBLIC + "/" + code + "/" + suffix

    def draft(self, code, name=NAME, text=MESSAGE, notice=NOTICE):
        status, headers, consent = self.request(self.path(code, "submission-consents"), anonymous=True,
            draft={"guestName": name, "noticeCode": notice, "accepted": True})
        self.assertEqual(200, status)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual({"consentProof", "expiresAt"}, set(consent))
        return {"category": "testimonial", "format": "text", "text": text,
            "identification": {"guestName": name, "noticeCode": notice, "consentProof": consent["consentProof"]}}

    def post(self, code, draft, key, authorization):
        return self.request(self.path(code, "submissions"), anonymous=True, key=key, draft=draft,
            headers={"Authorization": authorization})

    def verify_admission_and_idempotency(self, first_id, first, second):
        status, headers, notice = self.request(self.path(first, "contribution-notice"), anonymous=True)
        self.assertEqual((200, "no-store", {"code": NOTICE, "body": BODY}), (status, headers["Cache-Control"], notice))
        draft = self.draft(first)
        key = str(uuid4())
        authorization = "Bearer " + secrets.token_hex(32)
        saved_secret = authorization.split()[1]

        self.set_time("2032-02-29T01:59:59.999Z")
        status, _, error = self.post(first, draft, key, authorization)
        self.assertEqual((409, "UPLOAD_WINDOW_CLOSED", "before"), (status, error["code"], error["windowState"]))
        self.set_time(OPENING)
        self.assertEqual((409, "SUBMISSION_CONSENT_REQUIRED"), self.problem(self.path(second, "submissions"),
            anonymous=True, draft=draft, headers={"Authorization": authorization}))
        changed = draft | {"identification": draft["identification"] | {"guestName": "Outro nome sintético"}}
        self.assertEqual("SUBMISSION_CONSENT_REQUIRED", self.post(first, changed, key, authorization)[2]["code"])
        self.assertEqual("REQUEST_VALIDATION_FAILED", self.post(first, draft | {"text": "🎉" * 5001}, key, authorization)[2]["code"])

        with ThreadPoolExecutor(max_workers=8) as executor:
            replies = list(executor.map(lambda _: self.post(first, draft, key, authorization), range(8)))
        self.assertEqual([200] * 7 + [201], sorted(r[0] for r in replies))
        receipt = replies[0][2]
        self.assertEqual({"state", "confirmedAt"}, set(receipt))
        self.assertEqual("confirmed", receipt["state"])
        self.assertTrue(all(data == receipt for _, _, data in replies))
        location = next(headers["Location"] for status, headers, _ in replies if status == 201)
        self.assertTrue(location.endswith("/" + key))
        self.assertEqual("1|1|1|1", self.stack.app_sql(f"""
            SELECT (SELECT count(*) FROM scrapbook.records WHERE records_events_id={first_id}),
                   (SELECT count(*) FROM scrapbook.idempotent_operations WHERE idempotent_operations_events_id={first_id}),
                   (SELECT count(*) FROM scrapbook.upload_sessions WHERE upload_sessions_events_id={first_id}),
                   (SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={first_id} AND type='{FIRST_FACT}');
        """))
        self.assertEqual("t", self.stack.app_sql(f"""
            SELECT sender_name='{NAME}' AND message='{MESSAGE}' AND expected_file_count=0
                AND consented_at='2032-02-28 23:00:00' AND submitted_at='2032-02-28 23:00:00'
            FROM scrapbook.records WHERE records_events_id={first_id};
        """))

        self.set_time(CLOSED)
        path = self.path(first, "submissions/" + key)
        self.assertEqual(receipt, self.request(path, anonymous=True, headers={"Authorization": authorization})[2])
        self.assertEqual(receipt, self.post(first, draft, key, authorization)[2])
        status, _, conflict = self.post(first, draft | {"text": "Conteúdo divergente"}, key, authorization)
        self.assertEqual((409, "IDEMPOTENCY_KEY_REUSED"), (status, conflict["code"]))
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(path, anonymous=True))
        wrong = "Bearer " + secrets.token_hex(32)
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(path, anonymous=True, headers={"Authorization": wrong}))
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(self.path(second, "submissions/" + key),
            anonymous=True, headers={"Authorization": authorization}))
        self.assertEqual("UPLOAD_WINDOW_CLOSED", self.post(first, draft, str(uuid4()), authorization)[2]["code"])
        self.stack.app_sql(f"""
            UPDATE scrapbook.idempotent_operations SET authorization_expires_at='2032-03-01T15:00:00Z'
            WHERE idempotent_operations_events_id={first_id};
        """)
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(path, anonymous=True, headers={"Authorization": authorization}))
        self.assertEqual(404, self.post(first, draft, key, authorization)[0])
        return (key, saved_secret, draft["identification"]["consentProof"])

    def verify_rollback_and_notice_version(self, second_id, second):
        self.set_time(OPENING)
        old = self.draft(second)
        self.seed_notice("fixture-f08-v2", "2032-02-29T02:01:00Z")
        self.set_time("2032-02-29T02:01:00Z")
        authorization = "Bearer " + secrets.token_hex(32)
        self.assertEqual("SUBMISSION_CONSENT_REQUIRED", self.post(second, old, str(uuid4()), authorization)[2]["code"])
        draft = self.draft(second, notice="fixture-f08-v2")
        self.admin_sql(f"""
            ALTER TABLE scrapbook.outbox_messages ADD CONSTRAINT ck_fixture_submission_fail
            CHECK (aggregate_id <> {second_id} OR type <> '{FIRST_FACT}') NOT VALID;
        """)
        key = str(uuid4())
        try:
            self.assertEqual(500, self.post(second, draft, key, authorization)[0])
            self.assertEqual("0|0|0|0", self.stack.app_sql(f"""
                SELECT (SELECT count(*) FROM scrapbook.records WHERE records_events_id={second_id}),
                       (SELECT count(*) FROM scrapbook.idempotent_operations WHERE idempotent_operations_events_id={second_id}),
                       (SELECT count(*) FROM scrapbook.upload_sessions WHERE upload_sessions_events_id={second_id}),
                       (SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={second_id} AND type='{FIRST_FACT}');
            """))
        finally:
            self.admin_sql("ALTER TABLE scrapbook.outbox_messages DROP CONSTRAINT ck_fixture_submission_fail;")
        with ThreadPoolExecutor(max_workers=4) as executor:
            replies = list(executor.map(lambda item: self.post(second, draft, item, authorization),
                [key, str(uuid4()), str(uuid4()), str(uuid4())]))
        self.assertEqual([201] * 4, [r[0] for r in replies])
        self.assertEqual("4|1", self.stack.app_sql(f"""
            SELECT (SELECT count(*) FROM scrapbook.records WHERE records_events_id={second_id}),
                   (SELECT count(*) FROM scrapbook.outbox_messages WHERE aggregate_id={second_id} AND type='{FIRST_FACT}');
        """))
        self.assertEqual("0", self.stack.app_sql("SELECT count(*) FROM scrapbook.files;"))

    def run_browser(self, first, second, mode):
        env = self.stack.environment | {
            "PLAYWRIGHT_BASE_URL": self.origin, "SCRAPBOOK_TEST_PUBLIC_CODE": first,
            "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second, "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file),
            "SCRAPBOOK_TEST_SUBMISSION_4G": str(mode == "4g").lower(),
        }
        env.pop("NO_COLOR", None)
        command = ["npm", "exec", "--", "playwright", "test", "--config", "playwright.submission.config.ts"]
        if mode == "4g":
            command.extend(("--project", "android-emulado"))
        subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=190)

    def run_load(self, first_id, first, second_id, second):
        self.set_time(OPENING)
        before = int(self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        env = self.stack.environment | {"SCRAPBOOK_LOAD_ORIGIN": self.origin, "SCRAPBOOK_LOAD_PUBLIC_CODE_A": first,
            "SCRAPBOOK_LOAD_PUBLIC_CODE_B": second, "SCRAPBOOK_LOAD_NOTICE_CODE": NOTICE, "K6_NO_USAGE_REPORT": "true"}
        subprocess.run(["k6", "run", "--quiet", "--no-color", "load/submission.js"], cwd=ROOT, env=env, check=True, timeout=90)
        self.assertEqual(str(before + 200), self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        self.assertEqual("2", self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.outbox_messages
            WHERE type='{FIRST_FACT}' AND aggregate_id IN ({first_id},{second_id});
        """))

    def test_submission(self):
        self.configure_submissions()
        self.prepare()
        first_event, first = self.create_event("Evento de texto sintético A")
        second_event, second = self.create_event("Evento de texto sintético B")
        first_id, second_id = int(first_event["id"]), int(second_event["id"])
        self.seed_notice(NOTICE, "2032-02-28T01:00:00Z")
        markers = self.verify_admission_and_idempotency(first_id, first, second)
        self.verify_rollback_and_notice_version(second_id, second)
        for mode in ("browser", "4g", "load"):
            with self.subTest(mode=mode):
                if mode == "load":
                    self.run_load(first_id, first, second_id, second)
                else:
                    self.run_browser(first, second, mode)
        logs = self.stack.logs("service") + self.stack.logs("ui")
        for marker in (first, second, NAME, MESSAGE, *markers, "Texto sintético do navegador F08"):
            if marker in logs:
                self.fail("Telemetria expôs um valor privado da fixture.")
        print("Prazos, idempotência, rollback, Outbox e sigilo conferidos no PostgreSQL e nos logs.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    suite = unittest.TestSuite([SubmissionStackTests("test_submission")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

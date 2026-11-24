import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import statistics
import subprocess
import time
import unittest
from uuid import uuid4

from docker_stack import ROOT
from event_access_stack import EventAccessStackTests
from event_stack import DRAFT, EVENTS


ISSUED = "2030-02-02T12:00:00.000Z"
BEFORE_EXPIRY = "2030-02-03T11:59:59.999Z"
AT_EXPIRY = "2030-02-03T12:00:00.000Z"
PROJECTS = ("chromium-desktop", "firefox-desktop", "webkit-desktop", "android-emulado", "iphone-emulado")


class InvitationStackTests(EventAccessStackTests):
    def path(self, event):
        return f"{EVENTS}/{event}/invitations"

    def get_current(self, event):
        status, headers, body = self.request(self.path(event) + "/current")
        self.assertEqual(200, status)
        self.assertEqual("no-store", headers["Cache-Control"])
        return body

    def issue(self, event, *, key=None, expected=201):
        status, headers, body = self.request(self.path(event), draft={}, key=key)
        self.assertEqual(expected, status)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual({"id", "token", "expiresAt"}, set(body))
        self.assertRegex(body["token"], r"^v1\.fixture-hash\.[A-Za-z0-9_-]{43}$")
        if status == 201:
            self.assertTrue(headers["Location"].endswith(self.path(event) + "/current"))
        return body

    def verify_authorization(self, event):
        path = self.path(event)
        missing = self.path(9223372036854775807)
        for suffix, options in (("/current", {}), ("", {"draft": {}})):
            self.assertEqual((401, "IDENTITY_AUTHENTICATION_REQUIRED"), self.problem(path + suffix, anonymous=True, **options))
            denial = (404, "RESOURCE_NOT_FOUND")
            self.assertEqual(denial, self.problem(path + suffix, outsider=True, **options))
            self.assertEqual(denial, self.problem(missing + suffix, outsider=True, **options))
        self.assertEqual((403, "REQUEST_ORIGIN_REJECTED"),
            self.problem(path, draft={}, headers={"Origin": "https://other.example.invalid"}))
        self.assertEqual((400, "REQUEST_VALIDATION_FAILED"), self.problem(path, draft={"eventId": "1"}))
        self.assertEqual((400, "REQUEST_VALIDATION_FAILED"), self.problem(path, draft={}, key="invalid-uuid"))

    def verify_rotation(self, event):
        self.assertEqual({"state": "available", "invitation": None}, self.get_current(event))
        first_key = str(uuid4())
        first = self.issue(event, key=first_key)
        self.set_time(BEFORE_EXPIRY)
        self.assertEqual("2030-02-03T12:00:00+00:00", first["expiresAt"])
        self.assertEqual(first, self.issue(event, key=first_key, expected=200))
        self.assertEqual({"state": "pending", "invitation": first}, self.get_current(event))
        second_key = str(uuid4())
        second = self.issue(event, key=second_key)
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual((409, "INVITATION_UNAVAILABLE"), self.problem(self.path(event), draft={}, key=first_key))
        self.assertEqual("superseded|t|t", self.admin_sql(f"""
            SELECT state, token_ciphertext IS NULL, encryption_key_id IS NULL
            FROM scrapbook.invitations WHERE id = {int(first['id'])};
            """))
        self.assertEqual("2", self.admin_sql(f"""
            SELECT count(*) FROM scrapbook.audit_entries
            WHERE audit_entries_events_id = {int(event)} AND action = 'invitation.created';
            """))
        self.assertEqual(f"pending|superseded|{first['id']}", self.admin_sql(f"""
            SELECT before_state->>'previousState', after_state->>'previousState', after_state->>'replacedInvitationId'
            FROM scrapbook.audit_entries WHERE subject_type = 'invitation' AND subject_id = {int(second['id'])};
            """))
        self.verify_secret(first["token"], second["token"])

    def verify_exact_expiry(self, event):
        key = str(uuid4())
        first = self.issue(event, key=key)
        self.set_time(AT_EXPIRY)
        self.assertEqual({"state": "available", "invitation": None}, self.get_current(event))
        self.assertEqual((409, "INVITATION_UNAVAILABLE"), self.problem(self.path(event), draft={}, key=key))
        self.issue(event)
        self.assertEqual("expired|t", self.admin_sql(f"""
            SELECT state, token_ciphertext IS NULL FROM scrapbook.invitations WHERE id = {int(first['id'])};
            """))

    def verify_locked_and_full(self):
        locked = self.immediate_event(DRAFT["startsAt"])
        self.issue(locked)
        self.admin_sql(f"""
            UPDATE scrapbook.events
            SET locked_at = CURRENT_TIMESTAMP, first_record_id = 1, first_record_confirmed_at = CURRENT_TIMESTAMP
            WHERE id = {int(locked)};
            """)
        self.assertEqual({"state": "locked", "invitation": None}, self.get_current(locked))
        self.assertEqual((409, "INVITATION_EVENT_LOCKED"), self.problem(self.path(locked), draft={}))
        full = self.immediate_event(DRAFT["startsAt"])
        self.add_second_owner(full)
        self.assertEqual({"state": "full", "invitation": None}, self.get_current(full))
        self.assertEqual((409, "INVITATION_EVENT_FULL"), self.problem(self.path(full), draft={}, other=True))

    def verify_audit_rollback(self, event):
        initial = self.issue(event)
        self.admin_sql("""
            CREATE FUNCTION scrapbook.fixture_reject_invitation_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Falha de auditoria da fixture';
            END;
            $$;
            CREATE TRIGGER fixture_reject_invitation_audit BEFORE INSERT ON scrapbook.audit_entries
            FOR EACH ROW WHEN (NEW.action = 'invitation.created') EXECUTE FUNCTION scrapbook.fixture_reject_invitation_audit();
            """)
        try:
            self.assertEqual((500, "UNEXPECTED_ERROR"), self.problem(self.path(event), draft={}))
            self.assertEqual({"state": "pending", "invitation": initial}, self.get_current(event))
        finally:
            self.admin_sql("DROP TRIGGER fixture_reject_invitation_audit ON scrapbook.audit_entries;")

    def verify_concurrency(self, event):
        key = str(uuid4())
        with ThreadPoolExecutor(6) as executor:
            results = list(executor.map(lambda _: self.request(self.path(event), draft={}, key=key), range(6)))
            self.assertEqual([200] * 5 + [201], sorted(result[0] for result in results))
            self.assertTrue(all(result[2] == results[0][2] for result in results))
            distinct = list(executor.map(lambda _: self.issue(event), range(6)))
        self.assertEqual(6, len({response["id"] for response in distinct}))
        winner = self.get_current(event)["invitation"]
        self.assertIn(winner, distinct)
        self.assertEqual("1", self.admin_sql(f"""
            SELECT count(*) FROM scrapbook.invitations WHERE invitations_events_id = {int(event)} AND state = 'pending';
            """))
        self.verify_secret(*(response["token"] for response in distinct))

    def verify_secret(self, *tokens):
        logs = self.stack.logs("service") + self.stack.logs("ui")
        audit = self.admin_sql("SELECT before_state || after_state FROM scrapbook.audit_entries WHERE action = 'invitation.created';")
        for token in tokens:
            if token in logs or token in audit:
                self.fail("A credencial sintética aparece em telemetria ou auditoria.")

    def verify_entry_headers(self):
        status, headers, _ = self.stack.get("/convites/aceitar?shared=1")
        self.assertEqual(200, status)
        self.assertEqual("noindex, nofollow", headers["X-Robots-Tag"])
        self.assertEqual("no-referrer", headers["Referrer-Policy"])
        self.assertEqual("DENY", headers["X-Frame-Options"])

    def run_invitation_browser(self):
        events = {name: self.immediate_event(DRAFT["startsAt"]) for name in PROJECTS}
        env = {name: value for name, value in self.stack.environment.items()
            if not name.startswith("SCRAPBOOK_INVITATION_")} | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_TEST_INVITATION_EVENTS": json.dumps(events)}
        if env.pop("NO_COLOR", None):
            env.update(FORCE_COLOR="0", DEBUG_COLORS="0")
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.invitations.config.ts"],
            cwd=ROOT, env=env, check=True, timeout=100)
        for event in events.values():
            pending = self.get_current(event)
            self.assertEqual("pending", pending["state"])
            self.verify_secret(pending["invitation"]["token"])

    def run_invitation_load(self, event):
        def measure(act):
            start = time.perf_counter()
            act()
            return (time.perf_counter() - start) * 1000

        with ThreadPoolExecutor(12) as executor:
            current = list(executor.map(lambda _: measure(lambda: self.get_current(event)), range(60)))
            issue = list(executor.map(lambda _: measure(lambda: self.issue(event)), range(60)))
            lock = executor.submit(self.admin_sql, f"""
                BEGIN;
                SELECT id FROM scrapbook.events WHERE id = {int(event)} FOR UPDATE;
                SELECT pg_sleep(1.2);
                COMMIT;
                """)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if self.admin_sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event = 'PgSleep';") == "1":
                    break
                time.sleep(0.02)
            else:
                self.fail("A fixture não obteve o lock controlado.")
            waiting = executor.submit(measure, lambda: self.get_current(event))
            lock.result(timeout=5)
            blocked = waiting.result(timeout=5)
        current_p95 = statistics.quantiles(current, n=20)[18]
        issue_p95 = statistics.quantiles(issue, n=20)[18]
        self.assertLessEqual(current_p95, 500)
        self.assertLessEqual(issue_p95, 500)
        print(f"60 consultas p95 {current_p95:.2f} ms, 60 emissões p95 {issue_p95:.2f} ms, "
              f"consulta sob lock controlado {blocked:.2f} ms.", flush=True)

    def prepare_invitation_stack(self):
        self.configure_clock()
        self.set_time(ISSUED)
        self.stack.environment.update(
            SCRAPBOOK_INVITATION_HASH_KEY_ID="fixture-hash", SCRAPBOOK_INVITATION_HASH_KEY=secrets.token_hex(32),
            SCRAPBOOK_INVITATION_ENCRYPTION_KEY_ID="fixture-encryption",
            SCRAPBOOK_INVITATION_ENCRYPTION_KEY=secrets.token_hex(32),
        )
        self.prepare()

    def test_invitation_journey(self):
        self.prepare_invitation_stack()
        first = self.immediate_event(DRAFT["startsAt"])
        self.verify_authorization(first)
        self.verify_rotation(first)
        self.set_time(ISSUED)
        self.verify_exact_expiry(self.immediate_event(DRAFT["startsAt"]))
        self.verify_locked_and_full()
        self.verify_concurrency(self.immediate_event(DRAFT["startsAt"]))
        self.verify_audit_rollback(self.immediate_event(DRAFT["startsAt"]))
        self.verify_entry_headers()
        print("Emissão, substituição, borda, replay, disputa, rollback e privacidade verificados.", flush=True)
        if self.browser:
            self.run_invitation_browser()
        if self.load:
            self.run_invitation_load(self.immediate_event(DRAFT["startsAt"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida emissão de convites no PostgreSQL descartável.")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--load", action="store_true")
    options = parser.parse_args()
    story = InvitationStackTests("test_invitation_journey")
    story.browser = options.browser
    story.load = options.load
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([story])).wasSuccessful())

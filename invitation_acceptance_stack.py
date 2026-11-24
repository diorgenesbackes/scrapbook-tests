import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import statistics
import subprocess
import time
import unittest

from docker_stack import ROOT, available_ports
from event_stack import DRAFT, EVENTS
from invitation_stack import AT_EXPIRY, BEFORE_EXPIRY, ISSUED, PROJECTS, InvitationStackTests


PREVIEW = "/api/v1/invitations/preview"
ACCEPT = "/api/v1/invitations/accept"


class InvitationAcceptanceStackTests(InvitationStackTests):
    def add_unconfirmed_identity(self):
        account = self.stack.app_sql("""
            INSERT INTO scrapbook.accounts (state, subject, name, email, normalized_email, email_verified)
            VALUES ('active', 'fixture-unconfirmed', 'Conta sem confirmação',
                'unconfirmed@example.invalid', 'unconfirmed@example.invalid', FALSE)
            RETURNING id;
            """)
        self.assertTrue(account.isdecimal())
        port, _, _ = available_ports()
        origin = f"http://127.0.0.1:{port}"
        authorization = "Development " + secrets.token_hex(32)
        self.start_identity("unconfirmed", origin, account, authorization)
        return origin, authorization

    def request_invitee(self, path, token, **options):
        return self.request(path, draft={"token": token}, **({"other": True} | options))

    def assert_problem(self, expected, path, token, **options):
        status, headers, problem = self.request_invitee(path, token, **options)
        self.assertEqual(expected, (status, problem["code"]))
        self.assertIn("no-store", {directive.strip() for directive in headers["Cache-Control"].split(",")})
        if token in json.dumps(problem):
            self.fail("A resposta de erro contém o token sintético.")

    def assert_pending(self, event, issued):
        self.assertEqual({"state": "pending", "invitation": issued}, self.get_current(event))
        self.assertEqual("1", self.admin_sql(f"""
            SELECT count(*) FROM scrapbook.event_members WHERE event_members_events_id = {int(event)};
            """))

    def assert_accepted(self, event, invitation, account):
        self.assertEqual(f"accepted|{account}|t|t|t|2", self.admin_sql(f"""
            SELECT i.state, i.invitations_recipient_accounts_id, i.accepted_at IS NOT NULL,
                i.token_ciphertext IS NULL, i.encryption_key_id IS NULL, m.slot
            FROM scrapbook.invitations i
            JOIN scrapbook.event_members m ON m.event_members_events_id = i.invitations_events_id
                AND m.event_members_accounts_id = i.invitations_recipient_accounts_id
            WHERE i.id = {int(invitation)} AND i.invitations_events_id = {int(event)};
            """))
        self.assertEqual("1", self.admin_sql(f"""
            SELECT count(*) FROM scrapbook.audit_entries
            WHERE subject_id = {int(invitation)} AND action = 'invitation.accepted'
                AND audit_entries_accounts_id = {int(account)} AND audit_entries_events_id = {int(event)};
            """))

    def lock_event(self, event):
        self.admin_sql(f"""
            UPDATE scrapbook.events SET locked_at = CURRENT_TIMESTAMP,
                first_record_id = 1, first_record_confirmed_at = CURRENT_TIMESTAMP
            WHERE id = {int(event)};
            """)

    def verify_privacy_and_validation(self, unconfirmed):
        event = self.immediate_event(DRAFT["startsAt"])
        issued = self.issue(event)
        token = issued["token"]
        secret = token.rsplit(".", 1)[1]
        unknown = token[:-1] + ("A" if token[-1] != "A" else "B")
        for path in (PREVIEW, ACCEPT):
            self.assert_problem((401, "IDENTITY_AUTHENTICATION_REQUIRED"), path, token, anonymous=True)
            self.assert_problem((403, "REQUEST_ORIGIN_REJECTED"), path, token,
                headers={"Origin": "https://other.example.invalid"})
            self.assert_problem((403, "INVITATION_ACCOUNT_UNCONFIRMED"), path, token, identity=unconfirmed)
            self.assert_problem((403, "INVITATION_ACCOUNT_UNCONFIRMED"), path, unknown, identity=unconfirmed)
            for invalid in (unknown, "v1.other-environment." + secret, "invalid-token"):
                self.assert_problem((404, "RESOURCE_NOT_FOUND"), path, invalid)
            self.assert_problem((409, "INVITATION_ACCOUNT_ALREADY_LINKED"), path, token, other=False)
            self.assertEqual((400, "REQUEST_VALIDATION_FAILED"), self.problem(path, draft={}, other=True))
        self.assert_pending(event, issued)

    def verify_deadline_and_terminal_states(self):
        deadline_event = self.immediate_event(DRAFT["startsAt"])
        issued = self.issue(deadline_event)
        self.set_time(BEFORE_EXPIRY)
        self.assertEqual("eligible", self.request_invitee(PREVIEW, issued["token"])[2]["state"])
        self.set_time(AT_EXPIRY)
        for path in (PREVIEW, ACCEPT):
            self.assert_problem((409, "INVITATION_UNAVAILABLE"), path, issued["token"])

        event = self.immediate_event(DRAFT["startsAt"])
        first = self.issue(event)
        current = self.issue(event)
        for path in (PREVIEW, ACCEPT):
            self.assert_problem((409, "INVITATION_UNAVAILABLE"), path, first["token"])
        self.add_second_owner(event)
        self.assert_problem((409, "INVITATION_EVENT_FULL"), ACCEPT, current["token"], outsider=True)
        self.lock_event(event)
        self.assert_problem((409, "INVITATION_EVENT_LOCKED"), PREVIEW, current["token"], outsider=True)
        self.assert_problem((409, "INVITATION_EVENT_LOCKED"), ACCEPT, current["token"], outsider=True)

    def verify_competition_and_replay(self):
        event = self.immediate_event(DRAFT["startsAt"])
        issued = self.issue(event)
        token = issued["token"]
        status, headers, body = self.request_invitee(PREVIEW, token)
        self.assertEqual((200, "no-store"), (status, headers["Cache-Control"]))
        self.assertEqual({"state": "eligible", "eventName": DRAFT["name"], "expiresAt": issued["expiresAt"]}, body)
        self.assert_pending(event, issued)
        with ThreadPoolExecutor(2) as executor:
            futures = [executor.submit(self.request_invitee, ACCEPT, token, outsider=outsider) for outsider in (False, True)]
            responses = [future.result() for future in futures]
        self.assertEqual([200, 409], sorted(response[0] for response in responses))
        winner_is_outsider = responses[1][0] == 200
        loser_index = 0 if winner_is_outsider else 1
        self.assertEqual("INVITATION_ALREADY_USED", responses[loser_index][2]["code"])
        self.assert_accepted(event, issued["id"], self.outsider if winner_is_outsider else self.other)
        self.lock_event(event)
        for path in (PREVIEW, ACCEPT):
            self.assertEqual({"state": "accepted", "eventId": event},
                self.request_invitee(path, token, outsider=winner_is_outsider)[2])
            self.assert_problem((409, "INVITATION_ALREADY_USED"), path, token, outsider=not winner_is_outsider)
        listed = self.request(EVENTS, other=True, outsider=winner_is_outsider)[2]["items"]
        self.assertIn(event, {item["id"] for item in listed})
        self.assertEqual(200, self.request(f"{EVENTS}/{event}", other=True, outsider=winner_is_outsider)[0])
        unrelated = self.immediate_event(DRAFT["startsAt"])
        self.assertEqual(404, self.request(f"{EVENTS}/{unrelated}", other=True, outsider=winner_is_outsider)[0])
        audit = self.admin_sql("SELECT before_state || after_state FROM scrapbook.audit_entries WHERE action = 'invitation.accepted';")
        logs = "".join(self.stack.logs(component) for component in ("service", "ui"))
        for suffix in ("other", "outsider"):
            log = subprocess.run(["docker", "logs", self.stack.project + "-" + suffix], capture_output=True,
                text=True, check=True, timeout=15)
            logs += log.stdout + log.stderr
        if token in audit or token in logs:
            self.fail("A credencial sintética apareceu em auditoria ou telemetria.")

    def wait_for_fixture_lock(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.admin_sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event = 'PgSleep';") == "1":
                return
            time.sleep(0.02)
        self.fail("A fixture não obteve o lock controlado.")

    def verify_lock_race(self):
        event = self.immediate_event(DRAFT["startsAt"])
        issued = self.issue(event)
        with ThreadPoolExecutor(2) as executor:
            lock = executor.submit(self.admin_sql, f"""
                BEGIN;
                UPDATE scrapbook.events SET locked_at = CURRENT_TIMESTAMP,
                    first_record_id = 1, first_record_confirmed_at = CURRENT_TIMESTAMP WHERE id = {int(event)};
                SELECT pg_sleep(0.9);
                COMMIT;
                """)
            self.wait_for_fixture_lock()
            self.assert_problem((409, "INVITATION_EVENT_LOCKED"), ACCEPT, issued["token"])
            lock.result(timeout=5)
        self.assertEqual("1", self.admin_sql(f"""
            SELECT count(*) FROM scrapbook.event_members WHERE event_members_events_id = {int(event)};
            """))

    def verify_audit_failure(self):
        event = self.immediate_event(DRAFT["startsAt"])
        issued = self.issue(event)
        self.admin_sql("""
            CREATE FUNCTION scrapbook.fixture_reject_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Falha de auditoria da fixture';
            END;
            $$;
            CREATE TRIGGER fixture_reject_acceptance BEFORE INSERT ON scrapbook.audit_entries
            FOR EACH ROW WHEN (NEW.action = 'invitation.accepted') EXECUTE FUNCTION scrapbook.fixture_reject_acceptance();
            """)
        try:
            self.assert_problem((500, "UNEXPECTED_ERROR"), ACCEPT, issued["token"])
            self.assert_pending(event, issued)
        finally:
            self.admin_sql("DROP TRIGGER fixture_reject_acceptance ON scrapbook.audit_entries;")
        self.assertEqual(200, self.request_invitee(ACCEPT, issued["token"])[0])
        self.assert_accepted(event, issued["id"], self.other)

    def run_acceptance_browser(self):
        events = {}
        for name in PROJECTS:
            status, _, event = self.request(draft=DRAFT | {"name": f"Convite sintético {name}",
                "openingPolicy": {"mode": "immediate", "opensOn": None}})
            self.assertEqual(201, status)
            events[name] = event["id"]
        env = {name: value for name, value in self.stack.environment.items()
            if not name.startswith("SCRAPBOOK_INVITATION_")} | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization, "SCRAPBOOK_TEST_INVITEE_AUTHORIZATION": self.second_authorization,
            "SCRAPBOOK_TEST_INVITEE_ORIGIN": self.second_origin, "SCRAPBOOK_TEST_INVITATION_EVENTS": json.dumps(events)}
        if env.pop("NO_COLOR", None):
            env.update(FORCE_COLOR="0", DEBUG_COLORS="0")
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.invitation-acceptance.config.ts"],
            cwd=ROOT, env=env, check=True, timeout=120)
        for event in events.values():
            self.assertEqual("full", self.get_current(event)["state"])

    def run_acceptance_load(self):
        def timed(path, token):
            start = time.perf_counter()
            status, _, answer = self.request_invitee(path, token)
            self.assertEqual(200, status)
            self.assertEqual("eligible" if path == PREVIEW else "accepted", answer["state"])
            return (time.perf_counter() - start) * 1000

        event = self.immediate_event(DRAFT["startsAt"])
        shared = [self.issue(event)["token"]] * 60
        distinct = [self.issue(self.immediate_event(DRAFT["startsAt"]))["token"] for _ in range(60)]
        with ThreadPoolExecutor(12) as executor:
            for name, tokens in (("mesmo convite", shared), ("eventos distintos", distinct)):
                for path in (PREVIEW, ACCEPT):
                    samples = list(executor.map(lambda token: timed(path, token), tokens))
                    p95 = statistics.quantiles(samples, n=20)[18]
                    self.assertLessEqual(p95, 500)
                    print(f"{name} | {path.rsplit('/', 1)[1]} | {len(samples)} respostas | p95 {p95:.2f} ms", flush=True)

    def test_acceptance_journey(self):
        self.prepare_invitation_stack()
        self.verify_privacy_and_validation(self.add_unconfirmed_identity())
        self.verify_deadline_and_terminal_states()
        self.verify_competition_and_replay()
        self.verify_lock_race()
        self.verify_audit_failure()
        print("Prévia, autorização, vencimento, disputa, trava, rollback e replay verificados.", flush=True)
        if self.browser:
            self.run_acceptance_browser()
        if self.load:
            self.run_acceptance_load()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida o aceite de convites no PostgreSQL descartável.")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--load", action="store_true")
    options = parser.parse_args()
    story = InvitationAcceptanceStackTests("test_acceptance_journey")
    story.browser = options.browser
    story.load = options.load
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([story])).wasSuccessful())

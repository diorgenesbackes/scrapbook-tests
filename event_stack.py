import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import secrets
import subprocess
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from docker_stack import DockerStackTests, ROOT, available_ports


EVENTS = "/api/v1/events"
DRAFT = {"name": "Celebração sintética", "startsAt": "2032-02-29T01:30:00"}


class EventStackTests(unittest.TestCase):
    def setUp(self):
        self.stack = DockerStackTests("test_local_foundation")
        self.stack.setUp()
        self.addCleanup(self.stack.doCleanups)
        self.origin = f"http://127.0.0.1:{self.stack.ui_port}"
        second_port, outsider_port, _ = available_ports()
        self.second_origin = f"http://127.0.0.1:{second_port}"
        self.outsider_origin = f"http://127.0.0.1:{outsider_port}"
        self.authorization = "Development " + secrets.token_hex(32)
        self.second_authorization = "Development " + secrets.token_hex(32)
        self.outsider_authorization = "Development " + secrets.token_hex(32)

    def prepare(self):
        self.stack.compose_command("up", "-d", "--wait", "postgres")
        prepared = self.stack.prepare_database()
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        accounts = self.stack.app_sql("""
            INSERT INTO scrapbook.accounts (state, subject, name, email, normalized_email, email_verified)
            VALUES ('active', 'fixture-first', 'Responsável sintético A', 'owner-a@example.invalid', 'owner-a@example.invalid', TRUE),
                   ('active', 'fixture-second', 'Responsável sintético B', 'owner-b@example.invalid', 'owner-b@example.invalid', TRUE),
                   ('active', 'fixture-outsider', 'Conta sintética sem vínculo', 'outsider@example.invalid', 'outsider@example.invalid', TRUE)
            RETURNING id;
        """).splitlines()
        self.assertTrue(len(accounts) == 3 and all(account.isdecimal() for account in accounts))
        self.owner, self.other, self.outsider = accounts
        self.stack.compose_command("up", "-d", "--build", "--wait", "service", "ui",
                                   env=self.identity(self.owner, self.authorization))
        self.start_identity("other", self.second_origin, self.other, self.second_authorization)
        self.start_identity("outsider", self.outsider_origin, self.outsider, self.outsider_authorization)
        self.stack.wait_for_health("/health/ready", 200, "up")

    def start_identity(self, suffix, origin, account, authorization):
        name = self.stack.project + "-" + suffix
        port = origin.rpartition(":")[2]
        self.stack.compose_command("run", "-d", "--no-deps", "--name", name, "-p", f"127.0.0.1:{port}:8080",
                                   "service", env=self.identity(account, authorization))
        self.addCleanup(self.remove_other, name)
        self.stack.wait_for_health("/health/ready", 200, "up", port=int(port))

    @staticmethod
    def identity(account, authorization):
        return {
            "SCRAPBOOK_DEV_IDENTITY_ENABLED": "true",
            "SCRAPBOOK_DEV_ALLOW_CONTAINER_BINDING": "true",
            "SCRAPBOOK_DEV_ACCOUNT_ID": account,
            "SCRAPBOOK_DEV_IDENTITY_TOKEN": authorization.split()[1],
        }

    def remove_other(self, name):
        result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False, timeout=30)
        self.assertEqual(result.returncode, 0)

    def request(self, path=EVENTS, *, draft=None, raw=None, key=None, other=False, outsider=False,
                anonymous=False, headers=None, method=None, identity=None):
        if identity is not None:
            origin, authorization = identity
        elif outsider:
            origin, authorization = self.outsider_origin, self.outsider_authorization
        elif other:
            origin, authorization = self.second_origin, self.second_authorization
        else:
            origin, authorization = self.origin, self.authorization
        values = {"Accept": "application/json"}
        if not anonymous:
            values["Authorization"] = authorization
        body = raw if raw is not None else json.dumps(draft).encode() if draft is not None else None
        if body is not None:
            values.update({"Origin": origin, "Content-Type": "application/json"})
            if method != "PUT":
                values["Idempotency-Key"] = key or str(uuid4())
        values.update(headers or {})
        try:
            response = urlopen(Request(origin + path, data=body, headers=values, method=method), timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.code, response.headers, json.load(response)

    def verify_isolation_and_validation(self):
        self.assertEqual((401, "IDENTITY_AUTHENTICATION_REQUIRED"), self.problem(EVENTS, anonymous=True))
        self.assertEqual([], self.request()[2]["items"])
        self.assertEqual([], self.request(other=True)[2]["items"])
        self.assertEqual((403, "REQUEST_ORIGIN_REJECTED"),
                         self.problem(EVENTS, draft=DRAFT, headers={"Origin": "https://other.example.invalid"}))
        self.assertEqual((415, "UNSUPPORTED_MEDIA_TYPE"),
                         self.problem(EVENTS, draft=DRAFT, headers={"Content-Type": "text/plain"}))
        self.assertEqual((400, "REQUEST_VALIDATION_FAILED"),
                         self.problem(EVENTS, raw=b'{"name":"duplicado","name":"outro"}'))
        invalid = DRAFT | {"openingPolicy": {"mode": "scheduled", "opensOn": "2032-03-01"}}
        status, headers, response = self.request(draft=invalid)
        self.assertEqual(400, status)
        self.assertIn("openingPolicy.opensOn", response["errors"])
        self.assertIn("no-store", {directive.strip() for directive in headers["Cache-Control"].split(",")})

    def problem(self, path, **options):
        status, _, payload = self.request(path, **options)
        return status, payload["code"]

    def verify_creation(self):
        key = str(uuid4())
        with ThreadPoolExecutor(2) as executor:
            responses = list(executor.map(lambda _: self.request(draft=DRAFT, key=key), range(2)))
        self.assertEqual([200, 201], sorted(status for status, _, _ in responses))
        event_id = responses[0][2]["id"]
        self.assertEqual(event_id, responses[1][2]["id"])
        created = next(response for response in responses if response[0] == 201)
        self.assertTrue(created[1]["Location"].endswith("/" + event_id))
        self.assertEqual("2033-02-28", created[2]["openingPolicy"]["opensOn"])
        self.assertEqual("2032-02-28T23:30:00", created[2]["submissionOpensAt"])
        self.assertEqual("default", created[2]["cover"]["kind"])
        self.assertEqual("Seja bem-vindo! Compartilhe uma lembrança deste evento.", created[2]["welcomeMessage"])
        self.assertEqual((409, "IDEMPOTENCY_KEY_REUSED"),
                         self.problem(EVENTS, draft=DRAFT | {"name": "Outra tentativa"}, key=key))
        self.assertEqual("1|1", self.stack.app_sql(
            "SELECT count(*), count(*) FILTER (WHERE slot = 1) FROM scrapbook.event_members"))
        self.assertEqual(200, self.request(f"{EVENTS}/{event_id}")[0])
        self.assertEqual(self.problem(f"{EVENTS}/9223372036854775807", other=True),
                         self.problem(f"{EVENTS}/{event_id}", other=True))
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(f"{EVENTS}/{event_id}", other=True))

    def seed_for_paging(self):
        with ThreadPoolExecutor(4) as executor:
            statuses = list(executor.map(lambda index: self.request(
                draft=DRAFT | {"name": f"Festa sintética {index}"})[0], range(22)))
        self.assertEqual(statuses, [201] * 22)
        first = self.request(EVENTS + "?pageSize=1")[2]
        cursor = first["nextCursor"]
        self.assertTrue(cursor)
        next_page = EVENTS + "?" + urlencode({"pageSize": 1, "cursor": cursor})
        following = self.request(next_page)[2]
        self.assertNotEqual(first["items"][0]["id"], following["items"][0]["id"])
        self.assertEqual((400, "REQUEST_VALIDATION_FAILED"),
                         self.problem(next_page, other=True))
        self.assertEqual([], self.request(other=True)[2]["items"])

    def admin_sql(self, sql):
        result = self.stack.compose_command("exec", "-T", "postgres", "psql", "-qXAt", "-U", "postgres",
                                            "-d", "scrapbook", "-v", "ON_ERROR_STOP=1", stdin=sql)
        return result.stdout.strip()

    def immediate_event(self, starts_at):
        draft = DRAFT | {"startsAt": starts_at, "openingPolicy": {"mode": "immediate", "opensOn": None}}
        status, _, event = self.request(draft=draft)
        self.assertEqual(201, status)
        self.assertEqual("immediate", event["openingPolicy"]["mode"])
        self.assertTrue(event["id"].isdecimal())
        return event["id"]

    def verify_opening_policy(self):
        event_id = self.immediate_event(DRAFT["startsAt"])
        path = f"{EVENTS}/{event_id}/opening-policy"
        first = {"mode": "scheduled", "opensOn": "2033-02-28", "expectedVersion": "0"}
        second = first | {"opensOn": "2033-03-02"}
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(path, draft=first, method="PUT", other=True))
        self.assertEqual((401, "IDENTITY_AUTHENTICATION_REQUIRED"), self.problem(path, draft=first, method="PUT", anonymous=True))
        self.assertEqual((403, "REQUEST_ORIGIN_REJECTED"),
                         self.problem(path, draft=first, method="PUT", headers={"Origin": "https://other.example.invalid"}))
        self.assertEqual("0", self.admin_sql("SELECT count(*) FROM scrapbook.audit_entries;"))
        self.stack.app_sql(f"""
            INSERT INTO scrapbook.event_members (event_members_events_id, event_members_accounts_id, slot, joined_at)
            VALUES ({event_id}, {self.other}, 2, CURRENT_TIMESTAMP);
        """)
        correlations = [str(uuid4()), str(uuid4())]
        with ThreadPoolExecutor(2) as executor:
            responses = list(executor.map(lambda n: self.request(path, method="PUT", draft=[first, second][n],
                                                                 other=n == 1, headers={"X-Correlation-Id": correlations[n]}),
                                          range(2)))
        self.assertEqual([200, 409], sorted(response[0] for response in responses))
        winner = next(i for i, response in enumerate(responses) if response[0] == 200)
        stale = responses[1 - winner]
        self.assertEqual("EVENT_VERSION_CONFLICT", stale[2]["code"])
        updated = responses[winner][2]
        self.assertEqual("1", updated["version"])
        self.assertEqual("flexible", updated["openingPolicy"]["state"])
        audit = json.loads(self.admin_sql(f"""
            SELECT json_build_object('account', audit_entries_accounts_id::text, 'correlation', correlation_id,
                'before', before_state, 'after', after_state, 'action', action,
                'retained', expires_at = created_at + INTERVAL '4320 hours')
            FROM scrapbook.audit_entries WHERE audit_entries_events_id = {event_id};
        """))
        self.assertEqual([self.owner, self.other][winner], audit["account"])
        self.assertEqual(correlations[winner], audit["correlation"])
        self.assertEqual("event.opening.changed", audit["action"])
        self.assertEqual({"mode": "immediate", "opensOn": None, "version": "1"}, audit["before"])
        self.assertEqual({"mode": "scheduled", "opensOn": updated["openingPolicy"]["opensOn"], "version": "2"}, audit["after"])
        self.assertTrue(audit["retained"])
        unchanged = first | {"opensOn": audit["after"]["opensOn"], "expectedVersion": "1"}
        self.assertEqual("1", self.request(path, draft=unchanged, method="PUT")[2]["version"])
        self.assertEqual("1", self.admin_sql("SELECT count(*) FROM scrapbook.audit_entries;"))
        self.verify_audit_rollback(path, event_id)
        self.verify_after_cutoff(first)
        return event_id

    def verify_audit_rollback(self, path, event_id):
        self.admin_sql("ALTER TABLE scrapbook.audit_entries ADD CONSTRAINT fixture_refuse_audit CHECK (FALSE) NOT VALID;")
        try:
            draft = {"mode": "immediate", "opensOn": None, "expectedVersion": "1"}
            self.assertEqual((500, "UNEXPECTED_ERROR"), self.problem(path, draft=draft, method="PUT"))
        finally:
            self.admin_sql("ALTER TABLE scrapbook.audit_entries DROP CONSTRAINT fixture_refuse_audit;")
        self.assertEqual("scheduled|1|2", self.stack.app_sql(
            f"SELECT opening_mode, version, access_version FROM scrapbook.events WHERE id = {event_id};"))
        self.assertEqual("1", self.admin_sql("SELECT count(*) FROM scrapbook.audit_entries;"))

    def verify_after_cutoff(self, draft):
        expired_id = self.immediate_event("2020-02-29T01:30:00")
        path = f"{EVENTS}/{expired_id}/opening-policy"
        self.assertEqual("immediate_once", self.request(f"{EVENTS}/{expired_id}")[2]["openingPolicy"]["state"])
        locked = self.request(path, draft=draft, method="PUT")
        self.assertEqual((200, "scheduled_locked"), (locked[0], locked[2]["openingPolicy"]["state"]))
        after = {"mode": "immediate", "opensOn": None, "expectedVersion": "1"}
        self.assertEqual((409, "EVENT_OPENING_LOCKED"), self.problem(path, draft=after, method="PUT"))
        self.assertEqual("1", self.admin_sql(f"SELECT count(*) FROM scrapbook.audit_entries WHERE audit_entries_events_id = {expired_id};"))

    def verify_details(self):
        welcome = "Cheguem com alegria! 🎉"
        key = str(uuid4())
        status, _, event = self.request(draft=DRAFT | {"welcomeMessage": "  " + welcome + "  "}, key=key)
        self.assertEqual((201, welcome, False), (status, event["welcomeMessage"], event["usesDefaultWelcomeMessage"]))
        self.assertEqual(200, self.request(draft=DRAFT | {"welcomeMessage": welcome}, key=key)[0])
        self.assertEqual((409, "IDEMPOTENCY_KEY_REUSED"), self.problem(EVENTS, draft=DRAFT, key=key))
        event_id = event["id"]
        self.assertTrue(event_id.isdecimal())
        path = f"{EVENTS}/{event_id}/details"
        public_code = self.stack.app_sql(f"SELECT code FROM scrapbook.events WHERE id = {event_id};")
        self.assertRegex(public_code, "^[a-zA-Z0-9_-]{43}$")
        self.stack.app_sql(f"""
            INSERT INTO scrapbook.event_members (event_members_events_id, event_members_accounts_id, slot, joined_at)
            VALUES ({event_id}, {self.other}, 2, CURRENT_TIMESTAMP);
        """)
        draft = {"name": "Festa atualizada", "startsAt": "2032-03-11T01:30:00",
                 "welcomeMessage": None, "expectedVersion": "0"}
        self.verify_details_access(path, draft)
        without_welcome = {name: value for name, value in draft.items() if name != "welcomeMessage"}
        self.assertEqual((400, "REQUEST_VALIDATION_FAILED"), self.problem(path, draft=without_welcome, method="PUT"))
        incompatible = draft | {"startsAt": "2033-02-27T16:00:00"}
        status, _, errors = self.request(path, draft=incompatible, method="PUT")
        self.assertEqual(400, status)
        self.assertIn("startsAt", errors["errors"])
        with ThreadPoolExecutor(2) as executor:
            responses = list(executor.map(lambda other: self.request(
                path, method="PUT", draft=draft | {"name": f"Festa sintética {'B' if other else 'A'}"},
                other=other), [False, True]))
        self.assertEqual([200, 409], sorted(response[0] for response in responses))
        self.assertEqual("EVENT_VERSION_CONFLICT", next(result[2]["code"] for result in responses if result[0] == 409))
        changed = next(result[2] for result in responses if result[0] == 200)
        self.assertEqual("1", changed["version"])
        self.assertTrue(changed["usesDefaultWelcomeMessage"])
        self.assertEqual("2032-03-10T23:30:00", changed["submissionOpensAt"])
        self.assertEqual("2032-03-12T12:00:00", changed["submissionClosesAt"])
        self.assertEqual("2032-03-12T13:00:00", changed["completionDeadlineAt"])
        self.assertEqual(
            f"{public_code}|{changed['name']}|2032-03-11 01:30:00|2032-03-10 23:30:00|2032-03-12 12:00:00|2032-03-12 13:00:00|1|1|",
            self.stack.app_sql(f"""
                SELECT code, name, starts_at, submission_opens_at, submission_closes_at, completion_deadline_at,
                       version, access_version, welcome_message FROM scrapbook.events WHERE id = {event_id};
            """))
        for other in (True, False):
            status, _, result = self.request(path, method="PUT", other=other, draft=draft | {
                "name": f"Responsável {'B' if other else 'A'}", "expectedVersion": "1" if other else "2",
            })
            self.assertEqual((200, "2" if other else "3"), (status, result["version"]))
        self.verify_details_freeze()
        return event_id, self.verify_structural_lock(draft)

    def verify_details_access(self, path, draft):
        absent = f"{EVENTS}/9223372036854775807/details"
        denied = {"draft": draft, "method": "PUT", "outsider": True}
        self.assertEqual(self.problem(absent, **denied), self.problem(path, **denied))
        status, headers, problem = self.request(path, **denied)
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), (status, problem["code"]))
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertTrue({"code", "title", "status"}.issubset(problem))
        self.assertFalse({"name", "welcomeMessage", "lockedAt", "openingPolicy", "firstRecordId"} & set(problem))
        self.assertEqual((401, "IDENTITY_AUTHENTICATION_REQUIRED"),
                         self.problem(path, draft=draft, method="PUT", anonymous=True))
        self.assertEqual((403, "REQUEST_ORIGIN_REJECTED"),
                         self.problem(path, draft=draft, method="PUT", headers={"Origin": "https://other.example.invalid"}))

    def verify_details_freeze(self):
        event_id = self.immediate_event("2020-02-29T01:30:00")
        path = f"{EVENTS}/{event_id}/details"
        draft = {"name": "Evento com prazo anterior", "startsAt": "2032-02-29T01:30:00",
                 "welcomeMessage": None, "expectedVersion": "0"}
        status, _, result = self.request(path, draft=draft, method="PUT")
        self.assertEqual((200, "immediate_once"), (status, result["openingPolicy"]["state"]))
        self.assertEqual("immediate_once|2020-03-01 12:00:00|1", self.stack.app_sql(f"""
            SELECT access_state, access_frozen_at, access_version FROM scrapbook.events WHERE id = {event_id};
        """))

    def verify_structural_lock(self, draft):
        event_id = self.immediate_event(DRAFT["startsAt"])
        self.admin_sql(f"""
            WITH notice AS (
                INSERT INTO scrapbook.consent_notices (code, body, body_hash, effective_at)
                VALUES ('fixture-lock', 'Termo sintético', decode(repeat('00', 32), 'hex'), CURRENT_TIMESTAMP)
                RETURNING id
            ), record AS (
                INSERT INTO scrapbook.records (
                    records_events_id, records_consent_notices_id, category, format, sender_name, message,
                    expected_file_count, consented_at, submitted_at, received_at, confirmed_at, state
                )
                SELECT {event_id}, id, 'testimonial', 'text', 'Convidado sintético', 'Registro de teste', 0,
                       '2032-02-29 01:30:00', '2032-02-29 01:30:00', '2032-02-29 01:30:00',
                       '2032-02-29 01:30:00', 'confirmed' FROM notice
                RETURNING id, confirmed_at
            )
            UPDATE scrapbook.events SET locked_at = CURRENT_TIMESTAMP,
                first_record_id = record.id, first_record_confirmed_at = record.confirmed_at
            FROM record WHERE scrapbook.events.id = {event_id};
        """)
        path = f"{EVENTS}/{event_id}"
        self.assertEqual((409, "EVENT_LOCKED"), self.problem(path + "/details", draft=draft, method="PUT"))
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(path + "/details", draft=draft, method="PUT", outsider=True))
        self.assertFalse(self.request(path)[2]["canEditDetails"])
        self.assertEqual("0", self.stack.app_sql(f"SELECT version FROM scrapbook.events WHERE id = {event_id};"))
        return event_id

    def run_browser(self, locked_event_id):
        env = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
                                       "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization,
                                       "SCRAPBOOK_TEST_LOCKED_EVENT_ID": locked_event_id}
        if env.pop("NO_COLOR", None):
            env.update(FORCE_COLOR="0", DEBUG_COLORS="0")
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.events.config.ts"],
                       cwd=ROOT, env=env, check=True, timeout=180)

    def run_load(self, opening_event_id, details_event_id):
        env = self.stack.environment | {
            "SCRAPBOOK_LOAD_ORIGIN": self.origin,
            "SCRAPBOOK_LOAD_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_LOAD_SECOND_ORIGIN": self.second_origin,
            "SCRAPBOOK_LOAD_SECOND_AUTHORIZATION": self.second_authorization,
            "SCRAPBOOK_LOAD_OPENING_EVENT_ID": opening_event_id,
            "SCRAPBOOK_LOAD_DETAILS_EVENT_ID": details_event_id,
            "K6_NO_USAGE_REPORT": "true",
        }
        for scenario in ("events", "opening", "details"):
            subprocess.run(["k6", "run", "--quiet", "--no-color", f"load/{scenario}.js"], cwd=ROOT,
                           env=env, check=True, timeout=75)

    def test_event_journey(self):
        self.prepare()
        self.verify_isolation_and_validation()
        self.verify_creation()
        self.seed_for_paging()
        print("Criação concorrente, replay, paginação e isolamento verificados em HTTP e PostgreSQL.", flush=True)
        opening_event = self.verify_opening_policy()
        print("Política verificada com dois vínculos, disputa de versão, auditoria, rollback e trava.", flush=True)
        details_event, locked_event = self.verify_details()
        print("Edição verificada com três contas, gravação atômica, código estável, prazos e travas.", flush=True)
        if self.browser:
            self.run_browser(locked_event)
        if self.load:
            self.run_load(opening_event, details_event)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida a criação e a consulta de eventos em uma stack descartável.")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--load", action="store_true")
    options = parser.parse_args()
    EventStackTests.browser = options.browser
    EventStackTests.load = options.load
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EventStackTests)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

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
        self.second_origin = f"http://127.0.0.1:{available_ports()[0]}"
        self.authorization = "Development " + secrets.token_hex(32)
        self.second_authorization = "Development " + secrets.token_hex(32)

    def prepare(self):
        self.stack.compose_command("up", "-d", "--wait", "postgres")
        prepared = self.stack.prepare_database()
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        accounts = self.stack.app_sql("""
            INSERT INTO scrapbook.accounts (state, subject, name, email, normalized_email, email_verified)
            VALUES ('active', 'fixture-first', 'Responsável sintético A', 'owner-a@example.invalid', 'owner-a@example.invalid', TRUE),
                   ('active', 'fixture-second', 'Responsável sintético B', 'owner-b@example.invalid', 'owner-b@example.invalid', TRUE)
            RETURNING id;
        """).splitlines()
        self.assertTrue(len(accounts) == 2 and all(account.isdecimal() for account in accounts))
        self.owner, other = accounts
        self.stack.compose_command("up", "-d", "--build", "--wait", "service", "ui",
                                   env=self.identity(self.owner, self.authorization))
        name = self.stack.project + "-other"
        port = self.second_origin.rpartition(":")[2]
        self.stack.compose_command("run", "-d", "--no-deps", "--name", name, "-p", f"127.0.0.1:{port}:8080",
                                   "service", env=self.identity(other, self.second_authorization))
        self.addCleanup(self.remove_other, name)
        self.stack.wait_for_health("/health/ready", 200, "up", port=int(port))
        self.stack.wait_for_health("/health/ready", 200, "up")

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

    def request(self, path=EVENTS, *, draft=None, raw=None, key=None, other=False, anonymous=False, headers=None):
        origin = self.second_origin if other else self.origin
        authorization = self.second_authorization if other else self.authorization
        values = {"Accept": "application/json"}
        if not anonymous:
            values["Authorization"] = authorization
        body = raw if raw is not None else json.dumps(draft).encode() if draft is not None else None
        if body is not None:
            values.update({"Origin": origin, "Content-Type": "application/json", "Idempotency-Key": key or str(uuid4())})
        values.update(headers or {})
        try:
            response = urlopen(Request(origin + path, data=body, headers=values), timeout=10)
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

    def run_browser(self):
        env = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
                                       "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization}
        if env.pop("NO_COLOR", None):
            env.update(FORCE_COLOR="0", DEBUG_COLORS="0")
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.events.config.ts"],
                       cwd=ROOT, env=env, check=True, timeout=180)

    def run_load(self):
        env = self.stack.environment | {
            "SCRAPBOOK_LOAD_ORIGIN": self.origin,
            "SCRAPBOOK_LOAD_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_LOAD_SECOND_ORIGIN": self.second_origin,
            "SCRAPBOOK_LOAD_SECOND_AUTHORIZATION": self.second_authorization,
            "K6_NO_USAGE_REPORT": "true",
        }
        subprocess.run(["k6", "run", "--quiet", "--no-color", "load/events.js"], cwd=ROOT, env=env,
                       check=True, timeout=75)

    def test_event_journey(self):
        self.prepare()
        self.verify_isolation_and_validation()
        self.verify_creation()
        self.seed_for_paging()
        print("Criação concorrente, replay, paginação e isolamento verificados em HTTP e PostgreSQL.", flush=True)
        if self.browser:
            self.run_browser()
        if self.load:
            self.run_load()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida a criação e a consulta de eventos em uma stack descartável.")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--load", action="store_true")
    options = parser.parse_args()
    EventStackTests.browser = options.browser
    EventStackTests.load = options.load
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EventStackTests)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

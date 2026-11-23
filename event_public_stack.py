import argparse
from datetime import datetime
import subprocess
import unittest

from docker_stack import ROOT
from event_access_stack import EventAccessStackTests
from event_stack import EVENTS


PUBLIC = "/api/v1/public-events"
START = "2032-02-29T01:00:00"
BEFORE = "2032-02-29T01:59:58.000Z"
FIELDS = {"name", "startsAt", "timeZone", "cover", "welcomeMessage", "submissionWindow", "serverTime"}
WINDOW_FIELDS = {"state", "opensAt", "closesAt", "completionDeadlineAt", "nextTransitionAt"}


class EventPublicStackTests(EventAccessStackTests):
    def create_event(self, name, welcome=None):
        status, _, event = self.request(draft={"name": name, "startsAt": START, "welcomeMessage": welcome,
            "openingPolicy": {"mode": "immediate", "opensOn": None}})
        self.assertEqual(201, status)
        event_id = int(event["id"])
        code = self.stack.app_sql(f"SELECT code FROM scrapbook.events WHERE id = {event_id};")
        self.assertRegex(code, r"^[A-Za-z0-9_-]{43}$")
        return event, code

    def read_public(self, code, options=None):
        status, headers, body = self.request(PUBLIC + "/" + code, **(options or {"anonymous": True}))
        self.assertEqual(200, status)
        self.assertEqual("no-store", headers.get("Cache-Control"))
        self.assertEqual(FIELDS, set(body))
        self.assertEqual({"kind": "default"}, body["cover"])
        self.assertEqual(WINDOW_FIELDS, set(body["submissionWindow"]))
        return body

    def verify_window(self, code):
        for instant, state, transition in (
            (BEFORE, "before", "2032-02-29T02:00:00Z"),
            ("2032-02-29T02:00:00.000Z", "open", "2032-03-01T15:00:00Z"),
            ("2032-03-01T14:59:59.999Z", "open", "2032-03-01T15:00:00Z"),
            ("2032-03-01T15:00:00.000Z", "closed", None),
            ("2032-03-01T16:00:00.000Z", "closed", None),
        ):
            with self.subTest(instant=instant):
                self.set_time(instant)
                data = self.read_public(code)
                self.assertEqual("America/Sao_Paulo", data["timeZone"])
                self.assertEqual(datetime.fromisoformat(instant), datetime.fromisoformat(data["serverTime"]))
                window = data["submissionWindow"]
                self.assertEqual((state, "2032-02-28T23:00:00", "2032-03-01T12:00:00", "2032-03-01T13:00:00"),
                    tuple(window[key] for key in ("state", "opensAt", "closesAt", "completionDeadlineAt")))
                actual = window["nextTransitionAt"]
                self.assertEqual(datetime.fromisoformat(transition) if transition else None,
                    datetime.fromisoformat(actual) if actual else None)

    def verify_privacy(self, code):
        self.assertEqual(self.read_public(code), self.read_public(code, {"outsider": True}))
        unknown = ("B" if code.startswith("A") else "A") + code[1:]
        for path in (PUBLIC, PUBLIC + "/invalido", PUBLIC + "/" + unknown):
            status, headers, problem = self.request(path, anonymous=True)
            self.assertEqual((404, "RESOURCE_NOT_FOUND", "Evento indisponível."), (status, problem["code"], problem["title"]))
            self.assertIn("no-store", {directive.strip() for directive in headers["Cache-Control"].split(",")})
            self.assertEqual(headers.get("X-Correlation-Id"), problem["correlationId"])
        status, headers, _ = self.stack.get("/e/" + code + "?compartilhado=1")
        self.assertEqual(200, status)
        self.assertEqual("noindex, nofollow", headers.get("X-Robots-Tag"))
        self.assertEqual("no-referrer", headers.get("Referrer-Policy"))
        self.assertEqual("DENY", headers.get("X-Frame-Options"))
        self.assertIsNone(self.stack.get("/")[1].get("X-Robots-Tag"))
        logs = self.stack.logs("service") + self.stack.logs("ui")
        self.assertRegex(logs, r"api/v1/public-events/\{code\?\}")
        for secret in (code, "Remetente sintético", "Lembrança sintética", "Boas-vindas sintéticas."):
            if secret in logs:
                self.fail("A telemetria contém um dado da fixture que deve permanecer fora dos logs.")

    def verify_edited_event(self, event, code):
        self.set_time(BEFORE)
        edit = {"name": "Celebração atualizada", "startsAt": "2032-03-02T20:00:00",
                "welcomeMessage": "Mensagem atualizada", "expectedVersion": event["version"]}
        status, _, _ = self.request(f"{EVENTS}/{event['id']}/details", draft=edit, method="PUT")
        self.assertEqual(200, status)
        data = self.read_public(code)
        self.assertEqual((edit["name"], edit["welcomeMessage"], edit["startsAt"]),
            (data["name"], data["welcomeMessage"], data["startsAt"]))
        self.assertEqual("2032-03-02T18:00:00", data["submissionWindow"]["opensAt"])
        self.assertEqual("2032-03-03T12:00:00", data["submissionWindow"]["closesAt"])
        self.assertEqual("2032-03-03T13:00:00", data["submissionWindow"]["completionDeadlineAt"])

    def verify_share_link(self, event, code):
        path = f"{EVENTS}/{event['id']}/public-link"
        missing = f"{EVENTS}/9223372036854775807/public-link"
        self.assertEqual((401, "IDENTITY_AUTHENTICATION_REQUIRED"), self.problem(path, anonymous=True))
        self.assertEqual((401, "IDENTITY_AUTHENTICATION_REQUIRED"),
            self.problem(path, headers={"Authorization": "Bearer expired-fixture"}))
        for denied_path in (path, missing):
            self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(denied_path, outsider=True))
        for other in (False, True):
            status, headers, result = self.request(path, other=other)
            self.assertEqual((200, {"code": code}), (status, result))
            self.assertEqual("no-store", headers.get("Cache-Control"))
        logs = self.stack.logs("service") + self.stack.logs("ui")
        if code in logs:
            self.fail("A telemetria contém o código de compartilhamento da fixture.")

    def run_public_browser(self, event, code, *, performance=False):
        env = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": code, "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file),
            "SCRAPBOOK_TEST_PUBLIC_EVENT_ID": event["id"], "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_TEST_PUBLIC_4G": str(performance).lower()}
        if env.pop("NO_COLOR", None):
            env.update(FORCE_COLOR="0", DEBUG_COLORS="0")
        command = ["npm", "exec", "--", "playwright", "test", "--config", "playwright.public-event.config.ts"]
        if performance:
            command.extend(("--project", "android-emulado"))
        subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=180)

    def run_public_load(self, first, second):
        self.set_time(BEFORE)
        env = self.stack.environment | {"SCRAPBOOK_LOAD_ORIGIN": self.origin,
            "SCRAPBOOK_LOAD_PUBLIC_CODE_A": first, "SCRAPBOOK_LOAD_PUBLIC_CODE_B": second, "K6_NO_USAGE_REPORT": "true"}
        try:
            subprocess.run(["k6", "run", "--quiet", "--no-color", "load/public-event.js"], cwd=ROOT, env=env,
                check=True, timeout=75)
        except subprocess.CalledProcessError:
            logs = self.stack.logs("postgres").lower()
            exhausted = sum(logs.count(text) for text in ("remaining connection slots", "too many clients already"))
            print(f"Recusas de conexão do PostgreSQL por limite de slots: {exhausted}.", flush=True)
            raise

    def test_public_journey(self):
        self.configure_clock()
        self.set_time(BEFORE)
        self.prepare()
        first_event, first = self.create_event("Celebração pública sintética", "Boas-vindas sintéticas.")
        second_event, second = self.create_event("Outro evento sintético")
        self.add_second_owner(second_event["id"])
        self.seed_protected_record(int(second_event["id"]))
        self.verify_window(first)
        self.verify_privacy(second)
        self.verify_share_link(second_event, second)
        self.verify_edited_event(second_event, second)
        self.verify_share_link(second_event, second)
        print("Consulta anônima, janelas, vínculo, edição pelo mesmo código e privacidade verificados.", flush=True)
        if self.browser:
            self.run_public_browser(first_event, first)
        if self.load:
            self.run_public_browser(first_event, first, performance=True)
            self.run_public_load(first, second)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida a página dos convidados com PostgreSQL e relógio controlado.")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--load", action="store_true")
    options = parser.parse_args()
    tests = EventPublicStackTests("test_public_journey")
    tests.browser = options.browser
    tests.load = options.load
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([tests])).wasSuccessful())

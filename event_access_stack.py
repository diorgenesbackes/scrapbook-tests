import argparse
from datetime import datetime
import json
import os
import shutil
import subprocess
import unittest

from docker_stack import ROOT
from event_stack import DRAFT, EVENTS, EventStackTests


BEFORE = "2028-01-02T02:59:59.999Z"
OPENING = "2028-01-02T03:00:00Z"
STATUS_FIELDS = {"contentState", "opensOn", "opensAt", "serverTime"}


class EventAccessStackTests(EventStackTests):
    def configure_clock(self):
        directory = self.stack.env_file.parent
        clock = directory / "clock"
        clock.mkdir(mode=0o755)
        self.clock_file = clock / "now"
        self.set_time(BEFORE)

        source = ROOT.parent / "scrapbook-service"
        context = directory / "service-clock"
        context.mkdir()
        for name in ("Dockerfile", ".dockerignore", "global.json", "Directory.Build.props"):
            shutil.copy2(source / name, context / name)
        for project in ("Api", "Application", "Domain", "Infrastructure"):
            name = "ScrapBook." + project
            shutil.copytree(source / name, context / name, ignore=shutil.ignore_patterns("bin", "obj"))
        program = context / "ScrapBook.Api" / "Program.cs"
        contents = program.read_text(encoding="utf-8")
        registration = "builder.Services.AddSingleton(TimeProvider.System);"
        self.assertEqual(1, contents.count(registration))
        program.write_text(contents.replace(registration,
            "builder.Services.AddSingleton<TimeProvider, EventFixtureTimeProvider>();"), encoding="utf-8")
        shutil.copy2(ROOT / "fixtures" / "EventFixtureTimeProvider.cs", program.parent)

        override = directory / "clock.compose.json"
        override.write_text(json.dumps({"services": {"service": {
            "build": {"context": str(context)},
            "volumes": [{"type": "bind", "source": str(clock), "target": "/test-clock", "read_only": True}],
        }}}), encoding="utf-8")
        self.stack.compose.extend(("--file", str(override)))

    def set_time(self, value):
        instant = datetime.fromisoformat(value).isoformat(timespec="milliseconds").replace("+00:00", "Z").encode("ascii")
        if self.clock_file.exists():
            with self.clock_file.open("r+b", buffering=0) as handle:
                handle.write(instant)
                os.fsync(handle.fileno())
        else:
            self.clock_file.write_bytes(instant)
            self.clock_file.chmod(0o644)

    def scheduled_event(self, date="2028-01-02", start="2027-12-28T15:00:00"):
        status, _, event = self.request(draft=DRAFT | {"startsAt": start,
            "openingPolicy": {"mode": "scheduled", "opensOn": date}})
        self.assertEqual(201, status)
        self.assertTrue(event["id"].isdecimal())
        return event["id"]

    def add_second_owner(self, event_id):
        self.stack.app_sql(f"""
            INSERT INTO scrapbook.event_members (event_members_events_id, event_members_accounts_id, slot, joined_at)
            VALUES ({event_id}, {self.other}, 2, CURRENT_TIMESTAMP);
        """)

    def check_status(self, event_id, expected, now, *, date=None, instant=None, other=False):
        status, headers, body = self.request(f"{EVENTS}/{event_id}/opening-status", other=other)
        if status != 200:
            log = self.stack.compose_command("logs", "--no-color", "--tail", "20", "service").stdout
            fixture_errors = [line for line in log.splitlines() if "Relógio da fixture:" in line]
            self.fail(f"Consulta de abertura retornou {status} ({body.get('code')}). {fixture_errors}")
        self.assertIn("no-store", {value.strip() for value in headers["Cache-Control"].split(",")})
        self.assertEqual(STATUS_FIELDS, set(body))
        self.assertEqual(expected, body["contentState"])
        self.assertEqual(date, body["opensOn"])
        self.assertEqual(datetime.fromisoformat(now), datetime.fromisoformat(body["serverTime"]))
        if instant is None:
            self.assertIsNone(body["opensAt"])
        else:
            self.assertEqual(datetime.fromisoformat(instant), datetime.fromisoformat(body["opensAt"]))
        detail = self.request(f"{EVENTS}/{event_id}", other=other)[2]
        self.assertEqual(expected, detail["openingPolicy"]["contentState"])

    def seed_protected_record(self, event_id):
        self.admin_sql(f"""
            WITH notice AS (
                INSERT INTO scrapbook.consent_notices (code, body, body_hash, effective_at)
                VALUES ('fixture-opening', 'Termo sintético', decode(repeat('00', 32), 'hex'), CURRENT_TIMESTAMP)
                RETURNING id
            )
            INSERT INTO scrapbook.records (
                records_events_id, records_consent_notices_id, category, format, sender_name, message,
                expected_file_count, consented_at, submitted_at, received_at, confirmed_at, state
            )
            SELECT {event_id}, id, 'testimonial', 'text', 'Remetente sintético', 'Lembrança sintética', 0,
                   '2027-12-28 15:00:00', '2027-12-28 15:00:00', '2027-12-28 15:00:00',
                   '2027-12-28 15:00:00', 'confirmed' FROM notice;
        """)

    def verify_boundary(self):
        event_id = self.scheduled_event()
        self.seed_protected_record(event_id)
        self.add_second_owner(event_id)
        path = f"{EVENTS}/{event_id}/opening-status"
        unknown = f"{EVENTS}/9223372036854775807/opening-status"
        denied = self.problem(unknown, outsider=True)
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), denied)
        self.assertEqual(denied, self.problem(path, outsider=True))
        self.assertEqual((401, "IDENTITY_AUTHENTICATION_REQUIRED"), self.problem(path, anonymous=True))

        self.check_status(event_id, "closed", BEFORE, date="2028-01-02", instant=OPENING)
        self.check_status(event_id, "closed", BEFORE, date="2028-01-02", instant=OPENING, other=True)
        self.set_time(OPENING)
        self.check_status(event_id, "open", OPENING, date="2028-01-02", instant=OPENING)
        self.check_status(event_id, "open", OPENING, date="2028-01-02", instant=OPENING, other=True)
        self.assertEqual(denied, self.problem(path, outsider=True))
        self.assertEqual("0", self.admin_sql("SELECT count(*) FROM scrapbook.opening_dispatches;"))

    def verify_current_policy_and_membership(self):
        event_id = self.immediate_event(DRAFT["startsAt"])
        self.add_second_owner(event_id)
        self.check_status(event_id, "open", OPENING, other=True)
        draft = {"mode": "scheduled", "opensOn": "2033-03-02", "expectedVersion": "0"}
        status, _, _ = self.request(f"{EVENTS}/{event_id}/opening-policy", draft=draft, method="PUT")
        self.assertEqual(200, status)
        self.check_status(event_id, "closed", OPENING, date="2033-03-02", instant="2033-03-02T03:00:00Z", other=True)
        self.assertEqual("scheduled|2033-03-02 00:00:00|1", self.admin_sql(f"""
            SELECT opening_mode, opens_at, version FROM scrapbook.events WHERE id = {event_id};
        """))
        self.admin_sql(f"""
            DELETE FROM scrapbook.event_members WHERE event_members_events_id = {event_id}
                AND event_members_accounts_id = {self.other};
        """)
        self.assertEqual((404, "RESOURCE_NOT_FOUND"), self.problem(f"{EVENTS}/{event_id}/opening-status", other=True))

    def run_access_browser(self):
        env = self.stack.environment | {
            "PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file),
        }
        if env.pop("NO_COLOR", None):
            env.update(FORCE_COLOR="0", DEBUG_COLORS="0")
        subprocess.run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.access.config.ts"],
                       cwd=ROOT, env=env, check=True, timeout=180)

    def run_access_load(self):
        self.set_time(OPENING)
        open_event = self.immediate_event(DRAFT["startsAt"])
        closed_event = self.scheduled_event("2033-03-02", DRAFT["startsAt"])
        for event in (open_event, closed_event):
            self.add_second_owner(event)
        env = self.stack.environment | {
            "SCRAPBOOK_LOAD_ORIGIN": self.origin,
            "SCRAPBOOK_LOAD_AUTHORIZATION": self.authorization,
            "SCRAPBOOK_LOAD_SECOND_ORIGIN": self.second_origin,
            "SCRAPBOOK_LOAD_SECOND_AUTHORIZATION": self.second_authorization,
            "SCRAPBOOK_LOAD_OPEN_EVENT_ID": open_event,
            "SCRAPBOOK_LOAD_CLOSED_EVENT_ID": closed_event,
            "K6_NO_USAGE_REPORT": "true",
        }
        subprocess.run(["k6", "run", "--quiet", "--no-color", "load/opening-status.js"], cwd=ROOT, env=env,
                       check=True, timeout=75)

    def test_access_journey(self):
        self.configure_clock()
        self.prepare()
        self.verify_boundary()
        self.verify_current_policy_and_membership()
        print("Abertura na meia-noite de Brasília, reconsulta da política e dos vínculos e contrato sem cache verificados.", flush=True)
        if self.browser:
            self.run_access_browser()
        if self.load:
            self.run_access_load()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida o estado de abertura com relógio controlado em uma stack descartável.")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--load", action="store_true")
    options = parser.parse_args()
    tests = EventAccessStackTests("test_access_journey")
    tests.browser = options.browser
    tests.load = options.load
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([tests])).wasSuccessful())

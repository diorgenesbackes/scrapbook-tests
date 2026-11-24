import os
import subprocess
import unittest

from docker_stack import ROOT
from event_public_stack import EventPublicStackTests


class GuestIdentificationStackTests(EventPublicStackTests):
    def test_first_identification(self):
        self.configure_clock()
        self.set_time("2032-02-29T04:00:00Z")
        self.prepare()
        _, first = self.create_event("Primeiro evento sintético F06")
        _, second = self.create_event("Segundo evento sintético F06")
        for code, name in ((first, "Primeiro evento sintético F06"), (second, "Segundo evento sintético F06")):
            event = self.read_public(code)
            self.assertEqual((name, "open"), (event["name"], event["submissionWindow"]["state"]))

        environment = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first, "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second}
        command = ["npm", "exec", "--", "playwright", "test", "--config", "playwright.guest-identification.config.ts"]
        with self.subTest(mode="browser"):
            subprocess.run(command, cwd=ROOT, env=environment, check=True, timeout=180)
        with self.subTest(mode="4g"):
            subprocess.run(command + ["--project", "android-emulado"], cwd=ROOT,
                           env=environment | {"SCRAPBOOK_TEST_IDENTIFICATION_4G": "true"}, check=True, timeout=90)

        self.assertEqual("0", self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        logs = self.stack.logs("service") + self.stack.logs("ui")
        if any(marker in logs for marker in (
            "Convidada sintética da F06", "Convidada%20sint%C3%A9tica%20da%20F06", "f06-local-v1",
        )):
            self.fail("Os dados da identificação sintética apareceram nos logs.")
        print("Contexto anônimo, registros e telemetria verificados no Docker.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    suite = unittest.TestSuite([GuestIdentificationStackTests("test_first_identification")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

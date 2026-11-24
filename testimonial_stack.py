import os
import subprocess
import unittest

from docker_stack import ROOT
from event_public_stack import EventPublicStackTests


class TextTestimonialStackTests(EventPublicStackTests):
    def test_text_testimonial(self):
        self.configure_clock()
        self.set_time("2032-02-29T04:00:00Z")
        self.prepare()
        first_event, first = self.create_event("Celebração pública sintética", "Boas-vindas sintéticas.")
        _, second = self.create_event("Segundo evento sintético F07")
        self.assertEqual("open", self.read_public(first)["submissionWindow"]["state"])
        self.assertEqual("open", self.read_public(second)["submissionWindow"]["state"])

        environment = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first, "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file)}
        command = ["npm", "exec", "--", "playwright", "test", "--config", "playwright.testimonial.config.ts"]
        with self.subTest(mode="browser"):
            subprocess.run(command, cwd=ROOT, env=environment, check=True, timeout=180)
        with self.subTest(mode="4g"):
            subprocess.run(command + ["--project", "android-emulado"], cwd=ROOT,
                           env=environment | {"SCRAPBOOK_TEST_TESTIMONIAL_4G": "true"}, check=True, timeout=90)
        with self.subTest(mode="public-event-regression"):
            self.run_public_browser(first_event, first)

        self.assertEqual("0", self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        logs = self.stack.logs("service") + self.stack.logs("ui")
        if any(marker in logs for marker in ("Depoimento sintético da F07", "Nome sintético da F07", "f06-local-v1")):
            self.fail("O rascunho ou os dados da identificação apareceram nos logs da stack.")
        print("Janela, registros e telemetria verificados no Docker.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    suite = unittest.TestSuite([TextTestimonialStackTests("test_text_testimonial")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

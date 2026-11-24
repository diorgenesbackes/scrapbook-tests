import os
import subprocess
import unittest

from docker_stack import ROOT
from event_public_stack import EventPublicStackTests


class MediaContributionStackTests(EventPublicStackTests):
    def test_media(self):
        self.configure_clock()
        self.set_time("2032-02-29T04:00:00Z")
        self.prepare()
        first_event, first = self.create_event("Celebração pública sintética", "Boas-vindas sintéticas.")
        _, second = self.create_event("Segundo evento sintético F07")
        self.assertEqual("open", self.read_public(first)["submissionWindow"]["state"])
        self.assertEqual("open", self.read_public(second)["submissionWindow"]["state"])

        env = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first, "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file)}
        command = ["npm", "exec", "--", "playwright", "test", "--config"]
        for mode, config in (("media", "playwright.media.config.ts"), ("photos", "playwright.photos.config.ts"),
                             ("text", "playwright.testimonial.config.ts")):
            with self.subTest(mode=mode):
                subprocess.run(command + [config], cwd=ROOT, env=env, check=True, timeout=240)
        with self.subTest(mode="4g"):
            subprocess.run(command + ["playwright.media.config.ts", "--project", "android-emulado"], cwd=ROOT,
                env=env | {"SCRAPBOOK_TEST_MEDIA_4G": "true"}, check=True, timeout=120)
        with self.subTest(mode="public-regression"):
            self.run_public_browser(first_event, first)

        self.assertEqual("0", self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        logs = self.stack.logs("service") + self.stack.logs("ui")
        if any(marker in logs for marker in ("vídeo-privado.mp4", "Legenda privada de mídia", "Convidada sintética das fotos")):
            self.fail("Dados da composição sintética apareceram nos logs.")
        print("Janela, registros e telemetria verificados no Docker.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    suite = unittest.TestSuite([MediaContributionStackTests("test_media")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

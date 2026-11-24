import os
import subprocess
import unittest

from docker_stack import ROOT
from event_public_stack import EventPublicStackTests


class PhotoCameraStackTests(EventPublicStackTests):
    def test_camera(self):
        self.configure_clock()
        self.set_time("2032-02-29T04:00:00Z")
        self.prepare()
        first_event, first = self.create_event("Celebração pública sintética", "Boas-vindas sintéticas.")
        _, second = self.create_event("Segundo evento sintético com câmera")
        self.assertEqual("open", self.read_public(first)["submissionWindow"]["state"])
        self.assertEqual("open", self.read_public(second)["submissionWindow"]["state"])

        env = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first, "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file)}
        command = ["npm", "exec", "--", "playwright", "test", "--config"]
        for config in ("camera", "photos", "media", "testimonial"):
            with self.subTest(config=config):
                subprocess.run(command + [f"playwright.{config}.config.ts"], cwd=ROOT, env=env, check=True, timeout=240)
        with self.subTest(mode="4g"):
            subprocess.run(command + ["playwright.camera.config.ts", "--project", "android-emulado"], cwd=ROOT,
                env=env | {"SCRAPBOOK_TEST_CAMERA_4G": "true"}, check=True, timeout=120)
        with self.subTest(mode="public-regression"):
            self.run_public_browser(first_event, first)

        self.assertEqual("0", self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        logs = self.stack.logs("service") + self.stack.logs("ui")
        for marker in ("Foto da câmera", "Legenda de câmera sintética", first, second):
            if marker in logs:
                self.fail("Dados da captura sintética apareceram nos logs.")
        print("Janela, registros e telemetria da câmera verificados no Docker.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    suite = unittest.TestSuite([PhotoCameraStackTests("test_camera")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

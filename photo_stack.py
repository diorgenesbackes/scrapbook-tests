import os
import random
import struct
import subprocess
import unittest
import zlib

from docker_stack import ROOT
from event_public_stack import EventPublicStackTests


def photo_fixture(path):
    width, height = 640, 480
    random_bytes = random.Random(7)
    rows = b"".join(b"\0" + random_bytes.randbytes(width * 3) for _ in range(height))

    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    header = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class PhotoContributionStackTests(EventPublicStackTests):
    def test_photos(self):
        self.configure_clock()
        self.set_time("2032-02-29T04:00:00Z")
        first_image = self.stack.env_file.parent / "performance.png"
        photo_fixture(first_image)
        self.prepare()
        first_event, first = self.create_event("Celebração pública sintética", "Boas-vindas sintéticas.")
        _, second = self.create_event("Segundo evento sintético F07")
        self.assertEqual("open", self.read_public(first)["submissionWindow"]["state"])
        self.assertEqual("open", self.read_public(second)["submissionWindow"]["state"])

        env = self.stack.environment | {"PLAYWRIGHT_BASE_URL": self.origin,
            "SCRAPBOOK_TEST_PUBLIC_CODE": first, "SCRAPBOOK_TEST_SECOND_PUBLIC_CODE": second,
            "SCRAPBOOK_TEST_CLOCK_FILE": str(self.clock_file)}
        command = ["npm", "exec", "--", "playwright", "test", "--config"]
        with self.subTest(mode="browser"):
            subprocess.run(command + ["playwright.photos.config.ts"], cwd=ROOT, env=env, check=True, timeout=180)
        with self.subTest(mode="4g"):
            subprocess.run(command + ["playwright.photos.config.ts", "--project", "android-emulado"], cwd=ROOT, check=True,
                env=env | {"SCRAPBOOK_TEST_PHOTOS_4G": "true", "SCRAPBOOK_TEST_PHOTO_PERFORMANCE_FILE": str(first_image)}, timeout=105)
        with self.subTest(mode="text-regression"):
            subprocess.run(command + ["playwright.testimonial.config.ts"], cwd=ROOT, env=env, check=True, timeout=180)
        with self.subTest(mode="public-regression"):
            self.run_public_browser(first_event, first)

        self.assertEqual("0", self.stack.app_sql("SELECT count(*) FROM scrapbook.records;"))
        logs = self.stack.logs("service") + self.stack.logs("ui")
        if any(marker in logs for marker in ("arquivo-privado.png", "Legenda privada de teste", "Convidada sintética das fotos")):
            self.fail("Nome, arquivo ou legenda sintética apareceu nos logs.")
        print("Janela, registros e telemetria verificados no Docker.", flush=True)


if __name__ == "__main__":
    os.environ.pop("NO_COLOR", None)
    suite = unittest.TestSuite([PhotoContributionStackTests("test_photos")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

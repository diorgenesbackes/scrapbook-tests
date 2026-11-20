import json
import unittest

from check_hlg import HlgVerificationError, verify_hlg


UI = "https://ui.hlg.example.test"
API = "https://api.hlg.example.test"
RELEASE = {"sha": "a" * 40, "version": "v1.0.0"}


class HlgVerificationTests(unittest.TestCase):
    def setUp(self):
        shell = (
            200, {"Content-Type": "text/html; charset=utf-8", "Referrer-Policy": "no-referrer",
                  "X-Content-Type-Options": "nosniff",
                  "Content-Security-Policy": f"connect-src 'self' {API}; frame-ancestors 'none'"},
            b"<html><body><div id='root'></div></body></html>",
        )
        self.responses = {
            UI + "/": shell,
            UI + "/meus-eventos": shell,
            UI + "/release.json": (200, {}, json.dumps(RELEASE).encode()),
            API + "/health": (403, {}, b""),
            API + "/health/ready": (404, {}, b""),
        }

    def verify(self):
        verify_hlg(ui=UI, api=API, fetch=self.responses.__getitem__, **RELEASE)

    def test_known_release_and_operational_routes(self):
        self.verify()

    def test_fallback_requires_security_headers(self):
        self.responses[UI + "/meus-eventos"] = (200, {"Content-Type": "text/html"}, b"<html/>")
        with self.assertRaisesRegex(HlgVerificationError, "cabeçalhos"):
            self.verify()

    def test_different_artifact_is_rejected(self):
        self.responses[UI + "/release.json"] = (200, {}, b"{}")
        with self.assertRaisesRegex(HlgVerificationError, "artefato ativo"):
            self.verify()

    def test_public_readiness_is_rejected(self):
        self.responses[API + "/health/ready"] = (200, {}, b"")
        with self.assertRaisesRegex(HlgVerificationError, "rota operacional"):
            self.verify()


if __name__ == "__main__":
    unittest.main()

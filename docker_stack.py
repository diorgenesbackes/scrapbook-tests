import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
INFRASTRUCTURE = ROOT.parent / "scrapbook-infrastructure"


def available_ports():
    with socket.socket() as postgres, socket.socket() as service, socket.socket() as ui:
        sockets = (postgres, service, ui)
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return tuple(listener.getsockname()[1] for listener in sockets)


class DockerStackTests(unittest.TestCase):
    def setUp(self):
        self.project = "scrapbook-f01-" + secrets.token_hex(5)
        self.database_port, self.api_port, self.ui_port = available_ports()
        self.password = secrets.token_hex(32)
        self.environment = {name: value for name, value in os.environ.items() if not name.startswith("SCRAPBOOK_")}
        temporary = tempfile.TemporaryDirectory(prefix="scrapbook-f01-")
        self.addCleanup(temporary.cleanup)
        self.env_file = Path(temporary.name) / ".env"
        self.env_file.write_text(
            "\n".join((
                f"SCRAPBOOK_DB_ADMIN_PASSWORD={secrets.token_hex(32)}",
                f"SCRAPBOOK_DB_APP_PASSWORD={self.password}",
                f"SCRAPBOOK_DB_MIGRATOR_PASSWORD={secrets.token_hex(32)}",
                f"SCRAPBOOK_POSTGRES_PORT={self.database_port}",
                f"SCRAPBOOK_API_PORT={self.api_port}",
                f"SCRAPBOOK_UI_PORT={self.ui_port}",
                "SCRAPBOOK_DEV_IDENTITY_ENABLED=false",
            )) + "\n",
            encoding="utf-8",
        )
        self.compose = [
            "docker", "compose", "--file", str(INFRASTRUCTURE / "compose.yaml"),
            "--env-file", str(self.env_file), "--project-name", self.project,
        ]
        self.addCleanup(self.compose_command, "down", "--volumes", "--remove-orphans")

    def compose_command(self, *args, env=None, stdin=None, check=True):
        return subprocess.run(
            [*self.compose, *args], cwd=INFRASTRUCTURE, env=self.environment | (env or {}), input=stdin, text=True,
            capture_output=True, check=check, timeout=600,
        )

    def get(self, path, *, port=None, headers=None):
        target = f"http://127.0.0.1:{port or self.ui_port}{path}"
        request = Request(target, headers=headers or {})
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def wait_for_health(self, path, expected_status, expected_body, *, port=None):
        deadline = time.monotonic() + 35
        last = None
        while time.monotonic() < deadline:
            try:
                status, headers, body = self.get(path, port=port)
                last = (status, body)
                if status == expected_status:
                    self.assertEqual({"status": expected_body}, json.loads(body))
                    self.assertEqual("no-store", headers.get("Cache-Control"))
                    return
            except (URLError, TimeoutError) as error:
                last = type(error).__name__
            time.sleep(0.4)
        self.fail(f"{path} não alcançou {expected_status}. Observado {last!r}")

    def app_sql(self, sql):
        result = self.compose_command(
            "exec", "-T", "-e", "PGPASSWORD", "postgres", "psql", "-qAtX",
            "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-U", "scrapbook_app", "-d", "scrapbook",
            env={"PGPASSWORD": self.password}, stdin=sql,
        )
        return result.stdout.strip()

    def test_local_foundation(self):
        self.compose_command("up", "-d", "--build", "--wait", "postgres", "service", "ui")
        self.wait_for_health("/health", 200, "up", port=self.api_port)
        self.wait_for_health("/ui-health", 200, "up")
        self.wait_for_health("/health/ready", 503, "down")
        self.assertEqual(
            "", self.compose_command("exec", "-T", "postgres", "psql", "-qAtX", "-U", "postgres", "-d",
                "scrapbook", "-c", "SELECT to_regnamespace('scrapbook')").stdout.strip(),
        )
        print("CA1/CA2 vitalidade estável e prontidão 503 antes do Liquibase", flush=True)

        prepared = subprocess.run(
            [sys.executable, "scripts/prepare_database.py", "--env-file", str(self.env_file), "--project-name", self.project],
            cwd=INFRASTRUCTURE, env=self.environment, text=True, capture_output=True, timeout=180,
        )
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        self.wait_for_health("/health/ready", 200, "up")
        self.wait_for_health("/health/ready", 200, "up", port=self.api_port)
        _, headers, html = self.get("/")
        self.assertIn('lang="pt-BR"', html.decode())
        self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))
        self.assertEqual("DENY", headers.get("X-Frame-Options"))
        self.assertEqual("nosniff", headers.get("X-Content-Type-Options"))
        self.assertEqual("no-referrer", headers.get("Referrer-Policy"))
        self.assertIn("camera=()", headers.get("Permissions-Policy", ""))
        self.assertEqual(200, self.get("/rota-ausente")[0])
        for component, internal, published in (
            ("postgres", 5432, self.database_port), ("service", 8080, self.api_port), ("ui", 8080, self.ui_port),
        ):
            binding = self.compose_command("port", component, str(internal)).stdout.strip()
            self.assertEqual(f"127.0.0.1:{published}", binding)
        for component in ("service", "ui"):
            uid = self.compose_command("exec", "-T", component, "id", "-u").stdout.strip()
            self.assertNotEqual("0", uid)
        correlation = "e886be89-6358-48db-a1dd-665b5152d4ee"
        _, headers, _ = self.get("/health", headers={"X-Correlation-Id": correlation})
        self.assertEqual(correlation, headers.get("X-Correlation-Id"))
        print("CA1 API e UI em loopback, proxy, cabeçalhos e processos sem root", flush=True)

        account_id = self.app_sql("INSERT INTO scrapbook.accounts (state) VALUES ('anonymized') RETURNING id;")
        self.assertTrue(account_id.isdecimal())
        self.compose_command("exec", "-T", "service", "sh", "-c", "printf fixture > /app/keys/.foundation-test")
        self.compose_command("stop", "postgres")
        self.wait_for_health("/health", 200, "up")
        self.wait_for_health("/health/ready", 503, "down")
        self.compose_command("up", "-d", "--wait", "--force-recreate", "postgres", "service", "ui")
        self.wait_for_health("/health/ready", 200, "up")
        self.assertEqual("anonymized", self.app_sql(f"SELECT state FROM scrapbook.accounts WHERE id = {account_id};"))
        self.assertEqual("fixture", self.compose_command("exec", "-T", "service", "cat", "/app/keys/.foundation-test").stdout)
        print("CA2 perda do banco, recuperação e persistência dos volumes observadas", flush=True)

        for overrides, message in (
            (["-e", "ASPNETCORE_ENVIRONMENT=Production", "-e", "DevelopmentIdentity__Enabled=true"],
             "CONFIGURATION_INVALID DevelopmentIdentity:Enabled"),
            (["-e", "ConnectionStrings__ScrapBook=Invalid=fixture-secret"], "CONFIGURATION_INVALID ConnectionStrings:ScrapBook"),
        ):
            failed = self.compose_command("run", "--rm", "--no-deps", *overrides, "service", check=False)
            self.assertNotEqual(0, failed.returncode)
            self.assertIn(message, failed.stdout + failed.stderr)
            self.assertNotIn("fixture-secret", failed.stdout + failed.stderr)
        print("CA3 inicialização incompatível recusada com diagnóstico sanitizado", flush=True)

        if self.run_browser:
            subprocess.run(
                ["npm", "run", "test:foundation"], cwd=ROOT,
                env=os.environ | {"PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{self.ui_port}"},
                check=True, timeout=180,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Executa o aceite da F01 em um projeto Compose descartável.")
    parser.add_argument("--browser", action="store_true")
    arguments = parser.parse_args()
    DockerStackTests.run_browser = arguments.browser
    unittest.main(argv=[sys.argv[0]], verbosity=2)

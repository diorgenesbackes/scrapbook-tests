import argparse
from http.client import RemoteDisconnected
import json
import os
from pathlib import Path
import secrets
import shutil
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
            except (URLError, TimeoutError, RemoteDisconnected, ConnectionResetError) as error:
                last = type(error).__name__
            time.sleep(0.4)
        self.fail(f"{path} não alcançou {expected_status}. Observado {last!r}")

    def app_sql(self, sql):
        result = self.compose_command(
            "exec", "-T", "-e", "PGPASSWORD", "postgres", "psql", "-qAtX",
            "-v", "ON_ERROR_STOP=1", "-h", "postgres", "-U", "scrapbook_app", "-d", "scrapbook",
            env={"PGPASSWORD": self.password}, stdin=sql,
        )
        return result.stdout.strip()

    def prepare_database(self, *options, **environment):
        return subprocess.run(
            [sys.executable, "scripts/prepare_database.py", "--env-file", str(self.env_file),
             "--project-name", self.project, *options],
            cwd=INFRASTRUCTURE, env=self.environment | environment, text=True, capture_output=True, timeout=180,
        )

    def logs(self, component):
        return self.compose_command("logs", "--no-color", "--no-log-prefix", component).stdout

    def verify_reprepare(self):
        with tempfile.TemporaryDirectory(prefix="scrapbook-f01-migration-fixture-") as temporary:
            fixture = Path(temporary)
            changed_password = secrets.token_hex(32)
            missing = self.prepare_database("--liquibase-dir", temporary, SCRAPBOOK_DB_APP_PASSWORD=changed_password)
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("Arquivos do Liquibase", missing.stderr)

            (fixture / "config").mkdir()
            shutil.copyfile(INFRASTRUCTURE.parent / "scrapbook-liquibase" / "config/bootstrap.sql",
                            fixture / "config/bootstrap.sql")
            (fixture / "scripts").mkdir()
            (fixture / "scripts/migrate.py").write_text(
                "import sys\nprint('fixture-private-migration-detail', file=sys.stderr)\nsys.exit(12)\n",
                encoding="utf-8",
            )
            wrong = self.prepare_database("--liquibase-dir", temporary, SCRAPBOOK_DB_APP_PASSWORD=changed_password)
            self.assertNotEqual(0, wrong.returncode)
            self.assertIn("Credencial existente de scrapbook_app", wrong.stderr)
            self.assertNotIn(changed_password, wrong.stderr)

            failed = self.prepare_database("--liquibase-dir", temporary)
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("Migração Liquibase: comando recusado (código 12)", failed.stderr)
            self.assertNotIn("fixture-private-migration-detail", failed.stderr)

        self.assertEqual("1", self.app_sql("SELECT 1;"))
        self.compose_command("restart", "service")
        self.wait_for_health("/health/ready", 200, "up", port=self.api_port)
        print("Reteste do preparo preservou credenciais e prontidão em três falhas distintas", flush=True)

    def verify_proxy_logs(self, correlation):
        query = "fixture-private-proxy-query"
        self.assertEqual(200, self.get("/?probe=" + query)[0])
        self.compose_command("stop", "service")
        self.assertEqual(502, self.get("/health?probe=" + query)[0])
        self.wait_for_health("/ui-health", 200, "up")
        logs = self.logs("ui")
        self.assertNotIn(query, logs)
        entries = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
        self.assertTrue(any(entry.get("route") == "/health" and entry.get("statusCode") == 502 for entry in entries))
        self.assertTrue(any(entry.get("correlationId") == correlation for entry in entries))
        self.compose_command("up", "-d", "--wait", "service")
        self.wait_for_health("/health/ready", 200, "up", port=self.api_port)
        print("Logs de proxy conservaram a rota nominal e omitiram a query em sucesso e falha", flush=True)

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

        prepared = self.prepare_database()
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
        api_entries = [json.loads(line) for line in self.logs("service").splitlines() if line.startswith("{")]
        summary = next(entry for entry in api_entries if entry.get("correlationId") == correlation)
        self.assertEqual(
            (1000, "http.request.completed", "GET", "/health", 200, "success", "Development"),
            tuple(summary[key] for key in ("eventId", "eventName", "httpMethod", "route", "statusCode", "outcome", "environment")),
        )
        self.assertTrue(all(summary[key] for key in ("timestamp", "serviceVersion", "requestId", "traceId")))
        print("CA1 API e UI em loopback, proxy, cabeçalhos e processos sem root", flush=True)

        self.verify_proxy_logs(correlation)
        self.verify_reprepare()

        account_id = self.app_sql("INSERT INTO scrapbook.accounts (state) VALUES ('anonymized') RETURNING id;")
        self.assertTrue(account_id.isdecimal())
        self.compose_command("exec", "-T", "service", "sh", "-c", "printf fixture > /app/keys/.foundation-test")
        self.compose_command("stop", "postgres")
        self.wait_for_health("/health", 200, "up")
        self.wait_for_health("/health/ready", 503, "down")
        logs = self.logs("service")
        self.assertIn('"readinessReason":"database_unavailable"', logs)
        self.assertNotIn("NpgsqlException", logs)
        self.assertNotIn("InvalidOperationException", logs)
        self.compose_command("up", "-d", "--wait", "--force-recreate", "postgres", "service", "ui")
        self.wait_for_health("/health/ready", 200, "up")
        self.assertEqual("anonymized", self.app_sql(f"SELECT state FROM scrapbook.accounts WHERE id = {account_id};"))
        self.assertEqual("fixture", self.compose_command("exec", "-T", "service", "cat", "/app/keys/.foundation-test").stdout)
        print("CA2 perda do banco, recuperação e persistência dos volumes observadas", flush=True)

        for overrides, message in (
            (["-e", "ASPNETCORE_ENVIRONMENT=Production", "-e", "DevelopmentIdentity__Enabled=true"],
             "CONFIGURATION_INVALID DevelopmentIdentity:Enabled"),
            (["-e", "DevelopmentIdentity__Enabled=true"], "CONFIGURATION_INVALID DevelopmentIdentity:AllowContainerBinding"),
            (["-e", "ConnectionStrings__ScrapBook=Invalid=fixture-secret"], "CONFIGURATION_INVALID ConnectionStrings:ScrapBook"),
        ):
            failed = self.compose_command("run", "--rm", "--no-deps", *overrides, "service", check=False)
            self.assertNotEqual(0, failed.returncode)
            self.assertIn(message, failed.stdout + failed.stderr)
            self.assertNotIn("fixture-secret", failed.stdout + failed.stderr)
        print("CA3 inicialização incompatível recusada com diagnóstico sanitizado", flush=True)

        self.compose_command(
            "up", "-d", "--wait", "service",
            env={"SCRAPBOOK_DEV_IDENTITY_ENABLED": "true", "SCRAPBOOK_DEV_ALLOW_CONTAINER_BINDING": "true",
                 "SCRAPBOOK_DEV_ACCOUNT_ID": account_id, "SCRAPBOOK_DEV_IDENTITY_TOKEN": secrets.token_hex(32)},
        )
        self.wait_for_health("/health/ready", 200, "up", port=self.api_port)

        if self.run_browser:
            browser_environment = self.environment | {"PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{self.ui_port}"}
            if browser_environment.pop("NO_COLOR", None):
                browser_environment.update(FORCE_COLOR="0", DEBUG_COLORS="0")
            subprocess.run(
                ["npm", "run", "test:foundation"], cwd=ROOT,
                env=browser_environment,
                check=True, timeout=180,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Executa o aceite da F01 em um projeto Compose descartável.")
    parser.add_argument("--browser", action="store_true")
    arguments = parser.parse_args()
    DockerStackTests.run_browser = arguments.browser
    unittest.main(argv=[sys.argv[0]], verbosity=2)

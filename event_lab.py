"""Laboratório manual descartável. Nunca aceita conexão/banco externo."""
import contextlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
INFRA = ROOT.parent / 'scrapbook-infrastructure'
SERVICE = ROOT.parent / 'scrapbook-service'
LIQUIBASE = ROOT.parent / 'scrapbook-liquibase'
sys.path.insert(0, str(INFRA / 'scripts'))
import events_local  # noqa: E402


def available_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(base, method='GET', path='/api/v1/events', body=None, *, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body, ensure_ascii=True).encode() if body is not None else None)
    head = {'Content-Type': 'application/json'} if data is not None else {}
    head.update(headers or {})
    call = urllib.request.Request(base + path, method=method, data=data, headers=head)
    try:
        response = urllib.request.urlopen(call, timeout=8)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = response.read()
        return response.status, dict(response.headers.items()), json.loads(payload) if payload else None


class EventLab:
    def __init__(self):
        self.name = 'scrapbook-events-test-' + secrets.token_hex(6)
        self.password = secrets.token_urlsafe(32)
        self.processes = []
        self.temp = tempfile.TemporaryDirectory(prefix=self.name + '-')
        self.directory = Path(self.temp.name)
        self.urls = {}
        self.actors = {}
        self.logs = []

    def command(self, args, *, stdin=None, env=None, cwd=None, ok=True):
        result = subprocess.run(args, input=stdin, env=env, cwd=cwd, text=True, capture_output=True, timeout=120)
        if ok and result.returncode:
            raise AssertionError('Laboratório: comando falhou; diagnóstico privado suprimido: ' + args[0])
        return result

    def sql(self, statement, *, ok=True, ddl=False):
        return self.command(['docker', 'exec', '-i', self.name, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                             '-v', 'VERBOSITY=sqlstate', '-U', 'scrapbook_ddl' if ddl else 'scrapbook_admin',
                             '-d', 'scrapbook_events_test'], stdin=statement, ok=ok)

    def scalar(self, query):
        return self.sql(query).stdout.strip()

    def start_api(self, actor, *, enabled=True, environment='Development', account=None):
        port = available_port()
        base = 'http://127.0.0.1:' + str(port)
        log = self.directory / (actor + '-' + str(port) + '.log')
        stream = log.open('w')
        self.logs.append(log)
        env = dict(os.environ, ASPNETCORE_ENVIRONMENT=environment, DOTNET_ENVIRONMENT=environment,
                   ASPNETCORE_URLS=base, ConnectionStrings__ScrapBook=self.connection,
                   DevelopmentIdentity__Enabled=str(enabled).lower(),
                   DevelopmentIdentity__AccountId=str(account if account is not None else self.actors.get(actor, 0)),
                   DevelopmentIdentity__AllowedOrigins__0=self.ui_url,
                   Events__CursorKeyPath=str(self.directory / 'cursor-keys'))
        process = subprocess.Popen(['dotnet', str(SERVICE / 'ScrapBook.Api/bin/Debug/net10.0/ScrapBook.Api.dll')],
                                   cwd=SERVICE / 'ScrapBook.Api', env=env, stdout=stream, stderr=subprocess.STDOUT)
        stream.close()
        self.processes.append(process)
        if environment != 'Development' and enabled:
            process.wait(timeout=10)
            assert process.returncode != 0, 'Identidade local aceita em produção'
            return None
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError('API do laboratório encerrou antes de ficar pronta')
            try:
                if request(base, path='/health')[0] == 200:
                    self.urls[actor] = base
                    return base
            except OSError:
                pass
            time.sleep(.05)
        raise AssertionError('API do laboratório não iniciou')

    def __enter__(self):
        events_local.require_local_docker()
        self.ui_port = available_port()
        self.ui_url = 'http://127.0.0.1:' + str(self.ui_port)
        versions = json.loads((LIQUIBASE / 'config/versions.json').read_text())
        env = dict(os.environ, POSTGRES_PASSWORD=self.password)
        try:
            self.command(['docker', 'run', '--detach', '--rm', '--name', self.name,
                          '--publish', '127.0.0.1::5432', '--env', 'POSTGRES_PASSWORD',
                          '--env', 'POSTGRES_DB=scrapbook_events_test', '--env', 'POSTGRES_USER=scrapbook_admin',
                          versions['postgres_image']], env=env)
            for _ in range(100):
                if self.command(['docker', 'exec', self.name, 'pg_isready', '-h', '127.0.0.1', '-U', 'scrapbook_admin',
                                 '-d', 'scrapbook_events_test'], ok=False).returncode == 0:
                    break
                time.sleep(.1)
            port = self.command(['docker', 'port', self.name, '5432/tcp']).stdout.strip().split(':')[-1]
            self.sql("CREATE ROLE scrapbook_ddl LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD "
                     + events_local.literal(self.password) + '; ALTER DATABASE scrapbook_events_test OWNER TO scrapbook_ddl;')
            self.command(['liquibase', '--show-banner=false', '--changelog-file=db.changelog-master.yaml',
                          '--url=jdbc:postgresql://127.0.0.1:' + port + '/scrapbook_events_test',
                          '--username=scrapbook_ddl', 'update'],
                         env=dict(os.environ, LIQUIBASE_COMMAND_PASSWORD=self.password), cwd=LIQUIBASE)
            assert self.scalar('SELECT count(*) FROM public.databasechangelog;') == '47'
            self.sql("BEGIN; SELECT set_config('scrapbook.runtime_user','scrapbook_runtime',true); SELECT set_config('scrapbook.runtime_password',"
                     + events_local.literal(self.password) + ',true);\n'
                     + (INFRA / 'postgres/development/provision-runtime.sql').read_text() + '\nCOMMIT;')
            for actor in ['owner', 'other', 'second']:
                email = 'gestao-eventos-' + actor + '@example.invalid'
                self.actors[actor] = self.scalar('INSERT INTO identity.accounts(subject,name,email,normalized_email,email_verified,state) VALUES ('
                    + events_local.literal('development:gestao-eventos:' + actor) + ",'Synthetic fixture',"
                    + events_local.literal(email) + ',' + events_local.literal(email) + ",true,'active') RETURNING id;")
            self.connection = 'Host=127.0.0.1;Port=' + port + ';Database=scrapbook_events_test;Username=scrapbook_runtime;Password=' + self.password + ';Timeout=2;Command Timeout=4;Maximum Pool Size=20;SSL Mode=Disable;Include Error Detail=false'
            for actor in ['owner', 'other', 'second']:
                self.start_api(actor)
            self.start_api('disabled', enabled=False)
            self.start_api('invalid', account=9223372036854775807)
            self.start_api('production', environment='Production')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.command(['docker', 'rm', '--force', self.name], ok=False)
        self.temp.cleanup()

    def start_ui(self):
        stream = (self.directory / 'vite.log').open('w')
        process = subprocess.Popen(['npm', 'run', 'preview', '--', '--port', str(self.ui_port), '--strictPort'],
                                   cwd=ROOT.parent / 'scrapbook-ui', env=dict(os.environ, BACKEND_PROXY_TARGET=self.urls['owner']),
                                   stdout=stream, stderr=subprocess.STDOUT)
        stream.close()
        self.processes.append(process)
        for _ in range(100):
            try:
                with urllib.request.urlopen(self.ui_url, timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(.1)
        raise AssertionError('Vite do laboratório não iniciou')

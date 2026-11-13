#!/usr/bin/env python3
"""Validação manual da stack Compose, restrita a um projeto/volumes descartáveis próprios."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from event_lab import INFRA, LIQUIBASE, ROOT, available_port, events_local, request
from journeys import browser, performance


class ComposeLab:
    def __init__(self):
        self.name = 'scrapbook-docker-test-' + secrets.token_hex(6)
        self.temp = tempfile.TemporaryDirectory(prefix=self.name + '-')
        self.directory = Path(self.temp.name)
        self.env_file = self.directory / '.env'
        self.ui_port, self.api_port, self.db_port = available_port(), available_port(), available_port()
        self.ui_url = 'http://127.0.0.1:' + str(self.ui_port)
        self.api_url = 'http://127.0.0.1:' + str(self.api_port)
        self.urls = {'owner': self.ui_url}
        self.passwords = {role: secrets.token_urlsafe(32) for role in ['admin', 'migrator', 'runtime']}
        self.values = {
            'POSTGRES_DB': 'scrapbook_docker_test', 'POSTGRES_USER': 'scrapbook_admin',
            'POSTGRES_PASSWORD': self.passwords['admin'], 'POSTGRES_PORT': str(self.db_port),
            'POSTGRES_MIGRATOR_USER': 'scrapbook_migrator', 'POSTGRES_MIGRATOR_PASSWORD': self.passwords['migrator'],
            'POSTGRES_RUNTIME_USER': 'scrapbook_runtime', 'POSTGRES_RUNTIME_PASSWORD': self.passwords['runtime'],
            'SCRAPBOOK_PUBLIC_ORIGIN': self.ui_url, 'SCRAPBOOK_PUBLIC_ALLOW_HTTP_LOOPBACK': 'true',
            'UI_PORT': str(self.ui_port), 'SERVICE_PORT': str(self.api_port), 'SCRAPBOOK_IMAGE_TAG': 'local',
            'SCRAPBOOK_ENVIRONMENT': 'Production', 'SCRAPBOOK_DEVELOPMENT_IDENTITY': 'false',
            'SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID': '0',
        }
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in self.values and not key.startswith('COMPOSE_')}
        self.save_environment()

    def save_environment(self):
        events_local.private_write(self.env_file, '\n'.join(key + '=' + value for key, value in self.values.items()) + '\n')

    def command(self, args, *, stdin=None, env=None, cwd=None, ok=True):
        result = subprocess.run(args, input=stdin, env=env or self.environment, cwd=cwd,
                                capture_output=True, text=True, timeout=240)
        if ok and result.returncode:
            private = ROOT / 'artifacts/docker-stack-private.log'
            events_local.private_write(private, result.stdout + '\n' + result.stderr)
            raise AssertionError('Comando da stack falhou; diagnóstico privado em artifacts/docker-stack-private.log: ' + args[0])
        return result

    def compose(self, *args, **kwargs):
        return self.command(['docker', 'compose', '--project-name', self.name, '--env-file', str(self.env_file),
                             '-f', str(INFRA / 'compose.yaml'), *args], **kwargs)

    def sql(self, statement):
        return self.compose('exec', '-T', 'postgres', 'sh', '-c',
                            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -qAt -v ON_ERROR_STOP=1 '
                            '-h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"', stdin=statement)

    def scalar(self, query):
        return self.sql(query).stdout.strip()

    def start_ui(self):
        pass  # UI já é servida pelo container Nginx.

    def wait_proxy(self):
        # Após recriar só a API, aguardar propagação do DNS/keepalive no proxy.
        # Probe GET operacional; não repetir comandos de escrita ou o cenário de negócio.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if request(self.ui_url, path='/health')[2] == {'status': 'up'}:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        raise AssertionError('Proxy não convergiu após recriar a API do laboratório')

    def inspect(self, service):
        container_id = self.compose('ps', '-q', service).stdout.strip()
        return json.loads(self.command(['docker', 'inspect', container_id]).stdout)[0]

    def __enter__(self):
        events_local.require_local_docker()
        try:
            config = json.loads(self.compose('config', '--format', 'json').stdout)
            for service in config['services'].values():
                assert all(port['host_ip'] == '127.0.0.1' for port in service['ports'])
            connection = config['services']['service']['environment']['ConnectionStrings__ScrapBook']
            assert 'Host=postgres;Port=5432;' in connection
            assert self.passwords['runtime'] in connection
            assert all(self.passwords[role] not in connection for role in ['admin', 'migrator'])
            self.compose('up', '-d', '--wait', '--wait-timeout', '60', 'postgres')
            self.command(['liquibase', '--show-banner=false', '--changelog-file=db.changelog-master.yaml',
                          '--url=jdbc:postgresql://127.0.0.1:' + str(self.db_port) + '/scrapbook_docker_test',
                          '--username=scrapbook_migrator', 'update'],
                         cwd=LIQUIBASE, env=dict(self.environment, LIQUIBASE_COMMAND_PASSWORD=self.passwords['migrator']))
            self.sql("BEGIN; SELECT set_config('scrapbook.runtime_user','scrapbook_runtime',true); "
                     + "SELECT set_config('scrapbook.runtime_password'," + events_local.literal(self.passwords['runtime']) + ',true);\n'
                     + (INFRA / 'postgres/development/provision-runtime.sql').read_text() + '\nCOMMIT;')
            self.actor = self.scalar("INSERT INTO identity.accounts(subject,name,email,normalized_email,email_verified,state) "
                                     "VALUES ('development:gestao-eventos:owner','Synthetic fixture',"
                                     "'gestao-eventos-owner@example.invalid','gestao-eventos-owner@example.invalid',true,'active') RETURNING id;")
            self.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service', 'ui')
            self.wait_proxy()
            assert request(self.api_url, path='/health')[2] == {'status': 'up'}
            assert request(self.ui_url, path='/health')[2] == {'status': 'up'}
            assert request(self.ui_url)[0] == 401
            self.values.update(SCRAPBOOK_ENVIRONMENT='Development', SCRAPBOOK_DEVELOPMENT_IDENTITY='true',
                               SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=self.actor)
            self.save_environment()
            self.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service', 'ui')
            self.wait_proxy()
            assert request(self.ui_url)[0] == 200
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        logs = self.compose('logs', '--no-color', '--tail', '80', ok=False)
        events_local.private_write(ROOT / 'artifacts/docker-stack-containers-private.log', logs.stdout + logs.stderr)
        # Somente volumes deste projeto aleatório. Nunca usar down -v no Compose do usuário.
        cleanup = self.compose('down', '--volumes', '--remove-orphans', ok=False)
        self.temp.cleanup()
        assert cleanup.returncode == 0, 'Não foi possível limpar o projeto Compose exclusivo.'


def raw(base, path):
    try:
        response = urllib.request.urlopen(base + path, timeout=8)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers.items()), response.read()


def contracts(lab):
    status, _, html = raw(lab.ui_url, '/events/new')
    assert status == 200 and b'<div id="root"></div>' in html
    asset = re.search(rb'src="(/assets/[^\"]+\.js)"', html).group(1).decode()
    assert raw(lab.ui_url, asset)[0] == 200
    assert raw(lab.ui_url, '/assets/missing.js')[0] == 404
    assert raw(lab.ui_url, '/api/v1/missing')[0] == 404
    for service in ['service', 'ui']:
        info = lab.inspect(service)
        assert info['Config']['User'] not in ['', '0', 'root']
        assert info['HostConfig']['ReadonlyRootfs']
        assert info['State']['Health']['Status'] == 'healthy'
        assert list(info['NetworkSettings']['Networks']) == [lab.name + '_scrapbook']
    assert not lab.compose('exec', '-T', 'service', 'dotnet', '--list-sdks').stdout.strip()
    assert lab.compose('exec', '-T', 'ui', 'sh', '-c', 'command -v node || true').stdout.strip() == ''
    assert lab.compose('exec', '-T', 'ui', 'wget', '-qO-', 'http://service:8080/health').stdout == '{"status":"up"}'
    lab.compose('exec', '-T', 'service', 'bash', '-c', 'exec 3<>/dev/tcp/postgres/5432')
    start = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    status, _, event = request(lab.ui_url, 'POST', body={'name': 'Docker persistence fixture', 'startsAt': start})
    assert status == 201
    event_id = event['id']
    status, _, changed = request(lab.ui_url, 'PUT', '/api/v1/events/' + event_id,
                                {'name': 'Docker updated fixture', 'startsAt': start, 'welcomeMessage': None,
                                 'expectedVersion': event['version']})
    assert status == 200 and int(changed['version']) > int(event['version'])
    assert request(lab.ui_url, 'PUT', '/api/v1/events/' + event_id,
                   {'name': 'Stale', 'startsAt': start, 'welcomeMessage': None, 'expectedVersion': event['version']})[0] == 409
    for body in [{'name': 'Extra field', 'startsAt': start, 'accountId': lab.actor},
                 {'name': 'x' * 33000, 'startsAt': start}]:
        status, headers, error = request(lab.ui_url, 'POST', body=body)
        assert status in [400, 413] and headers['Content-Type'].startswith('application/problem+json')
        assert error['code'] in ['REQUEST_VALIDATION_FAILED', 'REQUEST_TOO_LARGE']
    assert lab.scalar('SELECT count(*) FROM events.event_members WHERE event_members_events_id=' + event_id + ';') == '1'
    assert int(lab.scalar('SELECT count(*) FROM events.audit_entries WHERE audit_entries_events_id=' + event_id + ';')) >= 2
    direct = request(lab.api_url, path='/api/v1/events/' + event_id)[2]
    # ServerTime avança entre requests; comparar dados persistidos em vez do relógio da resposta.
    assert {key: value for key, value in direct.items() if key != 'serverTime'} == {
        key: value for key, value in changed.items() if key != 'serverTime'}
    return {'proxy_crud': True, 'optimistic_version': True, 'strict_json': True, 'spa_assets': True,
            'internal_dns': True, 'non_root_read_only': True, 'runtime_without_build_tools': True,
            'default_identity_disabled': True, 'runtime_credential_separated': True, 'event_id': event_id}


def persistence(lab, event_id):
    start = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    assert request(lab.ui_url, 'POST', body={'name': 'Cursor fixture', 'startsAt': start})[0] == 201
    page = request(lab.ui_url, path='/api/v1/events?limit=1')[2]
    token = page['nextCursor']
    assert token
    lab.compose('down')
    lab.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120')
    assert request(lab.ui_url, path='/api/v1/events/' + event_id)[2]['name'] == 'Docker updated fixture'
    assert request(lab.ui_url, path='/api/v1/events?limit=1&cursor=' + token)[0] == 200
    assert lab.scalar('SELECT count(*) FROM public.databasechangelog;') == '47'
    assert lab.scalar("SELECT count(*) FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema IN ('identity','events','records','notifications');") == '33'
    return {'event_after_down_up': True, 'cursor_after_down_up': True, 'migrations': 47, 'tables': 33}


def main():
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    report = {'started_at': datetime.now().astimezone().isoformat(), 'target': 'exclusive disposable Compose project'}
    with ComposeLab() as lab:
        report['contracts'] = contracts(lab)
        report['persistence'] = persistence(lab, report['contracts'].pop('event_id'))
        # A jornada de paginação exige mais de uma página (20 itens).
        start = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
        for index in range(25):
            assert request(lab.ui_url, 'POST', body={'name': 'Browser pagination fixture ' + str(index), 'startsAt': start})[0] == 201
        report['browser_fixture_events'] = 25
        report['browser'] = browser(lab)
        report['performance'] = performance(lab)
        report['performance']['network'] = 'host loopback → Nginx → service:8080 → postgres:5432, Docker bridge'
        report['images'] = {service: lab.inspect(service)['Image'] for service in ['service', 'ui', 'postgres']}
        report['compose_version'] = lab.command(['docker', 'compose', 'version', '--short']).stdout.strip()
    report.update(passed=True, finished_at=datetime.now().astimezone().isoformat())
    (ROOT / 'artifacts/docker-stack-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

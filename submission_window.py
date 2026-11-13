#!/usr/bin/env python3
"""PP-STORY-004: HTTP temporal/Playwright em recursos exclusivamente descartáveis."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import time
from zoneinfo import ZoneInfo

from docker_stack import ComposeLab
from event_lab import ROOT, INFRA, available_port, events_local, request
from public_event import event_records, public_path, snapshot, valid_public_projection


def set_clock(path, instant):
    temporary = path.with_suffix('.next')
    events_local.private_write(temporary, instant.isoformat())
    temporary.replace(path)


@contextmanager
def temporal_host(lab):
    clock = lab.directory / 'window-clock.txt'
    set_clock(clock, datetime(2030, 6, 15, 19, tzinfo=timezone.utc))
    url = 'http://127.0.0.1:' + str(available_port())
    connection = ('Host=127.0.0.1;Port=' + str(lab.db_port) + ';Database=scrapbook_docker_test;'
                  'Username=scrapbook_runtime;Password=' + lab.passwords['runtime'] + ';Timeout=2;Command Timeout=4;Include Error Detail=false')
    env = dict(os.environ, ConnectionStrings__ScrapBook=connection, SCRAPBOOK_WINDOW_HOST=url,
               SCRAPBOOK_WINDOW_CLOCK=str(clock), SCRAPBOOK_PUBLIC_ORIGIN=lab.ui_url)
    log = ROOT/'artifacts/submission-window-host-private.log'
    events_local.private_write(log, '')
    with log.open('w') as output:
        process = subprocess.Popen(['dotnet', str(ROOT/'probes/bin/Debug/net10.0/ScrapBook.Probes.dll'), 'public-window'],
                                   cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError('Host temporal exclusivo encerrou antes do health.')
            try:
                if request(url, path='/health')[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(.1)
        else:
            raise AssertionError('Host temporal exclusivo não iniciou.')
        yield url, clock
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def create_fixtures(lab):
    starts = ['2030-06-15T18:00:00', '2030-01-31T00:30:00', '2030-12-31T23:59:00', '2028-02-29T00:30:00',
              (datetime.now(ZoneInfo('America/Sao_Paulo'))-timedelta(days=2)).strftime('%Y-%m-%dT18:00:00')]
    ids = []
    for index, start in enumerate(starts):
        status, _, event = request(lab.ui_url, 'POST', body={'name': 'Encontro temporal ' + str(index),
                                  'startsAt': start, 'openingMode': 'immediate'})
        assert status == 201
        ids.append(event['id'])
    rows = {event['id']: event for event in event_records(lab)}
    return [rows[event_id] for event_id in ids]


def contracts(lab, base, clock, events):
    before = snapshot(lab)
    cases = []
    for event in events[:4]:
        starts = datetime.fromisoformat(event['startsAt'])
        opens = starts-timedelta(hours=2)
        closes = starts.replace(hour=0, minute=0, second=0) + timedelta(days=1, hours=12)
        instants = [opens-timedelta(milliseconds=1), opens, opens+timedelta(milliseconds=1),
                    closes-timedelta(milliseconds=1), closes, closes+timedelta(milliseconds=1),
                    closes+timedelta(minutes=30), closes+timedelta(hours=1), closes+timedelta(hours=1, milliseconds=1)]
        for civil in instants:
            instant = civil.replace(tzinfo=ZoneInfo('America/Sao_Paulo')).astimezone(timezone.utc)
            set_clock(clock, instant)
            status, headers, body = request(base, path=public_path(event))
            assert status == 200 and valid_public_projection(body, event)
            assert datetime.fromisoformat(body['serverTime']) == instant
            assert headers['Cache-Control'] == 'no-store' and 'Set-Cookie' not in headers
            cases.append({'startsAt': event['startsAt'], 'serverTime': body['serverTime'], 'state': body['submissionWindow']['state']})
    assert snapshot(lab) == before
    assert request(base, path=public_path(events[0])+'?serverTime=2000-01-01T00:00:00Z')[0] == 400
    # Mudar a galeria pela API real não modifica nenhum marco/estado da participação.
    first = events[0]
    set_clock(clock, datetime(2030, 6, 15, 19, tzinfo=timezone.utc))
    original = request(base, path=public_path(first))[2]['submissionWindow']
    current = request(lab.ui_url, path='/api/v1/events/'+first['id'])[2]
    status, _, changed = request(lab.ui_url, 'PUT', '/api/v1/events/'+first['id']+'/access',
                                {'openingMode': 'scheduled', 'openingDate': '2035-01-01', 'expectedVersion': current['version']})
    assert status == 200
    assert request(base, path=public_path(first))[2]['submissionWindow'] == original
    assert request(lab.ui_url, 'PUT', '/api/v1/events/'+first['id']+'/access',
                   {'openingMode': 'immediate', 'openingDate': None, 'expectedVersion': changed['version']})[0] == 200
    return cases


def main():
    (ROOT/'artifacts').mkdir(exist_ok=True)
    report = {'story': 'PP-STORY-004', 'started_at': datetime.now(timezone.utc).isoformat(),
              'environment': 'isolated-local-compose-plus-test-only-clock-host', 'deployed': False, 'physical_devices': False}
    with ComposeLab() as lab:
        events = create_fixtures(lab)
        schema = request(lab.api_url, path='/openapi/v1.json')[2]
        assert set(schema['components']['schemas']['PublicEventResponse']['required']) == {
            'code', 'name', 'effectiveWelcomeMessage', 'cover', 'publicUrl', 'serverTime', 'submissionWindow'}
        examples = schema['paths']['/api/v1/public/events/{code}']['get']['responses']['200']['content']['application/json']['examples']
        assert {v['value']['submissionWindow']['state'] for v in examples.values()} == {'not_open', 'open', 'closed'}
        report['openapi'] = True
        with temporal_host(lab) as (base, clock):
            report['http_temporal_cases'] = contracts(lab, base, clock, events)
            report['gallery_independent'] = True
            manifest = lab.directory/'window-manifest.json'
            events_local.private_write(manifest, json.dumps({'event': events[0], 'closedEvent': events[-1], 'clock': str(clock),
                'host': base, 'project': lab.name, 'envFile': str(lab.env_file), 'compose': str(INFRA/'compose.yaml')}))
            baseline = snapshot(lab)
            result = subprocess.run(['npx', 'playwright', 'test', '--config', 'submission_window.config.ts'], cwd=ROOT,
                                    env=dict(os.environ, SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url, SCRAPBOOK_WINDOW_MANIFEST=str(manifest)), timeout=300)
            assert result.returncode == 0, 'Temporal browser suite failed'
            report['browser'] = {'tests': 10, 'projects': ['chromium-desktop/Asia-Tokyo', 'webkit-mobile/America-Los_Angeles'],
                                 'controlled_server_clock': True, 'physical_devices': False}
            # A jornada de edição/restauração faz gravações intencionais somente no evento/auditoria.
            after = snapshot(lab)
            assert all(before == after[table] for table, before in baseline.items() if table not in {'events.events', 'events.audit_entries'})
            report['read_only_http'] = True
            report['service_stop_restart_without_extension'] = True
        logs = lab.compose('logs', '--no-color', '--tail', '300', 'service').stdout
        assert all(e['code'] not in logs and e['name'] not in logs for e in events)
        report['logs_redacted'] = True
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (ROOT/'artifacts/submission-window-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

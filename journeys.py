#!/usr/bin/env python3
"""Jornadas manuais isoladas: HTTP, navegador, falhas e carga H-03."""
import argparse
import concurrent.futures
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from event_lab import EventLab, ROOT, request
from events_http import run as http_checks


def fixture(lab):
    today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    year = today.year + 1
    while not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        year += 1
    start = str(year) + '-02-29T18:30:00'
    body = {'name': 'Locked snapshot fixture', 'startsAt': start, 'openingMode': 'immediate', 'openingDate': None}
    status, _, locked = request(lab.urls['owner'], 'POST', body=body)
    assert status == 201
    # Simulated eventual Events snapshot ONLY in this disposable lab. No Records consumer is implemented.
    lab.sql('UPDATE events.events SET locked_at=CURRENT_TIMESTAMP,first_record_id=9223372036854775806,first_record_confirmed_at=clock_timestamp() AT TIME ZONE \'America/Sao_Paulo\' WHERE id=' + locked['id'] + ';')
    manifest = {'owner': lab.urls['owner'], 'lockedId': locked['id'], 'startsAt': start, 'openingDate': str(year + 1) + '-02-28'}
    path = lab.directory / 'browser-manifest.json'
    path.write_text(json.dumps(manifest))
    return path


def browser(lab):
    manifest = fixture(lab)
    lab.start_ui()
    environment = dict(os.environ, SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url, SCRAPBOOK_EVENTS_MANIFEST=str(manifest))
    result = subprocess.run(['npx', 'playwright', 'test'], cwd=ROOT, env=environment, timeout=240)
    assert result.returncode == 0, 'Browser suite failed; see ignored test-results and playwright-report'
    return {'projects': ['chromium-desktop/Asia-Tokyo', 'webkit-mobile/America-Los_Angeles'], 'physical_devices': False}


def failures_and_privacy(lab):
    owner = lab.urls['owner']
    status, _, event = request(owner, 'POST', body={'name': 'GE_PRIVATE_NAME_MARKER', 'startsAt':
        (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00'),
        'welcomeMessage': 'GE_PRIVATE_WELCOME_MARKER'})
    assert status == 201
    # Runtime outage without touching the user's database or stopping a shared service.
    lab.sql("ALTER ROLE scrapbook_runtime NOLOGIN; SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='scrapbook_runtime' AND pid<>pg_backend_pid();")
    try:
        before = time.monotonic()
        status, _, problem = request(owner)
        elapsed = time.monotonic() - before
        assert status == 503 and problem['code'] == 'DEPENDENCY_UNAVAILABLE', 'Runtime outage not sanitized as dependency failure'
        assert elapsed < 5.5
    finally:
        lab.sql('ALTER ROLE scrapbook_runtime LOGIN;')
    assert request(owner)[0] == 200
    # Hold a genuine lock; wait until PostgreSQL confirms this lab's locker owns it.
    locker = subprocess.Popen(['docker', 'exec', '-i', lab.name, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                               '-U', 'scrapbook_admin', '-d', 'scrapbook_events_test'],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    try:
        locker.stdin.write("SET application_name='ge_test_locker'; BEGIN; SELECT id FROM events.events WHERE id=" + event['id'] + ' FOR UPDATE; SELECT pg_sleep(8); ROLLBACK;')
        locker.stdin.close()
        for _ in range(100):
            if lab.scalar("SELECT count(*) FROM pg_stat_activity WHERE application_name='ge_test_locker' AND wait_event='PgSleep';") == '1':
                break
            time.sleep(.02)
        else:
            raise AssertionError('Could not establish test lock')
        # First spend ~2s resolving Identity, then wait on Events: the shared API budget must still be 5s.
        identity_locker = subprocess.Popen(['docker', 'exec', '-i', lab.name, 'psql', '-X', '-qAt', '-U', 'scrapbook_admin', '-d', 'scrapbook_events_test'],
                                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        identity_locker.stdin.write("SET application_name='ge_identity_locker'; BEGIN; LOCK TABLE identity.accounts IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(2); COMMIT;")
        identity_locker.stdin.close()
        for _ in range(100):
            if lab.scalar("SELECT count(*) FROM pg_stat_activity WHERE application_name='ge_identity_locker' AND wait_event='PgSleep';") == '1':
                break
            time.sleep(.01)
        before = time.monotonic()
        status, _, body = request(owner, 'PUT', '/api/v1/events/' + event['id'],
            {'name': event['name'], 'startsAt': event['startsAt'], 'welcomeMessage': None, 'expectedVersion': event['version']})
        lock_elapsed = time.monotonic() - before
        assert status == 503 and body['code'] == 'DEPENDENCY_UNAVAILABLE'
        assert 4.5 < lock_elapsed < 5.5
        identity_locker.wait(timeout=5)
    finally:
        lab.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='ge_test_locker';")
        locker.wait(timeout=5)
    assert request(owner, path='/api/v1/events/' + event['id'])[2]['version'] == event['version']
    allowed = ['starts_at', 'submission_closes_at', 'opening_mode', 'opens_at', 'access_state', 'access_version']
    audit_keys = json.loads(lab.scalar("SELECT coalesce(json_agg(DISTINCT key),'[]') FROM events.audit_entries a CROSS JOIN LATERAL jsonb_object_keys(coalesce(a.before_state,'{}'::jsonb)||coalesce(a.after_state,'{}'::jsonb)) key;"))
    assert set(audit_keys) <= set(allowed)
    rows = []
    for log in lab.logs:
        content = log.read_text()
        assert lab.password not in content, 'Credential appeared in diagnostics'
        assert 'GE_PRIVATE_NAME_MARKER' not in content and 'GE_PRIVATE_WELCOME_MARKER' not in content, 'Event content appeared in diagnostics'
        for line in content.splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get('EventId') in [4000, 4002, 4003]:
                assert entry['Timestamp'].endswith('Z')
                state = entry['State']
                assert state['Module'] == 'events' and state['Environment'] == 'Development'
                assert not any(private in state for private in ['Name', 'AccountId', 'EventId', 'WelcomeMessage'])
                assert state['RouteTemplate'].startswith('/api/v1/events') and len(state['TraceId']) == 32
                rows.append(entry)
    assert rows, 'No structured Events logs collected'
    return {'runtime_outage_503_ms': round(elapsed * 1000, 3), 'lock_timeout_503_ms': round(lock_elapsed * 1000, 3),
            'private_markers_absent': True, 'audit_allowlist': audit_keys, 'structured_log_rows': len(rows)}


def performance(lab):
    owner = lab.urls['owner']
    start = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    count = int(lab.scalar('SELECT count(*) FROM events.events;'))
    for index in range(count, 200):
        assert request(owner, 'POST', body={'name': 'Load synthetic ' + str(index), 'startsAt': start})[0] == 201
    ids = json.loads(lab.scalar("SELECT json_agg(id::text) FROM (SELECT id FROM events.events WHERE locked_at IS NULL ORDER BY id DESC LIMIT 10) e;"))
    assert len(ids) == 10 and lab.scalar('SELECT count(*) FROM events.events;') == '200'
    shared = {}
    barrier = threading.Barrier(10, action=lambda: shared.update(deadline=time.monotonic() + 60))

    def worker(index):
        latencies = {key: [] for key in ['list', 'detail', 'update']}
        errors = {key: 0 for key in latencies}
        event_id, changed = ids[index], False
        def cycle(measured):
            nonlocal changed
            event = None
            for operation in ['list', 'detail', 'update']:
                before = time.monotonic()
                try:
                    if operation == 'list':
                        status, _, body = request(owner)
                    elif operation == 'detail':
                        status, _, event = request(owner, path='/api/v1/events/' + event_id)
                    else:
                        assert event is not None
                        status, _, body = request(owner, 'PUT', '/api/v1/events/' + event_id,
                            body={'name': 'Load ' + str(index) + (' A' if changed else ' B'), 'startsAt': event['startsAt'],
                                  'welcomeMessage': None, 'expectedVersion': event['version']})
                        changed = not changed
                    assert status == 200
                except (OSError, ValueError, AssertionError):
                    if measured:
                        errors[operation] += 1
                    else:
                        raise
                if measured:
                    latencies[operation].append((time.monotonic() - before) * 1000)
        for _ in range(3):
            cycle(False)
        barrier.wait(timeout=30)
        while time.monotonic() < shared['deadline']:
            cycle(True)
        return latencies, errors
    print(json.dumps({'performance': 'started', 'seconds': 60, 'concurrency': 10, 'events': 200}), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        samples = list(pool.map(worker, range(10)))
    report = {'profile': 'H-03', 'seconds': 60, 'concurrency': 10, 'events': 200, 'warmup_cycles_per_worker': 3,
              'network': 'HTTP host loopback; PostgreSQL Docker Desktop VM loopback', 'machine': platform.machine(),
              'cpu_count': os.cpu_count(), 'os': platform.mac_ver()[0], 'operations': {}}
    for operation in ['list', 'detail', 'update']:
        values = sorted(value for result, _ in samples for value in result[operation])
        errors = sum(failures[operation] for _, failures in samples)
        report['operations'][operation] = {'requests': len(values), 'errors': errors, 'p95_ms': round(values[math.ceil(len(values) * .95) - 1], 3)}
    print(json.dumps({'performance_results': report}), flush=True)
    assert all(result['errors'] == 0 and result['p95_ms'] <= 500 for result in report['operations'].values()), 'H-03 target was not met'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['backend', 'browser', 'load', 'models', 'all'])
    stage = parser.parse_args().stage
    report = {'started_at': datetime.now().astimezone().isoformat(), 'target': 'disposable local container', 'migrations': 47}
    Path(ROOT / 'artifacts').mkdir(exist_ok=True)
    with EventLab() as lab:
        if stage in ['backend', 'models', 'all']:
            subprocess.run(['dotnet', 'run', '--project', str(ROOT / 'probes'), '--no-restore', '--', 'models'],
                           cwd=ROOT, env=dict(os.environ, ConnectionStrings__ScrapBook=lab.connection,
                                              SCRAPBOOK_PROBE_ACCOUNT_ID=lab.actors['owner']), check=True)
            report['models'] = 'unified scoped DbContext, five mappings, repository and rollback passed'
        if stage in ['backend', 'all', 'browser']:
            report['http'] = http_checks(lab)
        if stage in ['browser', 'all']:
            report['browser'] = browser(lab)
        if stage in ['backend', 'all']:
            report['failures_and_privacy'] = failures_and_privacy(lab)
        if stage in ['load', 'all']:
            report['performance'] = performance(lab)
    report['finished_at'] = datetime.now().astimezone().isoformat()
    report['passed'] = True
    (ROOT / 'artifacts' / (stage + '-report.json')).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

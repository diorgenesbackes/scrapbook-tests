#!/usr/bin/env python3
"""PP-STORY-001: testes manuais em Compose e volumes exclusivamente descartáveis."""
import concurrent.futures
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
import platform
import subprocess
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from docker_stack import ComposeLab, raw
from event_lab import ROOT, events_local, request


def snapshot(lab):
    tables = json.loads(lab.scalar("SELECT json_agg(format('%I.%I',schemaname,tablename) ORDER BY schemaname,tablename) "
                                  "FROM pg_tables WHERE schemaname IN ('identity','events','records','notifications');"))
    return {table: lab.scalar('SELECT md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id)::text,\'[]\')) FROM ' + table + ' t;')
            for table in tables}


def create(lab, index):
    starts = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    status, _, event = request(lab.ui_url, 'POST', body={'name': 'PP_PRIVATE_NAME_' + str(index),
                              'welcomeMessage': 'PP_PRIVATE_WELCOME', 'startsAt': starts})
    assert status == 201
    return event['id']


def event_records(lab):
    rows = json.loads(lab.scalar("SELECT json_agg(json_build_object('id',id::text,'code',code,'name',name,'welcomeMessage',welcome_message) ORDER BY id) FROM events.events;"))
    origin = getattr(lab, 'values', {}).get('SCRAPBOOK_PUBLIC_ORIGIN', lab.ui_url)
    return [{**row, 'publicUrl': origin + '/e/' + row['code']} for row in rows]


def public_projection(event):
    return {'code': event['code'], 'name': event['name'], 'publicUrl': event.get('publicUrl'),
            'effectiveWelcomeMessage': event['welcomeMessage'] if event['welcomeMessage'] is not None else 'Seja bem-vindo! Compartilhe uma lembrança deste evento.',
            'cover': {'kind': 'default', 'assetKey': 'event-cover-default-v1'}}


def public_path(event):
    return '/api/v1/public/events/' + event['code']


def contracts(lab, events):
    checks = []
    before = snapshot(lab)
    for event in events[:2]:
        for base in [lab.ui_url, lab.api_url]:
            status, headers, data = request(base, path=public_path(event))
            assert status == 200 and data == public_projection(event)
            assert headers['Cache-Control'] == 'no-store'
            uuid.UUID(headers['X-Correlation-Id'])
            assert 'Set-Cookie' not in headers
    checks.append('public_projection_direct_and_proxy')
    position = next(i for i, character in enumerate(events[0]['code'][:-1]) if character.isalpha())
    original = events[0]['code']
    different_case = original[:position] + original[position].swapcase() + original[position+1:]
    for code in ['1', 'bad', 'A' * 43, 'A' * 42 + 'B', different_case, events[0]['code'] + '=']:
        status, headers, data = request(lab.ui_url, path='/api/v1/public/events/' + code)
        assert status == 404 and data['code'] == 'RESOURCE_NOT_FOUND'
        assert headers['Content-Type'].startswith('application/problem+json')
        assert data['correlationId'] == headers['X-Correlation-Id']
        assert 'instance' not in data
    checks.append('invalid_unknown_pk_case_and_padding')
    for correlation in ['not-a-uuid', str(uuid.uuid4())]:
        status, headers, _ = request(lab.ui_url, path=public_path(events[0]), headers={'X-Correlation-Id': correlation})
        assert status == 200
        assert str(uuid.UUID(headers['X-Correlation-Id'])) == headers['X-Correlation-Id']
        if correlation != 'not-a-uuid':
            assert headers['X-Correlation-Id'] == correlation
    assert request(lab.ui_url, path=public_path(events[0]) + '?accountId=1')[0] == 400
    checks.append('correlation_and_query_validation')
    # A valid public code is not a management PK or identity.
    assert request(lab.ui_url, path='/api/v1/events/' + events[0]['code'])[0] == 400
    outsider = lab.scalar("INSERT INTO identity.accounts(subject,name,email,normalized_email,email_verified,state) "
                          "VALUES ('development:gestao-eventos:other','Other fixture','pp-other@example.invalid',"
                          "'pp-other@example.invalid',true,'active') RETURNING id;")
    lab.values['SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID'] = outsider
    lab.save_environment()
    lab.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service')
    lab.wait_proxy()
    assert request(lab.ui_url, path='/api/v1/events/' + events[0]['id'])[0] == 404
    assert request(lab.ui_url, path=public_path(events[0]))[2] == public_projection(events[0])
    lab.values.update(SCRAPBOOK_ENVIRONMENT='Production', SCRAPBOOK_DEVELOPMENT_IDENTITY='false',
                      SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID='0')
    lab.save_environment()
    lab.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service')
    lab.wait_proxy()
    assert request(lab.ui_url, path='/api/v1/events/' + events[0]['id'])[0] == 401
    assert request(lab.ui_url, 'PUT', '/api/v1/events/' + events[0]['id'], {})[0] == 401
    assert request(lab.ui_url, path=public_path(events[0]), headers={'Cookie': 'invalid=ignored', 'Authorization': 'Bearer invalid'})[2] == public_projection(events[0])
    checks.append('anonymous_in_production_and_private_authorization')
    # Only intentional fixture writes may differ from the first snapshot.
    after = snapshot(lab)
    assert all(before[table] == value for table, value in after.items() if table != 'identity.accounts')
    baseline = snapshot(lab)
    for event in events:
        assert request(lab.ui_url, path=public_path(event))[2] == public_projection(event)
    assert snapshot(lab) == baseline
    checks.append('all_application_tables_unchanged_by_reads')
    assert lab.scalar('SELECT count(*) FROM public.databasechangelog;') == '47'
    assert raw(lab.ui_url, '/e/' + events[0]['code'])[0] == 200
    assert raw(lab.ui_url, public_path(events[0]))[1]['Content-Type'].startswith('application/json')
    assert raw(lab.ui_url, '/assets/pp-missing.js')[0] == 404
    checks.append('spa_fallback_assets_api_and_migrations')
    # Read-only port cannot mutate; production tests run with no development actor.
    return checks, baseline


def browser(lab, events):
    manifest = lab.directory / 'public-event-manifest.json'
    events_local.private_write(manifest, json.dumps({'code': events[0]['code'], 'otherCode': events[1]['code']}))
    result = subprocess.run(['npx', 'playwright', 'test', '--config', 'public_event.config.ts'], cwd=ROOT,
                            env=dict(os.environ, SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url,
                                     SCRAPBOOK_PUBLIC_EVENT_MANIFEST=str(manifest)), timeout=180)
    assert result.returncode == 0, 'Public event browser suite failed'
    return {'tests': 6, 'projects': ['chromium-desktop', 'webkit-mobile'], 'physical_devices': False}


def performance(lab, events, *, seconds=60, workers=10):
    barrier = threading.Barrier(workers)

    def worker(index):
        for _ in range(3):
            assert request(lab.ui_url, path=public_path(events[index]))[0] == 200
        barrier.wait(timeout=15)
        deadline, durations, errors, cursor = time.monotonic() + seconds, [], 0, index
        while time.monotonic() < deadline:
            event = events[cursor % len(events)]
            start = time.perf_counter()
            try:
                status, _, body = request(lab.ui_url, path=public_path(event))
                if status != 200 or body != public_projection(event):
                    errors += 1
            except (OSError, ValueError):
                errors += 1
            durations.append((time.perf_counter() - start) * 1000)
            cursor += workers
        return durations, errors

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(worker, range(workers)))
    timings = sorted(value for values, _ in results for value in values)
    errors = sum(value for _, value in results)
    p95 = timings[math.ceil(len(timings) * .95) - 1]
    report = {'events': len(events), 'workers': workers, 'seconds': seconds, 'requests': len(timings),
              'errors': errors, 'p95_ms': round(p95, 3), 'max_ms': round(max(timings), 3)}
    print(json.dumps({'public_event_load': report}), flush=True)
    assert errors == 0 and p95 <= 500
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--functional-only', action='store_true', help='Revalida funcional/navegador, sem repetir carga.')
    args = parser.parse_args()
    report = {'story': 'PP-STORY-001', 'started_at': datetime.now(timezone.utc).isoformat(),
              'python': platform.python_version(), 'environment': 'isolated-local-compose', 'deployed': False}
    with ComposeLab() as lab:
        event_ids = [create(lab, index) for index in range(1000)]
        events = event_records(lab)
        assert len(events) == len(event_ids) == 1000
        print(json.dumps({'fixtures_ready': len(events)}), flush=True)
        lab.sql('ANALYZE events.events;')
        plan = lab.scalar('EXPLAIN (FORMAT JSON) SELECT code FROM events.events WHERE code='
                          + events_local.literal(events[0]['code']) + ';')
        # Existing unique code index, not a forced planner setting or new migration.
        assert 'Index' in plan and 'code' in plan
        report['existing_code_index_used'] = True
        # Validate generated OpenAPI before disabling development-only exposure.
        schema = request(lab.api_url, path='/openapi/v1.json')[2]
        operation = schema['paths']['/api/v1/public/events/{code}']['get']
        assert operation['operationId'] == 'GetPublicEvent' and operation.get('security', []) == []
        report['openapi'] = True
        report['functional'], baseline = contracts(lab, events)
        report['browser'] = browser(lab, events)
        report['load'] = {'executed': False, 'reason': 'functional-only'} if args.functional_only else performance(lab, events)
        assert snapshot(lab) == baseline
        logs = lab.compose('logs', '--no-color', '--tail', '300', 'service').stdout
        assert all(event['code'] not in logs for event in events)
        assert 'PP_PRIVATE_NAME_' not in logs and 'PP_PRIVATE_WELCOME' not in logs
        report['logs_redacted'] = True
        report['tables_unchanged_after_browser_and_load'] = True
        # Regression of the owner shell changed by the introduction of guest routes.
        # Intentional fixture writes start only after the read-only snapshot assertion above.
        from journeys import browser as management_browser
        lab.values.update(SCRAPBOOK_ENVIRONMENT='Development', SCRAPBOOK_DEVELOPMENT_IDENTITY='true',
                          SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=lab.actor)
        lab.save_environment()
        lab.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service')
        lab.wait_proxy()
        report['management_browser_regression'] = management_browser(lab)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    (ROOT / 'artifacts/public-event-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""IC-STORY-001: validação manual somente em Compose/volumes/tag descartáveis próprios."""
import concurrent.futures
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
import secrets
import subprocess
import time
from zoneinfo import ZoneInfo

from docker_stack import ComposeLab
from event_lab import ROOT, LIQUIBASE, INFRA, events_local, request
from public_event import event_records, snapshot, valid_public_projection
from journeys import browser as management_browser


def lb(lab, *args, ok=True):
    return lab.command(['liquibase', '--show-banner=false', '--changelog-file=db.changelog-master.yaml',
                        '--url=jdbc:postgresql://127.0.0.1:' + str(lab.db_port) + '/scrapbook_docker_test',
                        '--username=scrapbook_migrator', *args], cwd=LIQUIBASE, ok=ok,
                       env=dict(lab.environment, LIQUIBASE_COMMAND_PASSWORD=lab.passwords['migrator']))


def migration_checks(lab):
    checks = []
    forward = (LIQUIBASE / 'changelogs/identification/sql/048-provision-consent-notice.sql').read_text()
    rollback = (LIQUIBASE / 'changelogs/identification/rollback/048-provision-consent-notice.sql').read_text()
    expected = json.loads(lab.scalar("SELECT json_build_object('body',body,'effectiveAt',effective_at) FROM records.consent_notices WHERE code='event-access-v1';"))
    for _ in range(2): lab.sql('BEGIN;\n' + forward + '\nCOMMIT;')
    assert lab.scalar('SELECT count(*) FROM public.ic001_consent_notice_provision;') == '1'
    assert lab.scalar("SELECT count(*) FROM records.consent_notices WHERE code='event-access-v1';") == '1'
    lb(lab, 'validate')
    lb(lab, 'update')
    assert lab.scalar('SELECT count(*) FROM public.databasechangelog;') == '48'
    checks.append('48_changesets_forward_twice_and_validate_update')
    lb(lab, 'rollback-count', '--count=1')
    assert lab.scalar("SELECT count(*) FROM records.consent_notices WHERE code='event-access-v1';") == '0'
    lab.sql('BEGIN;\n' + rollback + '\nCOMMIT;')
    assert lab.scalar("SELECT to_regclass('public.ic001_consent_notice_provision') IS NULL;") == 't'
    checks.append('owned_notice_rollback_and_repeat')
    literal = events_local.literal
    def insert(body):
        lab.sql("INSERT INTO records.consent_notices(code,body,body_hash,effective_at) VALUES('event-access-v1',"
                + literal(body) + ',sha256(convert_to(' + literal(body) + ",'UTF8'))," + literal(expected['effectiveAt']) + '::timestamptz);')
    insert(expected['body'])
    preexisting_id = lab.scalar("SELECT id FROM records.consent_notices WHERE code='event-access-v1';")
    lb(lab, 'update')
    assert lab.scalar('SELECT inserted FROM public.ic001_consent_notice_provision;') == 'f'
    lb(lab, 'rollback-count', '--count=1')
    assert lab.scalar("SELECT id FROM records.consent_notices WHERE code='event-access-v1';") == preexisting_id
    checks.append('compatible_preexisting_notice_preserved_by_rollback')
    lab.sql("DELETE FROM records.consent_notices WHERE code='event-access-v1';")
    insert('INCOMPATIBLE_SYNTHETIC_NOTICE')
    assert lb(lab, 'update', ok=False).returncode != 0
    assert lab.scalar("SELECT body FROM records.consent_notices WHERE code='event-access-v1';") == 'INCOMPATIBLE_SYNTHETIC_NOTICE'
    assert lab.scalar('SELECT count(*) FROM public.databasechangelog;') == '47'
    assert lab.scalar("SELECT to_regclass('public.ic001_consent_notice_provision') IS NULL;") == 't'
    lab.sql("DELETE FROM records.consent_notices WHERE code='event-access-v1';")
    lb(lab, 'update')
    checks.append('incompatible_preexisting_notice_rejected_atomically')
    lab.sql("COMMENT ON TABLE public.ic001_consent_notice_provision IS NULL;")
    result = lab.compose('exec', '-T', 'postgres', 'sh', '-c',
                         'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                         stdin='BEGIN;\n'+forward+'\nCOMMIT;', ok=False)
    assert result.returncode != 0
    lab.sql("COMMENT ON TABLE public.ic001_consent_notice_provision IS 'scrapbook:ic-001-048:provenance';")
    checks.append('unowned_marker_not_adopted')
    return checks


def fixtures(lab):
    starts = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    ids = []
    for name in ['Identificação — primeiro evento', 'Identificação — segundo evento']:
        status, _, event = request(lab.ui_url, 'POST', body={'name': name, 'startsAt': starts})
        assert status == 201
        ids.append(event['id'])
    rows = {row['id']: row for row in event_records(lab)}
    return {'first': rows[ids[0]], 'second': rows[ids[1]]}


def contracts(lab, data):
    path = '/api/v1/public/events/' + data['first']['code'] + '/consent-notice'
    checks = []
    baseline = snapshot(lab)
    for base in [lab.api_url, lab.ui_url]:
        status, headers, notice = request(base, path=path)
        assert status == 200 and set(notice) == {'code', 'body'} and notice['code'] == 'event-access-v1'
        for header, value in [('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'), ('X-Content-Type-Options', 'nosniff')]:
            assert headers[header] == value
        data['notice'] = notice
        assert request(base, path=path+'?name=NO_NAME_IN_API')[0] == 400
        assert request(base, path=path.replace(data['first']['code'], 'bad'))[0] == 404
        assert request(base, path=path.replace(data['first']['code'], 'A'*43))[0] == 404
    assert snapshot(lab) == baseline
    checks.append('anonymous_api_proxy_closed_payload_headers_invalid_context_and_read_only')
    schema = request(lab.api_url, path='/openapi/v1.json')[2]
    notice_schema = schema['components']['schemas']['ConsentNoticeResponse']
    assert set(notice_schema['required']) == {'code', 'body'} and notice_schema['additionalProperties'] is False
    assert set(schema['components']['schemas']['PublicEventResponse']['required']) == {'code', 'name', 'effectiveWelcomeMessage', 'cover', 'publicUrl', 'serverTime', 'submissionWindow'}
    checks.append('openapi_closed_notice_and_unchanged_public_projection')
    lab.sql("INSERT INTO records.consent_notices(code,body,body_hash,effective_at) VALUES ('ic-future','FUTURE_NOTICE',sha256('FUTURE_NOTICE'::bytea),CURRENT_TIMESTAMP+INTERVAL '1 day');")
    assert request(lab.ui_url, path=path)[2] == data['notice']
    lab.sql("INSERT INTO records.consent_notices(code,body,body_hash,effective_at) SELECT 'ic-tie','TIE_NOTICE',sha256('TIE_NOTICE'::bytea),effective_at FROM records.consent_notices WHERE code='event-access-v1';")
    assert request(lab.ui_url, path=path)[2]['code'] == 'ic-tie'
    lab.sql("DELETE FROM records.consent_notices WHERE code IN ('ic-future','ic-tie');")
    checks.append('future_notice_excluded_and_effective_at_tie_uses_highest_id')
    # Retirar/restaurar somente o provisionamento deste laboratório, por Liquibase real.
    lb(lab, 'rollback-count', '--count=1')
    status, _, failure = request(lab.ui_url, path=path)
    assert status == 503 and failure['code'] == 'CONSENT_NOTICE_UNAVAILABLE'
    lab.sql("INSERT INTO records.consent_notices(code,body,body_hash,effective_at) VALUES ('ic-only-future','FUTURE_NOTICE',sha256('FUTURE_NOTICE'::bytea),CURRENT_TIMESTAMP+INTERVAL '1 day');")
    assert request(lab.ui_url, path=path)[0] == 503
    lab.sql("DELETE FROM records.consent_notices WHERE code='ic-only-future';")
    lb(lab, 'update')
    checks.append('absent_or_only_future_notice_returns_503_without_fallback')
    lab.sql('REVOKE SELECT (id,code,body,effective_at) ON records.consent_notices FROM scrapbook_runtime;')
    try:
        status, _, failure = request(lab.ui_url, path=path)
        assert status == 500 and failure['code'] == 'UNEXPECTED_ERROR'
        assert all(marker not in json.dumps(failure).lower() for marker in ['select ', 'npgsql', 'password', 'stacktrace'])
    finally: lab.sql('GRANT SELECT (id,code,body,effective_at) ON records.consent_notices TO scrapbook_runtime;')
    assert request(lab.ui_url, path=path)[2] == data['notice']
    checks.append('provider_failure_sanitized_and_manual_recovery')
    for event in [data['first'], data['second']]:
        assert valid_public_projection(request(lab.ui_url, path='/api/v1/public/events/'+event['code'])[2], event)
    assert request(lab.ui_url, path='/api/v1/events/'+data['first']['id'])[0] == 200
    assert request(lab.ui_url, path='/api/v1/events/'+data['first']['code'])[0] == 400
    checks.append('public_projection_and_private_identifier_regression')
    return checks


def load_notice(lab, data):
    path = '/api/v1/public/events/' + data['first']['code'] + '/consent-notice'
    def client():
        samples, errors = [], 0
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                status, _, notice = request(lab.ui_url, path=path)
                if status != 200 or notice != data['notice']: errors += 1
            except (OSError, ValueError): errors += 1
            samples.append((time.monotonic()-started)*1000)
        return samples, errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: client(), range(10)))
    samples = sorted(value for values, _ in results for value in values)
    errors = sum(errors for _, errors in results)
    p95 = samples[math.ceil(len(samples)*.95)-1]
    assert errors == 0 and p95 <= 500
    return {'seconds': 15, 'concurrent_clients': 10, 'queries': len(samples), 'errors': errors, 'p95_ms': round(p95, 3), 'dataset_events': len(event_records(lab)), 'production_sla_proven': False}


def referenced_rollback(lab):
    code = secrets.token_urlsafe(32)
    # Fixture de conjunto válida derivada da suíte de integridade da base, em transação única.
    lab.sql("""DO $fixture$
    DECLARE event_id bigint; notice_id bigint; record_id bigint; operation_id bigint;
        business_now timestamp without time zone := CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo';
    BEGIN
        SELECT id INTO notice_id FROM records.consent_notices WHERE code='event-access-v1';
        INSERT INTO events.events(events_creator_accounts_id,code,name,starts_at,submission_opens_at,submission_closes_at,completion_deadline_at,opens_at,access_state)
        VALUES (""" + lab.actor + ',' + events_local.literal(code) + """,'IC reference fixture',date_trunc('day',business_now),date_trunc('day',business_now)-INTERVAL '2 hours',
            date_trunc('day',business_now)+INTERVAL '1 day 12 hours',date_trunc('day',business_now)+INTERVAL '1 day 13 hours',date_trunc('day',business_now)+INTERVAL '1 year','flexible') RETURNING id INTO event_id;
        INSERT INTO events.event_members(event_members_events_id,event_members_accounts_id,slot,joined_at)
        VALUES(event_id,""" + lab.actor + """,1,CURRENT_TIMESTAMP);
        INSERT INTO records.records(records_events_id,records_consent_notices_id,category,format,sender_name,message,expected_file_count,consented_at,submitted_at,state)
        VALUES(event_id,notice_id,'testimonial','text','Synthetic guest','Synthetic text',0,business_now,business_now,'validating') RETURNING id INTO record_id;
        INSERT INTO records.idempotent_operations(idempotent_operations_events_id,idempotent_operations_records_id,operation,key,payload_hash,authorization_hash,hash_key_id,authorization_expires_at,state)
        VALUES(event_id,record_id,'contribution.submit',gen_random_uuid(),sha256('payload'::bytea),sha256('authorization'::bytea),'synthetic',CURRENT_TIMESTAMP+INTERVAL '1 day','in_progress') RETURNING id INTO operation_id;
        INSERT INTO records.upload_sessions(upload_sessions_events_id,upload_sessions_records_id,upload_sessions_idempotent_operations_id,state,window_opens_at,window_closes_at,completion_deadline_at,started_at)
        SELECT event_id,record_id,operation_id,'validating',submission_opens_at,submission_closes_at,completion_deadline_at,business_now FROM events.events WHERE id=event_id;
    END; $fixture$;""")
    assert lb(lab, 'rollback-count', '--count=1', ok=False).returncode != 0
    assert lab.scalar('SELECT count(*) FROM public.databasechangelog;') == '48'
    assert lab.scalar("SELECT count(*) FROM records.consent_notices WHERE code='event-access-v1';") == '1'
    return True


def management_fixtures(lab):
    starts = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    for index in range(25):
        assert request(lab.ui_url, 'POST', body={'name': 'IC pagination fixture '+str(index), 'startsAt': starts})[0] == 201


def runtime_grants(lab):
    profile = json.loads(lab.scalar("""SELECT json_build_object(
        'notice',has_column_privilege('scrapbook_runtime','records.consent_notices','body','SELECT'),
        'hash',has_column_privilege('scrapbook_runtime','records.consent_notices','body_hash','SELECT'),
        'contributions',has_any_column_privilege('scrapbook_runtime','records.records','SELECT'),
        'marker',has_table_privilege('scrapbook_runtime','public.ic001_consent_notice_provision','SELECT'),
        'ddl',has_schema_privilege('scrapbook_runtime','records','CREATE'));"""))
    assert profile == {'notice': True, 'hash': False, 'contributions': False, 'marker': False, 'ddl': False}
    lab.sql("BEGIN; SELECT set_config('scrapbook.runtime_user','scrapbook_runtime',true);\n"
            + (INFRA/'postgres/development/revoke-runtime.sql').read_text() + '\nCOMMIT;')
    try:
        assert lab.scalar("SELECT rolcanlogin FROM pg_roles WHERE rolname='scrapbook_runtime';") == 'f'
        assert lab.scalar("SELECT has_column_privilege('scrapbook_runtime','records.consent_notices','body','SELECT');") == 'f'
    finally:
        lab.sql("BEGIN; DO $settings$ BEGIN PERFORM set_config('scrapbook.runtime_user','scrapbook_runtime',true); PERFORM set_config('scrapbook.runtime_password',"
                + events_local.literal(lab.passwords['runtime']) + ",true); END; $settings$;\n"
                + (INFRA/'postgres/development/provision-runtime.sql').read_text() + '\nCOMMIT;')
    assert request(lab.ui_url)[0] == 200
    return {'minimal_notice_read': True, 'private_data_denied': True, 'column_grant_revoke_replay': True}


def main(regression_only=False):
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    report = {'story': 'IC-STORY-001', 'started_at': datetime.now(timezone.utc).isoformat(), 'environment': 'isolated-local-compose', 'deployed': False, 'real_screen_reader': False, 'regression_only': regression_only}
    lab = ComposeLab()
    lab.values['SCRAPBOOK_IMAGE_TAG'] = 'ic001-test-' + secrets.token_hex(5)
    lab.save_environment()
    images = ['scrapbook-service:'+lab.values['SCRAPBOOK_IMAGE_TAG'], 'scrapbook-ui:'+lab.values['SCRAPBOOK_IMAGE_TAG']]
    try:
        lab.compose('build', 'service', 'ui')
        with lab:
            report['runtime_grants'] = runtime_grants(lab)
            if regression_only:
                management_fixtures(lab)
                report['management_regression'] = management_browser(lab)
                report.update(passed=True, finished_at=datetime.now(timezone.utc).isoformat())
                print(json.dumps(report), flush=True)
                return
            report['migrations'] = migration_checks(lab)
            data = fixtures(lab)
            report['http_postgresql'] = contracts(lab, data)
            baseline = snapshot(lab)
            manifest = lab.directory / 'identification-manifest.json'
            events_local.private_write(manifest, json.dumps(data))
            result = subprocess.run(['npx', 'playwright', 'test', '--config', 'guest_identification.config.ts'], cwd=ROOT,
                env=dict(os.environ, SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url, SCRAPBOOK_IDENTIFICATION_MANIFEST=str(manifest)), timeout=240)
            assert result.returncode == 0, 'Identification browser suite failed; no automatic assertion retry'
            report['browser'] = {'functional_cases': 14, 'performance_cases': 1, 'physical_devices': False}
            report['page_performance'] = json.loads((ROOT / 'artifacts/guest-identification-performance.json').read_text())
            report['load'] = load_notice(lab, data)
            assert snapshot(lab) == baseline
            report['read_only'] = True
            logs = lab.compose('logs', '--no-color', 'service', 'ui').stdout
            assert all(marker not in logs for marker in [data['first']['code'], data['second']['code'], 'IC_PRIVATE_GUEST', data['notice']['body']])
            report['logs_sanitized'] = True
            report['referenced_notice_rollback_refused'] = referenced_rollback(lab)
            management_fixtures(lab)
            report['management_regression'] = management_browser(lab)
            report['images'] = {service: lab.inspect(service)['Image'] for service in ['service', 'ui', 'postgres']}
        report.update(passed=True, finished_at=datetime.now(timezone.utc).isoformat())
        print(json.dumps(report), flush=True)
    except BaseException as error:
        report.update(passed=False, error_type=type(error).__name__, finished_at=datetime.now(timezone.utc).isoformat())
        raise
    finally:
        target = 'guest-identification-regression-report.json' if regression_only else 'guest-identification-report.json'
        (ROOT / 'artifacts'/target).write_text(json.dumps(report, indent=2)+'\n')
        # Somente imagens com tag aleatória criada por esta execução; nunca prune ou tags do usuário.
        lab.command(['docker', 'image', 'rm', *images], ok=False)
        lab.temp.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regression-only', action='store_true', help='Revalidar somente os grants e as jornadas de gestão em laboratório novo.')
    main(parser.parse_args().regression_only)

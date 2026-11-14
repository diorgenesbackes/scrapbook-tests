#!/usr/bin/env python3
"""PP-STORY-005: falhas/recuperação exclusivamente no laboratório Compose descartável."""
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import time
import uuid
from zoneinfo import ZoneInfo

from docker_stack import ComposeLab, raw
from event_lab import ROOT, INFRA, events_local, request
from public_event import event_records, public_path, snapshot, valid_public_projection


def problem(status, headers, payload, expected_status, expected_code):
    assert status == expected_status
    assert headers['Content-Type'].startswith('application/problem+json')
    assert headers['Cache-Control'] == 'no-store'
    uuid.UUID(headers['X-Correlation-Id'])
    assert payload['correlationId'] == headers['X-Correlation-Id'] and payload['code'] == expected_code
    assert not any(key in payload for key in ['exception', 'stackTrace', 'instance', 'records'])
    assert not any(marker in json.dumps(payload).lower() for marker in ['password', 'select ', 'npgsql', 'scrapbook_runtime'])


def contracts(lab, event):
    before = snapshot(lab)
    checks = []
    for name, suffix in [('empty', ''), ('empty_slash', '/'), ('malformed', '/bad'), ('long', '/'+('A'*1024)),
                         ('encoded_slash', '/x%2Fy'), ('extra_segment', '/x/y'), ('invalid_escape', '/%GG'),
                         ('absent_canonical', '/'+('A'*43))]:
        for base in [lab.api_url, lab.ui_url]:
            status, headers, body = raw(base, '/api/v1/public/events'+suffix)
            if name=='invalid_escape' and base==lab.ui_url and status==400:
                checks.append('invalid_escape_proxy_transport_400')
                continue
            assert status==404 and headers.get('Content-Type','').startswith('application/problem+json'), (name,'api' if base==lab.api_url else 'proxy',status,headers.get('Content-Type'))
            problem(status, headers, json.loads(body), 404, 'RESOURCE_NOT_FOUND')
        checks.append(name)
    # URI rejeitada antes da API não recebe garantia de Problem Details/correlação.
    status, headers, body = raw(lab.api_url, '/api/v1/public/events/%FF')
    assert status in [400, 404]
    if status == 404: problem(status, headers, json.loads(body), 404, 'RESOURCE_NOT_FOUND')
    checks.append('invalid_utf8_api_or_transport_'+str(status))
    for correlation in ['bad', str(uuid.uuid4()), str(uuid.uuid4())+','+str(uuid.uuid4())]:
        status, headers, data = request(lab.ui_url, path='/api/v1/public/events/bad', headers={'X-Correlation-Id': correlation})
        problem(status, headers, data, 404, 'RESOURCE_NOT_FOUND')
        if len(correlation)==36: assert headers['X-Correlation-Id']==correlation
    path = public_path(event)
    # Revogação e restauração de ACL somente do papel/volume descartável: força o fallback 500 real.
    lab.sql('REVOKE SELECT ON events.events FROM scrapbook_runtime;')
    try:
        status, headers, data = request(lab.ui_url, path=path)
        problem(status, headers, data, 500, 'UNEXPECTED_ERROR')
    finally:
        lab.sql('GRANT SELECT ON events.events TO scrapbook_runtime;')
    assert valid_public_projection(request(lab.ui_url, path=path)[2], event)
    checks.append('unexpected_provider_error_sanitized_and_recovered')
    lab.sql("ALTER ROLE scrapbook_runtime NOLOGIN; SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='scrapbook_runtime' AND pid<>pg_backend_pid();")
    try:
        status, headers, data = request(lab.ui_url, path=path)
        problem(status, headers, data, 503, 'DEPENDENCY_UNAVAILABLE')
    finally:
        lab.sql('ALTER ROLE scrapbook_runtime LOGIN;')
    assert valid_public_projection(request(lab.ui_url, path=path)[2], event)
    checks.append('connection_outage_sanitized_and_recovered')
    command=['docker','compose','--project-name',lab.name,'--env-file',str(lab.env_file),'-f',str(INFRA/'compose.yaml'),
             'exec','-T','postgres','sh','-c','PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    locker=subprocess.Popen(command,env=lab.environment,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
    try:
        locker.stdin.write("SET application_name='pp5_locker'; BEGIN; LOCK TABLE events.events IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(15); ROLLBACK;")
        locker.stdin.close()
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if lab.scalar("SELECT count(*) FROM pg_stat_activity WHERE application_name='pp5_locker' AND wait_event='PgSleep';")=='1':break
            time.sleep(.05)
        else:raise AssertionError('Lock exclusivo do laboratório não foi confirmado')
        started=time.monotonic()
        status,headers,data=request(lab.ui_url,path=path)
        elapsed=time.monotonic()-started
        problem(status,headers,data,503,'DEPENDENCY_UNAVAILABLE')
        assert elapsed<5.5
    finally:
        lab.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='pp5_locker';")
        locker.wait(timeout=10)
    assert valid_public_projection(request(lab.ui_url,path=path)[2],event)
    assert snapshot(lab)==before
    checks.append('lock_timeout_sanitized_and_recovered')
    return {'cases':checks,'timeout_seconds':round(elapsed,3),'read_only':True}


def main():
    (ROOT/'artifacts').mkdir(exist_ok=True)
    report={'story':'PP-STORY-005','started_at':datetime.now(timezone.utc).isoformat(),
            'environment':'isolated-local-compose','deployed':False,'physical_devices':False,'real_screen_reader':False}
    with ComposeLab() as lab:
        starts=(datetime.now(ZoneInfo('America/Sao_Paulo'))+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S')
        status,_,created=request(lab.ui_url,'POST',body={'name':'Encontro para recuperar','startsAt':starts,'openingMode':'immediate'})
        assert status==201
        event=next(row for row in event_records(lab) if row['id']==created['id'])
        report['http']=contracts(lab,event)
        baseline=snapshot(lab)
        manifest=lab.directory/'recovery-manifest.json'
        closed=(datetime.now(ZoneInfo('America/Sao_Paulo'))-timedelta(days=2)).strftime('%Y-%m-%dT18:00:00')
        events_local.private_write(manifest,json.dumps({'event':event,'closedStartsAt':closed,'project':lab.name,'envFile':str(lab.env_file),'compose':str(INFRA/'compose.yaml')}))
        result=subprocess.run(['npx','playwright','test','--config','public_recovery.config.ts'],cwd=ROOT,
            env=dict(os.environ,SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url,SCRAPBOOK_RECOVERY_MANIFEST=str(manifest)),timeout=300)
        assert result.returncode==0,'Public recovery browser suite failed'
        report['browser']={'tests':12,'projects':['chromium-desktop','webkit-mobile'],'physical_devices':False,'real_screen_reader':False}
        after=snapshot(lab)
        assert all(value==after[table] for table,value in baseline.items() if table not in {'events.events','events.audit_entries'})
        current=request(lab.ui_url,path='/api/v1/events/'+event['id'])[2]
        assert current['startsAt']==event['startsAt']
        report['metadata_restored']=True
        logs=lab.compose('logs','--no-color','--tail','500','service').stdout
        assert event['code'] not in logs and event['name'] not in logs
        assert 'permission denied for table' not in logs and 'password authentication failed' not in logs
        report['logs_redacted']=True
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    (ROOT/'artifacts/public-recovery-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()

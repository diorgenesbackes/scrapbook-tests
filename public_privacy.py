#!/usr/bin/env python3
"""PP-STORY-006: revisão de privacidade em banco/containers exclusivamente descartáveis."""
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from docker_stack import ComposeLab, raw
from event_lab import ROOT, events_local, request
from event_sharing import configure
from public_event import event_records, public_path, snapshot, valid_public_projection
from submission_window import temporal_host, set_clock


MARKERS = ['PRIVATE_GUEST_NAME', 'PRIVATE_GUEST_TEXT', 'private-media.invalid', 'PRIVATE_STORAGE_KEY']


def headers_safe(headers, *, api=True):
    assert headers['Cache-Control'] == 'no-store'
    assert headers['Referrer-Policy'] == 'no-referrer'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert not any(key in headers for key in ['Set-Cookie', 'Location'])
    if api:
        assert not any(key in headers for key in ['ETag', 'Last-Modified'])


def fixtures(lab):
    starts = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S')
    ids = []
    for mode, name in [('immediate', 'Encontro público'), ('scheduled', 'Encontro público'), ('immediate', 'Outro encontro público')]:
        status, _, value = request(lab.ui_url, 'POST', body={'name': name, 'startsAt': starts,
            'welcomeMessage': 'Mensagem pública <strong>como texto</strong>', 'openingMode': mode,
            **({'openingDate': '2035-01-01'} if mode == 'scheduled' else {})})
        assert status == 201
        ids.append(value['id'])
    rows = {row['id']: row for row in event_records(lab)}
    outsider = lab.scalar("INSERT INTO identity.accounts(subject,name,email,normalized_email,email_verified,state) VALUES "
        "('development:gestao-eventos:other','Outro responsável sintético','gestao-eventos-other@example.invalid',"
        "'gestao-eventos-other@example.invalid',true,'active') RETURNING id;")
    return [rows[event_id] for event_id in ids], outsider


def seed_private_records(lab, events):
    # Grafo completo e constraints habilitadas; não é implementação de envio ou de S3.
    lab.sql("INSERT INTO records.consent_notices(code,body,body_hash,effective_at) VALUES "
        "('pp6-fixture','Aviso privado sintético',sha256(convert_to('Aviso privado sintético','UTF8')),CURRENT_TIMESTAMP);")
    for event in events[:2]:
        for format in ['text', 'photo']:
            text = format == 'text'
            lab.sql("BEGIN; DO $fixture$ DECLARE target bigint; operation_id bigint; file_id bigint; "
                "notice_id bigint:=(SELECT id FROM records.consent_notices WHERE code='pp6-fixture'); "
                "business_now timestamp:=(SELECT submission_opens_at+interval '1 hour' FROM events.events WHERE id="+event['id']+"); BEGIN "
                "INSERT INTO records.records(records_events_id,records_consent_notices_id,category,format,sender_name,message,caption,"
                "expected_file_count,consented_at,submitted_at,state) VALUES ("+event['id']+",notice_id,'testimonial',"
                +events_local.literal(format)+",'PRIVATE_GUEST_NAME',"+("'PRIVATE_GUEST_TEXT https://private-media.invalid/item',NULL,0" if text else "NULL,'PRIVATE_GUEST_TEXT',1")+
                ",business_now,business_now,'validating') RETURNING id INTO target; "
                "INSERT INTO records.idempotent_operations(idempotent_operations_events_id,idempotent_operations_records_id,operation,key,"
                "payload_hash,authorization_hash,hash_key_id,authorization_expires_at,state) VALUES ("+event['id']+",target,'contribution.submit',"
                "gen_random_uuid(),sha256('private-payload'::bytea),sha256('private-authorization'::bytea),'synthetic',CURRENT_TIMESTAMP+interval '720 hours','in_progress') RETURNING id INTO operation_id; "
                "INSERT INTO records.upload_sessions(upload_sessions_events_id,upload_sessions_records_id,upload_sessions_idempotent_operations_id,"
                "state,window_opens_at,window_closes_at,completion_deadline_at,started_at) SELECT id,target,operation_id,'validating',"
                "submission_opens_at,submission_closes_at,completion_deadline_at,business_now FROM events.events WHERE id="+event['id']+"; "
                +("UPDATE records.upload_sessions SET state='confirmed',terminal_at=CURRENT_TIMESTAMP WHERE upload_sessions_records_id=target; "
                  "UPDATE records.idempotent_operations SET state='confirmed',completed_at=CURRENT_TIMESTAMP WHERE id=operation_id; "
                  "UPDATE records.records SET state='available',received_at=submitted_at,confirmed_at=submitted_at WHERE id=target; "
                  "INSERT INTO records.outbox_messages(message_id,type,protocol_version,aggregate_type,aggregate_id,payload,correlation_id,occurred_at,state,available_at) "
                  "VALUES(gen_random_uuid(),'records.record_confirmed',1,'record',target,jsonb_build_object('recordId',target::text),gen_random_uuid(),CURRENT_TIMESTAMP,'pending',CURRENT_TIMESTAMP); "
                  "UPDATE events.events SET first_record_id=target,first_record_confirmed_at=business_now,locked_at=CURRENT_TIMESTAMP WHERE id="+event['id']+"; " if text else
                  "INSERT INTO records.files(files_records_id,position,client_key,kind,declared_type,declared_size,validation_state,processing_state) "
                  "VALUES(target,1,gen_random_uuid(),'photo','image/jpeg',1024,'expected','not_requested') RETURNING id INTO file_id; "
                  "INSERT INTO records.storage_objects(storage_objects_files_id,kind,state,bucket,object_key) "
                  "VALUES(file_id,'temporary','planned','scrapbook-private-fixture','PRIVATE_STORAGE_KEY/'||file_id); ")+
                "END; $fixture$; SET CONSTRAINTS ALL IMMEDIATE; COMMIT;")
    assert lab.scalar('SELECT count(*) FROM records.records;') == '4'
    assert lab.scalar("SELECT count(*) FROM records.records WHERE confirmed_at IS NOT NULL;") == '2'


def normalized(data):
    return {key: value for key, value in data.items() if key != 'serverTime'}


def temporal_contracts(lab, events, base, clock):
    states = []
    results = []
    for event in events[:2]:
        starts = datetime.fromisoformat(event['startsAt'])
        for civil in [starts-timedelta(hours=3), starts, starts+timedelta(days=2)]:
            set_clock(clock, civil.replace(tzinfo=ZoneInfo('America/Sao_Paulo')).astimezone(timezone.utc))
            status, headers, data = request(base, path=public_path(event))
            assert status == 200 and valid_public_projection(data, event)
            headers_safe(headers)
            results.append(data)
            states.append(data['submissionWindow']['state'])
    assert set(states) == {'not_open', 'open', 'closed'}
    return results


def identity_contracts(lab, events, outsider):
    checks = []
    logs = []
    for actor, account in [('anonymous', '0'), ('owner', lab.actor), ('outsider', outsider)]:
        configure(lab, SCRAPBOOK_ENVIRONMENT='Production' if actor == 'anonymous' else 'Development',
            SCRAPBOOK_DEVELOPMENT_IDENTITY='false' if actor == 'anonymous' else 'true', SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=account)
        for event in events[:2]:
            for base in [lab.api_url, lab.ui_url]:
                status, headers, data = request(base, path=public_path(event), headers={
                    'Cookie': 'scrapbook-session=PRIVATE_GUEST_TEXT', 'Authorization': 'Bearer '+event['code'], 'X-Account-Id': lab.actor})
                assert status == 200 and valid_public_projection(data, event)
                assert not any(marker in json.dumps(data) for marker in MARKERS)
                headers_safe(headers)
                # Mesmo contrato com requisição condicional; não emitir 304/ETag de apresentação.
                assert request(base, path=public_path(event), headers={'If-None-Match': '*', 'If-Modified-Since': 'Tue, 01 Jan 2030 00:00:00 GMT'})[0] == 200
            private = '/api/v1/events/'+event['id']
            expected = 401 if actor == 'anonymous' else 200 if actor == 'owner' else 404
            assert request(lab.ui_url, path=private)[0] == expected
            assert request(lab.ui_url, path=private+'/share')[0] == expected
            invalid = '/api/v1/events/'+event['code']
            expected_invalid = 401 if actor == 'anonymous' else 400
            for path in [invalid, invalid+'/share']:
                assert request(lab.ui_url, path=path, headers={'Authorization': 'Bearer '+event['code'], 'X-Account-Id': lab.actor})[0] == expected_invalid
            if actor != 'owner':
                for path, body in [(private, {'name':'Forbidden','startsAt':event['startsAt'],'welcomeMessage':None,'expectedVersion':'0'}),
                                   (private+'/access', {'openingMode':'immediate','openingDate':None,'expectedVersion':'0'})]:
                    status,_,failure=request(lab.ui_url,'PUT',path,body,headers={'Authorization':'Bearer '+event['code']})
                    assert status==expected,(actor,'access' if path.endswith('/access') else 'structure',status,failure.get('code'))
            checks.append({'actor':actor,'gallery_mode':'immediate' if event==events[0] else 'scheduled','public_status':200,'private_status':expected})
        logs.append(lab.compose('logs','--no-color','service').stdout)
    configure(lab, SCRAPBOOK_ENVIRONMENT='Development',SCRAPBOOK_DEVELOPMENT_IDENTITY='true',SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=lab.actor)
    return checks, logs


def query_probe(lab, event):
    connection = 'Host=127.0.0.1;Port='+str(lab.db_port)+';Database=scrapbook_docker_test;Username=scrapbook_runtime;Password='+lab.passwords['runtime']+';Include Error Detail=false'
    result = lab.command(['dotnet',str(ROOT/'probes/bin/Debug/net10.0/ScrapBook.Probes.dll'),'public-privacy'],
        env=dict(os.environ,ConnectionStrings__ScrapBook=connection,SCRAPBOOK_PRIVACY_CODE=event['code']))
    data = json.loads(result.stdout.strip())
    assert data['passed']
    return data


def main():
    (ROOT/'artifacts').mkdir(exist_ok=True)
    report={'story':'PP-STORY-006','started_at':datetime.now(timezone.utc).isoformat(),'deployed':False,
            'environment':'isolated-local-compose','physical_devices':False,'real_screen_reader':False,'remote_telemetry':False}
    with ComposeLab() as lab:
        schemas=request(lab.api_url,path='/openapi/v1.json')[2]['components']['schemas']
        for name,fields in [('PublicEventResponse',{'code','name','effectiveWelcomeMessage','cover','publicUrl','serverTime','submissionWindow'}),
                            ('CoverResponse',{'kind','assetKey'}),('PublicSubmissionWindowResponse',{'state','opensAt','closesAt','timeZone','revalidateAfterMs'})]:
            assert schemas[name]['additionalProperties'] is False and set(schemas[name]['required'])==fields
            assert set(schemas[name]['properties'])==fields
        report['openapi_exact_schemas']=True
        events, outsider = fixtures(lab)
        with temporal_host(lab) as (base,clock):
            before=temporal_contracts(lab,events,base,clock)
            seed_private_records(lab,events)
            baseline=snapshot(lab)
            after=temporal_contracts(lab,events,base,clock)
            assert before==after
        report['temporal_contracts']={'cases':12,'states':['not_open','open','closed'],'same_before_after_private_records':True,'gallery_modes':['immediate','scheduled']}
        print(json.dumps({'phase':'temporal_privacy','passed':True,'cases':12}),flush=True)
        report['fixtures']={'confirmed_texts':2,'provisional_photos':2,'private_storage_metadata':2,'constraints_enabled':True,'actual_s3_files':False}
        report['identity_contracts'],logs=identity_contracts(lab,events,outsider)
        report['query_and_signals']=query_probe(lab,events[0])
        print(json.dumps({'phase':'identity_and_sql_signals','passed':True,'identities':3}),flush=True)
        for path in ['/e/'+events[0]['code'],'/events/'+events[0]['id'],'/invalid-not-a-route']:
            status,headers,html=raw(lab.ui_url,path)
            assert status==200
            headers_safe(headers,api=False)
            assert b'name="referrer" content="no-referrer"' in html
        status,headers,data=request(lab.api_url,path=public_path(events[0]),headers={'Origin':'https://untrusted.invalid'})
        assert status==200 and 'Access-Control-Allow-Origin' not in headers
        status,headers,_=request(lab.api_url,path='/api/v1/events',headers={'Origin':'https://untrusted.invalid'})
        assert status==200 and 'Access-Control-Allow-Origin' not in headers
        assert request(lab.api_url,'POST',body={'name':'Forbidden','startsAt':events[0]['startsAt']},headers={'Origin':'https://untrusted.invalid'})[0]==403
        # Método arbitrário também não pode virar label/valor livre de diagnóstico.
        call=urllib.request.Request(lab.ui_url+public_path(events[0]),method='PRIVATE_GUEST_METHOD')
        try: urllib.request.urlopen(call,timeout=8)
        except urllib.error.HTTPError as error: assert error.code==405
        manifest=lab.directory/'privacy-manifest.json'
        events_local.private_write(manifest,json.dumps({'events':events,'markers':MARKERS}))
        result=subprocess.run(['npx','playwright','test','--config','public_privacy.config.ts'],cwd=ROOT,
            env=dict(os.environ,SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url,SCRAPBOOK_PRIVACY_MANIFEST=str(manifest)),timeout=240)
        assert result.returncode==0,'Privacy browser suite failed'
        report['browser']={'tests':10,'projects':['chromium-desktop','webkit-mobile'],'retries':0}
        lab.compose('stop','service')
        try:
            status,headers,_=raw(lab.ui_url,public_path(events[0]))
            assert 500<=status<600,('stopped_service',status)
            upstream_error_status=status
            headers_safe(headers)
        finally:
            lab.compose('start','--wait','service')
            lab.wait_proxy()
        edge=lab.compose('logs','--no-color','--no-log-prefix','ui').stdout
        logs.append(lab.compose('logs','--no-color','service').stdout)
        all_logs='\n'.join(logs)+edge
        for marker in MARKERS+[event['code'] for event in events]+['PRIVATE_GUEST_METHOD']:
            assert marker not in all_logs,'Diagnóstico expôs dado da fixture'
        edge_events=[json.loads(line) for line in edge.splitlines() if line.startswith('{"component":"edge"')]
        assert edge_events and any(value['status']==upstream_error_status for value in edge_events)
        assert any(value['method']=='OTHER' and value['status']==405 for value in edge_events)
        assert all(set(value)=={'component','operation','method','status','durationSeconds'} for value in edge_events)
        report['edge_signals']={'events':len(edge_events),'fields':['component','operation','method','status','durationSeconds'],'upstream_error_status':upstream_error_status,'safe_upstream_error_observed':True,'arbitrary_method_categorized':True}
        assert snapshot(lab)==baseline
        assert lab.scalar('SELECT count(*) FROM public.databasechangelog;')=='48'
        report['read_only_snapshots']=True
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    (ROOT/'artifacts/public-privacy-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()

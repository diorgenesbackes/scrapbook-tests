#!/usr/bin/env python3
"""PP-STORY-003: compartilhamento/QR em Compose descartável, sem dados reais."""
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
from zoneinfo import ZoneInfo

from docker_stack import ComposeLab
from event_lab import ROOT, events_local, request
from public_event import snapshot


def configure(lab, **values):
    lab.values.update(values)
    lab.save_environment()
    lab.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service')
    lab.wait_proxy()


def contracts(lab):
    start = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    status, _, event = request(lab.ui_url, 'POST', body={'name': 'Um encontro para compartilhar 🎉', 'startsAt': start})
    assert status == 201
    code = lab.scalar('SELECT code FROM events.events WHERE id=' + event['id'] + ';')
    path = '/api/v1/events/' + event['id'] + '/share'
    expected = {'code': code, 'name': event['name'], 'publicUrl': lab.ui_url + '/e/' + code}
    actors = {}
    for actor in ['other', 'second']:
        actors[actor] = lab.scalar("INSERT INTO identity.accounts(subject,name,email,normalized_email,email_verified,state) VALUES ("
            + events_local.literal('development:gestao-eventos:' + actor) + ",'Synthetic sharing fixture',"
            + events_local.literal('gestao-eventos-' + actor + '@example.invalid') + ','
            + events_local.literal('gestao-eventos-' + actor + '@example.invalid') + ",true,'active') RETURNING id;")
    # Valid synthetic accepted invitation, required by the existing deferred invariant.
    lab.sql("BEGIN; INSERT INTO events.invitations(invitations_events_id,invitations_issuer_event_members_id,invitations_recipient_accounts_id,request_key,token_hash,hash_key_id,state,expires_at,accepted_at) SELECT "
            + event['id'] + ",id," + actors['second'] + ",gen_random_uuid(),sha256('sharing-fixture'::bytea),'synthetic','accepted',CURRENT_TIMESTAMP+interval '24 hours',CURRENT_TIMESTAMP FROM events.event_members WHERE event_members_events_id=" + event['id']
            + " AND slot=1; INSERT INTO events.event_members(event_members_events_id,event_members_accounts_id,slot,joined_at) VALUES (" + event['id'] + ',' + actors['second'] + ",2,CURRENT_TIMESTAMP); COMMIT;")
    baseline = snapshot(lab)
    status, headers, result = request(lab.ui_url, path=path)
    assert status == 200 and result == expected and headers['Cache-Control'] == 'no-store'
    assert request(lab.api_url, path=path, headers={'Host': 'hostile.invalid', 'X-Forwarded-Host': 'hostile.invalid', 'X-Forwarded-Proto': 'http'})[2] == expected
    configure(lab, SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=actors['second'])
    assert request(lab.ui_url, path=path)[2] == expected
    configure(lab, SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=actors['other'])
    assert request(lab.ui_url, path=path)[0] == 404
    assert request(lab.ui_url, path='/api/v1/events/' + code + '/share')[0] == 400
    configure(lab, SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=lab.actor, SCRAPBOOK_PUBLIC_ORIGIN='https://sharing.example.invalid')
    external = 'https://sharing.example.invalid/e/' + code
    assert request(lab.ui_url, path=path)[2]['publicUrl'] == external
    assert request(lab.ui_url, path='/api/v1/public/events/' + code)[2]['publicUrl'] == external
    # Explicit empty override: app remains available, but cannot invent sharing URLs.
    override = lab.directory / 'sharing-unconfigured.yaml'
    override.write_text("services:\n  service:\n    environment:\n      PublicSite__Origin: ''\n")
    lab.compose('-f', str(override), 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service')
    lab.wait_proxy()
    status, _, problem = request(lab.ui_url, path=path)
    assert status == 503 and problem['code'] == 'SHARING_NOT_CONFIGURED'
    assert request(lab.ui_url, path='/api/v1/public/events/' + code)[2]['publicUrl'] is None
    configure(lab, SCRAPBOOK_PUBLIC_ORIGIN=lab.ui_url)
    assert request(lab.ui_url, path=path)[2] == expected
    assert snapshot(lab) == baseline
    schema = request(lab.api_url, path='/openapi/v1.json')[2]
    assert schema['paths']['/api/v1/events/{eventId}/share']['get']['operationId'] == 'GetEventSharing'
    return {**expected, 'id': event['id']}, baseline


def main():
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    report = {'story': 'PP-STORY-003', 'started_at': datetime.now(timezone.utc).isoformat(),
              'environment': 'isolated-local-compose', 'paper_or_real_device': False, 'deployed': False}
    with ComposeLab() as lab:
        event, baseline = contracts(lab)
        report['http'] = {'two_responsibles': True, 'outsider_denied': True, 'host_ignored': True,
                          'configured_https_origin': True, 'missing_origin_safe': True, 'openapi': True}
        manifest = lab.directory / 'sharing-manifest.json'
        events_local.private_write(manifest, json.dumps(event))
        env = dict(os.environ, SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url, SCRAPBOOK_SHARING_MANIFEST=str(manifest))
        result = subprocess.run(['npx', 'playwright', 'test', '--config', 'event_sharing.config.ts'], cwd=ROOT, env=env, timeout=240)
        assert result.returncode == 0, 'Sharing browser/independent QR tests failed'
        report['browser'] = {'tests': 10, 'projects': ['chromium-desktop', 'webkit-mobile'], 'decoder': 'jsQR 1.4.0 / pngjs 7.0.0', 'real_device': False}
        assert snapshot(lab) == baseline
        report['read_only'] = True
        configure(lab, SCRAPBOOK_ENVIRONMENT='Production', SCRAPBOOK_DEVELOPMENT_IDENTITY='false', SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID='0')
        assert request(lab.ui_url, path='/api/v1/events/' + event['id'] + '/share')[0] == 401
        assert request(lab.ui_url, path='/api/v1/public/events/' + event['code'])[2]['publicUrl'] == event['publicUrl']
        report['anonymous_public_private_denied'] = True
        logs = lab.compose('logs', '--no-color', '--tail', '300', 'service').stdout
        assert event['code'] not in logs and event['name'] not in logs
        report['logs_redacted'] = True
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (ROOT / 'artifacts/event-sharing-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""PP-STORY-002: apresentação pública em Compose descartável, sem alterar o volume persistente."""
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
from zoneinfo import ZoneInfo

from docker_stack import ComposeLab
from event_lab import ROOT, events_local, request
from public_event import contracts, create, event_records, performance, public_path, public_projection, valid_public_projection, snapshot
from journeys import browser as management_browser


def fixtures(lab):
    starts = (datetime.now(ZoneInfo('America/Sao_Paulo')) + timedelta(days=30)).strftime('%Y-%m-%dT18:30:00')
    definitions = {
        'normal': ('Encontro de amigos', 'Que bom ter você aqui!\nGuarde um momento com carinho. 🎉'),
        'default': ('Mensagem padrão', None),
        'long': ('L' * 120, 'Olá! \n' + '🎉' * 1994),
        'markup': ('<img src=x onerror=globalThis.__PP_XSS=1>', '<script>globalThis.__PP_XSS=1</script>\n& bem-vindo'),
    }
    ids = {}
    for key, (name, message) in definitions.items():
        status, _, event = request(lab.ui_url, 'POST', body={'name': name, 'startsAt': starts, 'welcomeMessage': message})
        assert status == 201
        ids[key] = event['id']
    # A management edit and reset must be reflected by the next public read.
    target = ids['normal']
    current = request(lab.ui_url, path='/api/v1/events/' + target)[2]
    for message in ['Mensagem alterada\ncom acentos 🎉', None, definitions['normal'][1]]:
        status, _, updated = request(lab.ui_url, 'PUT', '/api/v1/events/' + target,
                                    body={'name': current['name'], 'startsAt': current['startsAt'],
                                          'welcomeMessage': message, 'expectedVersion': current['version']})
        assert status == 200
        row = next(event for event in event_records(lab) if event['id'] == target)
        assert valid_public_projection(request(lab.ui_url, path=public_path(row))[2], row)
        assert public_projection(row)['effectiveWelcomeMessage'] == updated['effectiveWelcomeMessage']
        current = updated
    rows = {event['id']: event for event in event_records(lab)}
    return {key: public_projection(rows[event_id]) for key, event_id in ids.items()}


def run_browser(lab, data):
    manifest = lab.directory / 'presentation-manifest.json'
    events_local.private_write(manifest, json.dumps(data))
    result = subprocess.run(['npx', 'playwright', 'test', '--config', 'public_presentation.config.ts'], cwd=ROOT,
                            env=dict(os.environ, SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url,
                                     SCRAPBOOK_PRESENTATION_MANIFEST=str(manifest)), timeout=240)
    assert result.returncode == 0, 'Presentation browser/performance suite failed'
    return json.loads((ROOT / 'artifacts/public-presentation-performance.json').read_text())


def main():
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    report = {'story': 'PP-STORY-002', 'started_at': datetime.now(timezone.utc).isoformat(),
              'environment': 'isolated-local-compose', 'deployed': False, 'real_screen_reader': False}
    with ComposeLab() as lab:
        for index in range(1000):
            create(lab, index)
        data = fixtures(lab)
        events = event_records(lab)
        print(json.dumps({'fixtures_ready': len(events)}), flush=True)
        report['metadata_edit_and_reset'] = True
        cover = ROOT.parent / 'scrapbook-ui/public/assets/event-cover-default-v1.svg'
        report['cover_bytes'] = cover.stat().st_size
        assert report['cover_bytes'] <= 200 * 1024
        assert (ROOT.parent / 'scrapbook-ui/src/shared/styles/tokens.css').read_text() == (ROOT.parent / 'scrapbook-docs/visual-identity/tokens.css').read_text()
        schema = request(lab.api_url, path='/openapi/v1.json')[2]
        required = schema['components']['schemas']['PublicEventResponse']['required']
        assert set(required) == {'code', 'name', 'effectiveWelcomeMessage', 'cover', 'publicUrl', 'serverTime', 'submissionWindow'}
        report['openapi'] = True
        report['functional'], baseline = contracts(lab, events)
        report['page_performance'] = run_browser(lab, data)
        report['api_load'] = performance(lab, events)
        assert snapshot(lab) == baseline
        report['read_only'] = True
        logs = lab.compose('logs', '--no-color', '--tail', '300', 'service').stdout
        assert all(event['code'] not in logs for event in events)
        assert 'PP_PRIVATE_NAME_' not in logs and 'PP_PRIVATE_WELCOME' not in logs
        report['logs_redacted'] = True
        lab.values.update(SCRAPBOOK_ENVIRONMENT='Development', SCRAPBOOK_DEVELOPMENT_IDENTITY='true',
                          SCRAPBOOK_DEVELOPMENT_ACCOUNT_ID=lab.actor)
        lab.save_environment()
        lab.compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service')
        lab.wait_proxy()
        report['management_regression'] = management_browser(lab)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (ROOT / 'artifacts/public-presentation-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

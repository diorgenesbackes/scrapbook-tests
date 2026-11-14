#!/usr/bin/env python3
"""IC-STORY-003: edição com renovação de concordância em stack/volumes/tags descartáveis."""
from datetime import datetime, timezone
import json
import os
import secrets
import subprocess

from docker_stack import ComposeLab
from event_lab import ROOT, events_local, request
from guest_identification import fixtures, management_fixtures
from journeys import browser as management_browser
from public_event import snapshot, valid_public_projection


def main():
    (ROOT/'artifacts').mkdir(exist_ok=True)
    report={'story':'IC-STORY-003','started_at':datetime.now(timezone.utc).isoformat(),
            'environment':'isolated-local-compose','deployed':False,'real_screen_reader':False}
    lab=ComposeLab()
    lab.values['SCRAPBOOK_IMAGE_TAG']='ic003-test-'+secrets.token_hex(5)
    lab.save_environment()
    images=['scrapbook-service:'+lab.values['SCRAPBOOK_IMAGE_TAG'],'scrapbook-ui:'+lab.values['SCRAPBOOK_IMAGE_TAG']]
    try:
        lab.compose('build','service','ui')
        with lab:
            data=fixtures(lab)
            status,_,notice=request(lab.ui_url,path='/api/v1/public/events/'+data['first']['code']+'/consent-notice')
            assert status==200 and set(notice)=={'code','body'}
            data['notice']=notice
            for event in [data['first'],data['second']]:
                assert valid_public_projection(request(lab.ui_url,path='/api/v1/public/events/'+event['code'])[2],event)
            assert lab.scalar('SELECT count(*) FROM public.databasechangelog;')=='48'
            baseline=snapshot(lab)
            manifest=lab.directory/'edit-identification-manifest.json'
            events_local.private_write(manifest,json.dumps(data))
            result=subprocess.run(['npx','playwright','test','--config','edit_identification.config.ts'],cwd=ROOT,
                env=dict(os.environ,SCRAPBOOK_BROWSER_BASE_URL=lab.ui_url,SCRAPBOOK_IDENTIFICATION_MANIFEST=str(manifest)),timeout=240)
            assert result.returncode==0,'Edit identification browser suite failed; no automatic assertion retry'
            assert snapshot(lab)==baseline
            report['read_only_database']=True
            report['browser']={'new_cases':12,'remembered_regression':16,'identification_regression':14,'performance_cases':1,'physical_devices':False}
            report['page_performance']=json.loads((ROOT/'artifacts/guest-identification-performance.json').read_text())
            logs=lab.compose('logs','--no-color','service','ui').stdout
            assert all(marker not in logs for marker in ['IC3_PRIVATE_GUEST','IC2_PRIVATE_GUEST',data['first']['code'],data['second']['code']])
            report['logs_sanitized']=True
            management_fixtures(lab)
            report['management_regression']=management_browser(lab)
        report.update(passed=True,finished_at=datetime.now(timezone.utc).isoformat())
        print(json.dumps(report),flush=True)
    except BaseException as error:
        report.update(passed=False,error_type=type(error).__name__,finished_at=datetime.now(timezone.utc).isoformat())
        raise
    finally:
        (ROOT/'artifacts/edit-identification-report.json').write_text(json.dumps(report,indent=2)+'\n')
        lab.command(['docker','image','rm',*images],ok=False)
        lab.temp.cleanup()


if __name__=='__main__':main()

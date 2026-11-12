"""Validação manual da infraestrutura local e grants; nenhuma URL remota é aceita."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys

INFRA = Path(__file__).resolve().parent.parent / 'scrapbook-infrastructure'
sys.path.insert(0, str(INFRA / 'scripts'))
import events_local  # noqa: E402


def run():
    config = events_local.configuration()

    def runtime_sql(sql, denied=False):
        shell = ('IFS= read -r PGPASSWORD; export PGPASSWORD; '
                 'exec psql -X -qAt -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -h 127.0.0.1 -U '
                 + config['POSTGRES_RUNTIME_USER'] + ' -d ' + config['POSTGRES_DB'])
        result = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'sh', '-c', shell],
                                cwd=INFRA, input=config['POSTGRES_RUNTIME_PASSWORD'] + '\n' + sql,
                                text=True, capture_output=True)
        if denied:
            assert result.returncode != 0 and '42501' in result.stderr, 'Expected permission denial'
        else:
            assert result.returncode == 0, 'Runtime SQL failed (private diagnostics suppressed)'
        return result.stdout.strip()

    def role_profile():
        user = events_local.literal(config['POSTGRES_RUNTIME_USER'])
        return json.loads(events_local.admin_sql('''
SELECT json_build_object('superuser', rolsuper, 'createdb', rolcreatedb, 'createrole', rolcreaterole,
    'replication', rolreplication, 'bypassrls', rolbypassrls,
    'database_create', has_database_privilege(rolname, current_database(), 'CREATE'),
    'temp', has_database_privilege(rolname, current_database(), 'TEMPORARY'),
    'events_create', has_schema_privilege(rolname, 'events', 'CREATE'),
    'records_usage', has_schema_privilege(rolname, 'records', 'USAGE'),
    'identity_update', has_any_column_privilege(rolname, 'identity.accounts', 'UPDATE'))
FROM pg_roles WHERE rolname = ''' + user + ';'))

    with contextlib.redirect_stdout(io.StringIO()):
        events_local.provision(config)
        events_local.provision(config)
        events_local.bootstrap_actor('owner')
    first = json.loads((events_local.STATE / 'actors.json').read_text())['owner']['accountId']
    with contextlib.redirect_stdout(io.StringIO()):
        events_local.bootstrap_actor('owner')
    assert first == json.loads((events_local.STATE / 'actors.json').read_text())['owner']['accountId']
    collision_email = 'gestao-eventos-collision-probe@example.invalid'
    assert events_local.admin_sql('SELECT count(*) FROM identity.accounts WHERE normalized_email='
                                  + events_local.literal(collision_email) + ';') == '0', 'Collision fixture is already in use'
    events_local.admin_sql("INSERT INTO identity.accounts(subject,name,email,normalized_email,email_verified,state) VALUES ('development:collision-probe:foreign','Synthetic collision',"
                           + events_local.literal(collision_email) + ',' + events_local.literal(collision_email) + ",true,'active');")
    try:
        refused = False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                events_local.bootstrap_actor('collision-probe')
        except ValueError:
            refused = True
        assert refused, 'Colliding non-fixture account was accepted'
        assert events_local.admin_sql("SELECT count(*) FROM identity.accounts WHERE subject='development:collision-probe:foreign';") == '1'
    finally:
        events_local.admin_sql("DELETE FROM identity.accounts WHERE subject='development:collision-probe:foreign' AND normalized_email="
                               + events_local.literal(collision_email) + ';')
    with contextlib.redirect_stdout(io.StringIO()):
        events_local.bootstrap_actor('other')
        events_local.rollback_actor('other')
        events_local.rollback_actor('other')
    assert not any(role_profile().values()), 'Runtime has excessive privileges'

    runtime_sql('SELECT id,subject,state,email,email_verified FROM identity.accounts LIMIT 1;')
    for statement in ['CREATE TABLE events.foundation_denied (id integer);',
                      'CREATE TEMP TABLE foundation_denied (id integer);',
                      'UPDATE identity.accounts SET state=state WHERE false;',
                      'TRUNCATE events.events;']:
        runtime_sql('BEGIN; ' + statement + ' ROLLBACK;', denied=True)

    runtime_sql('''
BEGIN;
SET LOCAL lock_timeout='3s';
WITH t AS (SELECT date_trunc('day',clock_timestamp() AT TIME ZONE 'America/Sao_Paulo') + interval '30 days 18 hours' s)
INSERT INTO events.events(events_creator_accounts_id,code,name,starts_at,submission_opens_at,
    submission_closes_at,completion_deadline_at,opening_mode,opens_at,access_state)
SELECT ''' + first + ''',repeat('F',43),'Foundation synthetic',s,s-interval '2 hours',
    date_trunc('day',s)+interval '1 day 12 hours',date_trunc('day',s)+interval '1 day 13 hours',
    'scheduled',date_trunc('day',s)+interval '1 year','flexible' FROM t RETURNING id AS event_id
\\gset
INSERT INTO events.event_members(event_members_events_id,event_members_accounts_id,slot,joined_at)
VALUES (:event_id,''' + first + ''',1,CURRENT_TIMESTAMP);
INSERT INTO events.audit_entries(audit_entries_accounts_id,audit_entries_events_id,actor_type,action,
    subject_type,subject_id,outcome,correlation_id)
VALUES (''' + first + ''',:event_id,'account','events.created','event',:event_id,'success','b34b44ed-6cb1-4868-8ac0-6945f421c74a');
SET CONSTRAINTS ALL IMMEDIATE;
ROLLBACK;
''')

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            events_local.provision(config, revoke=True)
        login = events_local.admin_sql('SELECT rolcanlogin FROM pg_roles WHERE rolname='
                                       + events_local.literal(config['POSTGRES_RUNTIME_USER']) + ';')
        assert login == 'f', 'Rollback did not disable the runtime'
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            events_local.provision(config)

    inventory = json.loads(events_local.admin_sql("SELECT json_build_object('migrations', (SELECT count(*) FROM public.databasechangelog), 'tables', (SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('identity','events','records','notifications') AND table_type='BASE TABLE'));"))
    assert inventory == {'migrations': 47, 'tables': 33}
    print(json.dumps({'suite': 'foundation', 'passed': True, 'idempotent_actor': True,
                      'runtime_dml_rollback': True, 'ddl_denied': True, 'actor_collision_refused': True,
                      'unused_actor_rollback': True,
                      'grants_rollback_replay': True, **inventory}))

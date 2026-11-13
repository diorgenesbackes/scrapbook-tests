"""Contratos funcionais HTTP/PostgreSQL; somente EventLab descartável."""
import concurrent.futures
import json
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
from event_lab import EventLab, request


def run(lab):
    owner, other, second = (lab.urls[key] for key in ['owner', 'other', 'second'])
    today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    future = (today + timedelta(days=30)).isoformat() + 'T18:30:00'
    opening = (today + timedelta(days=400)).isoformat()
    checks = []

    def mark(name):
        checks.append(name)
        print(json.dumps({'check': name, 'passed': True}), flush=True)

    def create(**overrides):
        status, headers, body = request(owner, 'POST', body={'name': 'Synthetic event', 'startsAt': future, **overrides})
        assert status == 201, 'Create must commit before 201: ' + str(body.get('code'))
        assert headers['Location'] == '/api/v1/events/' + body['id']
        assert isinstance(body['version'], str) and isinstance(body['id'], str)
        return body

    def get(event, base=owner):
        status, _, body = request(base, path='/api/v1/events/' + event['id'])
        assert status == 200
        return body

    def edit(event, *, base=owner, **overrides):
        payload = {key: event[key] for key in ['name', 'startsAt', 'welcomeMessage']}
        return request(base, 'PUT', '/api/v1/events/' + event['id'],
                       {**payload, 'expectedVersion': event['version'], **overrides})

    def access(event, mode, date=None):
        return request(owner, 'PUT', '/api/v1/events/' + event['id'] + '/access',
                       {'openingMode': mode, 'openingDate': date, 'expectedVersion': event['version']})

    def problem(result, status, code):
        actual, headers, body = result
        assert actual == status and body['code'] == code, 'Unexpected failure contract: ' + str((actual, body.get('code')))
        assert headers['Content-Type'].startswith('application/problem+json')
        assert headers['Cache-Control'] == 'no-store' and str(uuid.UUID(headers['X-Correlation-Id'])) == body['correlationId']
        assert body['status'] == status and body['type'] == 'about:blank'
        assert not any(term in json.dumps(body).lower() for term in ['stacktrace', 'password=', 'select e.', 'npgsql'])

    first = create(name='  Contract marker  ')
    assert first['name'] == 'Contract marker' and first['welcomeMessage'] is None
    assert first['cover'] == {'kind': 'default', 'assetKey': 'event-cover-default-v1'}
    graph = json.loads(lab.scalar("SELECT json_build_array((SELECT count(*) FROM events.events),(SELECT count(*) FROM events.event_members),(SELECT count(*) FROM events.audit_entries));"))
    assert graph == [1, 1, 1]
    mark('GE-036/088 creation_event_member_audit_commit')

    for actor in ['disabled', 'invalid']:
        problem(request(lab.urls[actor]), 401, 'IDENTITY_AUTHENTICATION_REQUIRED')
        assert request(lab.urls[actor], path='/health')[0] == 200
    problem(request(owner, 'POST', body={'name': 'Denied', 'startsAt': future}, headers={'Origin': 'https://foreign.invalid'}), 403, 'REQUEST_ORIGIN_NOT_ALLOWED')
    assert request(owner, 'OPTIONS', headers={'Origin': lab.ui_url, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})[0] == 204
    problem(request(owner, path='/api/v1/events?accountId=' + lab.actors['other']), 400, 'REQUEST_VALIDATION_FAILED')
    assert request(other)[2]['items'] == []
    absent = '/api/v1/events/9223372036854775807'
    path = '/api/v1/events/' + first['id']
    for target in [path, absent]:
        problem(request(other, path=target), 404, 'RESOURCE_NOT_FOUND')
        problem(request(other, 'PUT', target, {'name': 'Denied', 'startsAt': future, 'welcomeMessage': None, 'expectedVersion': '0'}), 404, 'RESOURCE_NOT_FOUND')
        problem(request(other, 'PUT', target + '/access', {'openingMode': 'immediate', 'openingDate': None, 'expectedVersion': '0'}), 404, 'RESOURCE_NOT_FOUND')
    mark('GE-089/090 identity_origin_preflight_and_account_isolation')

    # Valid second member with accepted invitation, wholly synthetic and local.
    lab.sql("BEGIN; INSERT INTO events.invitations(invitations_events_id,invitations_issuer_event_members_id,invitations_recipient_accounts_id,request_key,token_hash,hash_key_id,state,expires_at,accepted_at) SELECT "
            + first['id'] + ",id," + lab.actors['second'] + ",gen_random_uuid(),sha256('second-fixture'::bytea),'synthetic','accepted',CURRENT_TIMESTAMP+interval '24 hours',CURRENT_TIMESTAMP FROM events.event_members WHERE event_members_events_id=" + first['id']
            + " AND slot=1; INSERT INTO events.event_members(event_members_events_id,event_members_accounts_id,slot,joined_at) VALUES (" + first['id'] + ',' + lab.actors['second'] + ",2,CURRENT_TIMESTAMP); COMMIT;")
    assert any(item['id'] == first['id'] for item in request(second)[2]['items'])
    shared = get(first, second)
    status, _, shared = edit(shared, base=second, name='Second responsible edit')
    assert status == 200
    status, _, shared = request(second, 'PUT', path + '/access', {'openingMode': 'immediate', 'openingDate': None, 'expectedVersion': shared['version']})
    assert status == 200 and get(first)['openingMode'] == 'immediate'
    mark('GE-091 equivalent_second_responsible_permissions')

    for name, expected in [('😀' * 120, 201), ('😀' * 121, 400), ('\u0085\ufeff  \ufeff', 400), ('A\u0000B', 400), ('\ud800', 400)]:
        assert request(owner, 'POST', body={'name': name, 'startsAt': future})[0] == expected
    for size, expected in [(2000, 201), (2001, 400)]:
        assert request(owner, 'POST', body={'name': 'Unicode', 'startsAt': future, 'welcomeMessage': '😀' * size})[0] == expected
    invalid = [{'accountId': '1'}, {'version': '0'}, {'submissionWindow': {}}, {'startsAt': future + 'Z'},
               {'startsAt': future + '-03:00'}, {'openingMode': 'unknown'}, {'openingMode': 'immediate', 'openingDate': opening}]
    for extra in invalid:
        problem(request(owner, 'POST', body={'name': 'Invalid', 'startsAt': future, **extra}), 400, 'REQUEST_VALIDATION_FAILED')
    problem(request(owner, 'POST', raw=b'{' + b' ' * 32768), 413, 'REQUEST_TOO_LARGE')
    problem(request(owner, 'POST', raw=b'{}', headers={'Content-Type': 'text/plain'}), 415, 'UNSUPPORTED_MEDIA_TYPE')
    problem(request(owner, path='/api/v1/events/9223372036854775808'), 400, 'REQUEST_VALIDATION_FAILED')
    problem(edit(shared, expectedVersion=0), 400, 'REQUEST_VALIDATION_FAILED')
    status, headers, _ = request(owner, headers={'X-Correlation-Id': 'untrusted-text'})
    assert status == 200 and str(uuid.UUID(headers['X-Correlation-Id'])) != 'untrusted-text'
    mark('GE-048/051/092 strict_json_unicode_bigint_and_body_limit')

    fresh = create()
    before = get(fresh)
    assert edit(before)[2]['version'] == before['version']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: edit(before, name=name), ['Race A', 'Race B']))
    assert sorted(result[0] for result in results) == [200, 409]
    problem(next(result for result in results if result[0] == 409), 409, 'EVENT_VERSION_CONFLICT')
    saved = get(fresh)
    database = json.loads(lab.scalar('SELECT json_build_object(\'version\',version::text,\'accessVersion\',access_version,\'name\',name) FROM events.events WHERE id=' + fresh['id'] + ';'))
    assert saved['version'] == database['version'] and saved['name'] == database['name']
    status, _, changed = edit(saved, welcomeMessage='Custom welcome')
    assert status == 200 and changed['effectiveWelcomeMessage'] == 'Custom welcome'
    assert edit(changed, welcomeMessage=None)[2]['welcomeMessage'] is None
    mark('GE-041/044/045/096 optimistic_race_noop_null_reset_final_trigger_state')

    # Use actual DB clock: closed event is two days in the past, never a fabricated clock.
    past = (today - timedelta(days=2)).isoformat() + 'T18:30:00'
    closed = create(startsAt=past, openingDate=opening)
    assert closed['accessState'] == 'scheduled_locked' and not closed['canEditAccess']
    assert closed['submissionWindow']['closesAt'].endswith('T12:00:00')
    assert closed['submissionWindow']['completionDeadlineAt'].endswith('T13:00:00')
    problem(access(closed, 'immediate'), 409, 'EVENT_OPENING_LOCKED')
    moved_result = edit(closed, startsAt=future)
    assert moved_result[0] == 200 and moved_result[2]['accessState'] == 'scheduled_locked'
    immediate = create(startsAt=past, openingMode='immediate')
    assert immediate['accessState'] == 'immediate_once'
    problem(access(immediate, 'scheduled', today.isoformat()), 400, 'REQUEST_VALIDATION_FAILED')
    status, _, once = access(immediate, 'scheduled', opening)
    assert status == 200 and once['accessState'] == 'scheduled_locked'
    problem(access(once, 'scheduled', (today + timedelta(days=401)).isoformat()), 409, 'EVENT_OPENING_LOCKED')
    assert access(once, 'scheduled', opening)[2]['version'] == once['version']
    problem(edit(once, startsAt=(today + timedelta(days=800)).isoformat() + 'T18:30:00'), 400, 'REQUEST_VALIDATION_FAILED')
    assert get(once)['startsAt'] == once['startsAt']
    mark('GE-093/094/095 real_clock_window_12_13_and_irreversible_access_policy')

    # Inject failures ONLY in this disposable database; production migrations remain immutable.
    counts = lab.scalar('SELECT count(*) FROM events.events;')
    lab.sql("CREATE FUNCTION events.ge_test_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure' USING ERRCODE='23514'; END $$; CREATE TRIGGER ge_test_reject_audit BEFORE INSERT ON events.audit_entries FOR EACH ROW EXECUTE FUNCTION events.ge_test_reject_audit();")
    try:
        problem(request(owner, 'POST', body={'name': 'Atomic failure', 'startsAt': future}), 500, 'UNEXPECTED_ERROR')
        assert lab.scalar('SELECT count(*) FROM events.events;') == counts
    finally:
        lab.sql('DROP TRIGGER ge_test_reject_audit ON events.audit_entries; DROP FUNCTION events.ge_test_reject_audit();')
    lab.sql("CREATE FUNCTION events.ge_test_reject_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.name='GE_TEST_COMMIT_FAILURE' THEN RAISE EXCEPTION 'synthetic deferred failure' USING ERRCODE='23514'; END IF; RETURN NULL; END $$; CREATE CONSTRAINT TRIGGER ge_test_reject_commit AFTER INSERT ON events.events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION events.ge_test_reject_commit();")
    try:
        problem(request(owner, 'POST', body={'name': 'GE_TEST_COMMIT_FAILURE', 'startsAt': future}), 500, 'UNEXPECTED_ERROR')
        assert lab.scalar('SELECT count(*) FROM events.events;') == counts
    finally:
        lab.sql('DROP TRIGGER ge_test_reject_commit ON events.events; DROP FUNCTION events.ge_test_reject_commit();')
    mark('GE-046/088/097 early_and_deferred_commit_failures_rollback_all_effects')

    for state in ['pending', 'requested', 'dispatched']:
        event = create()
        lab.sql("INSERT INTO events.opening_dispatches(opening_dispatches_events_id,opening_dispatches_event_members_id,access_version,due_at,state,requested_at,dispatched_at) SELECT e.id,m.id,e.access_version,e.opens_at,"
                + "'" + state + "'," + ('NULL' if state == 'pending' else 'CURRENT_TIMESTAMP') + ',' + ('CURRENT_TIMESTAMP' if state == 'dispatched' else 'NULL')
                + ' FROM events.events e JOIN events.event_members m ON m.event_members_events_id=e.id WHERE e.id=' + event['id'] + ' AND m.slot=1;')
        if state == 'pending':
            lab.sql("CREATE FUNCTION events.ge_test_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure' USING ERRCODE='23514'; END $$; CREATE TRIGGER ge_test_reject_audit BEFORE INSERT ON events.audit_entries FOR EACH ROW EXECUTE FUNCTION events.ge_test_reject_audit();")
            try:
                problem(access(event, 'immediate'), 500, 'UNEXPECTED_ERROR')
                assert lab.scalar('SELECT state FROM events.opening_dispatches WHERE opening_dispatches_events_id=' + event['id']) == 'pending'
                assert get(event)['version'] == event['version']
            finally:
                lab.sql('DROP TRIGGER ge_test_reject_audit ON events.audit_entries; DROP FUNCTION events.ge_test_reject_audit();')
        assert access(event, 'immediate')[0] == 200
        assert lab.scalar('SELECT state FROM events.opening_dispatches WHERE opening_dispatches_events_id=' + event['id']) == ('dispatched' if state == 'dispatched' else 'cancelled')
    mark('GE-042/099 cancel_obsolete_dispatches_atomically_preserve_dispatched')

    for _ in range(22):
        create()
    page = request(owner)[2]
    assert len(page['items']) == 20 and page['hasMore']
    ids, cursor = [], None
    while True:
        page = request(owner, path='/api/v1/events?limit=5' + ('&cursor=' + quote(cursor) if cursor else ''))[2]
        ids.extend(item['id'] for item in page['items'])
        if not page['hasMore']:
            assert page['nextCursor'] is None
            break
        cursor = page['nextCursor']
    assert len(ids) == len(set(ids)) == int(lab.scalar('SELECT count(*) FROM events.events;'))
    token = request(owner, path='/api/v1/events?limit=1')[2]['nextCursor']
    problem(request(other, path='/api/v1/events?cursor=' + quote(token)), 400, 'REQUEST_VALIDATION_FAILED')
    for suffix in ['limit=0', 'limit=51', 'limit=x', 'limit=1&limit=2', 'cursor=bad', 'cursor=' + 'x' * 2049]:
        problem(request(owner, path='/api/v1/events?' + suffix), 400, 'REQUEST_VALIDATION_FAILED')
    restarted = lab.start_api('restarted', account=lab.actors['owner'])
    assert request(restarted, path='/api/v1/events?cursor=' + quote(token))[0] == 200
    mark('GE-037/038/098 keyset_pagination_account_binding_and_process_restart')

    status, _, spec = request(owner, path='/openapi/v1.json')
    operations = [operation for path, verbs in spec['paths'].items() for method, operation in verbs.items() if method in ['post', 'get', 'put']]
    assert status == 200 and len(operations) == 5
    assert {op['operationId'] for op in operations} == {'CreateEvent', 'ListEvents', 'GetEvent', 'UpdateEvent', 'UpdateEventAccess'}
    schema = spec['components']['schemas']['UpdateEventRequest']
    assert set(schema['required']) == {'name', 'startsAt', 'welcomeMessage', 'expectedVersion'}
    assert schema['additionalProperties'] is False
    assert 'string' in schema['properties']['expectedVersion']['type']
    assert 'null' in schema['properties']['welcomeMessage']['type']
    mark('GE-059 openapi_five_operations_and_strict_nullable_contracts')
    return checks


if __name__ == '__main__':
    with EventLab() as environment:
        results = run(environment)
        print(json.dumps({'suite': 'events_http', 'passed': True, 'checks': results,
                          'database': 'disposable', 'migrations': 47,
                          'limits': ['Exact wall-clock noon not executed; unit boundaries plus real DB 12h/13h window and closed policy tested.']}))

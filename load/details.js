import { check, sleep } from 'k6'
import exec from 'k6/execution'
import http from 'k6/http'
import { Counter } from 'k6/metrics'

const actors = [
  { origin: __ENV.SCRAPBOOK_LOAD_ORIGIN, authorization: __ENV.SCRAPBOOK_LOAD_AUTHORIZATION },
  { origin: __ENV.SCRAPBOOK_LOAD_SECOND_ORIGIN, authorization: __ENV.SCRAPBOOK_LOAD_SECOND_AUTHORIZATION },
]
const eventId = __ENV.SCRAPBOOK_LOAD_DETAILS_EVENT_ID
if (!/^[1-9][0-9]*$/.test(eventId ?? '')) throw new Error('Informe o evento descartável.')
for (const { origin, authorization } of actors) {
  if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(origin ?? '') || !/^Development [a-f0-9]{64}$/.test(authorization ?? '')) {
    throw new Error('Use a stack local descartável de eventos.')
  }
}

const versionConflicts = new Counter('details_version_conflicts')
const acceptedChanges = new Counter('details_accepted_changes')

export const options = {
  vus: 6,
  duration: '10s',
  thresholds: {
    'http_req_duration{name:replace_details}': ['p(95)<500'],
    http_req_failed: ['rate==0'],
    checks: ['rate==1'],
  },
}

export default function () {
  const { origin, authorization } = actors[(exec.vu.idInTest - 1) % actors.length]
  const path = origin + '/api/v1/events/' + eventId
  const headers = { Authorization: authorization, Origin: origin, 'Content-Type': 'application/json' }
  const detail = http.get(path, { headers, tags: { name: 'details_snapshot' }, timeout: '3s', redirects: 0 })
  if (!check(detail, { 'snapshot disponível': response => response.status === 200 })) return
  const event = detail.json()
  const name = 'Carga sintética ' + exec.vu.idInTest + '-' + exec.vu.iterationInScenario
  const result = http.put(path + '/details', JSON.stringify({
    name, startsAt: event.startsAt, welcomeMessage: null, expectedVersion: event.version,
  }), {
    headers, tags: { name: 'replace_details' }, timeout: '3s', redirects: 0,
    responseCallback: http.expectedStatuses(200, 409),
  })
  const conflict = result.status === 409 && result.json('code') === 'EVENT_VERSION_CONFLICT'
  versionConflicts.add(conflict ? 1 : 0)
  acceptedChanges.add(result.status === 200 ? 1 : 0)
  check(result, { 'alteração ou conflito de versão': response =>
    conflict || (response.status === 200 && response.json('name') === name) })
  sleep(0.2)
}

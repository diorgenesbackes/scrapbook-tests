import { check, sleep } from 'k6'
import exec from 'k6/execution'
import http from 'k6/http'

const actors = [
  { origin: __ENV.SCRAPBOOK_LOAD_ORIGIN, authorization: __ENV.SCRAPBOOK_LOAD_AUTHORIZATION },
  { origin: __ENV.SCRAPBOOK_LOAD_SECOND_ORIGIN, authorization: __ENV.SCRAPBOOK_LOAD_SECOND_AUTHORIZATION },
]
const events = { open: __ENV.SCRAPBOOK_LOAD_OPEN_EVENT_ID, closed: __ENV.SCRAPBOOK_LOAD_CLOSED_EVENT_ID }
for (const id of Object.values(events)) {
  if (!/^[1-9][0-9]*$/.test(id ?? '')) throw new Error('Informe os eventos descartáveis.')
}
for (const { origin, authorization } of actors) {
  if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(origin ?? '') || !/^Development [a-f0-9]{64}$/.test(authorization ?? '')) {
    throw new Error('Use a stack local descartável de eventos.')
  }
}

export const options = {
  vus: 6,
  duration: '10s',
  thresholds: {
    'http_req_duration{name:opening_status_open}': ['p(95)<500'],
    'http_req_duration{name:opening_status_closed}': ['p(95)<500'],
    http_req_failed: ['rate==0'],
    checks: ['rate==1'],
  },
}

export default function () {
  const { origin, authorization } = actors[(exec.vu.idInTest - 1) % actors.length]
  const state = (exec.vu.idInTest + exec.vu.iterationInScenario) % 2 ? 'open' : 'closed'
  const response = http.get(origin + '/api/v1/events/' + events[state] + '/opening-status', {
    headers: { Authorization: authorization }, tags: { name: 'opening_status_' + state }, timeout: '3s', redirects: 0,
  })
  check(response, { 'estado e horário da fixture': result =>
    result.status === 200 && result.json('contentState') === state && result.json('serverTime') === '2028-01-02T03:00:00+00:00',
  })
  sleep(0.2)
}

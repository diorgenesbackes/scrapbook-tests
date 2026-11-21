import { check, sleep } from 'k6'
import exec from 'k6/execution'
import http from 'k6/http'

const actors = [
  { origin: __ENV.SCRAPBOOK_LOAD_ORIGIN, authorization: __ENV.SCRAPBOOK_LOAD_AUTHORIZATION },
  { origin: __ENV.SCRAPBOOK_LOAD_SECOND_ORIGIN, authorization: __ENV.SCRAPBOOK_LOAD_SECOND_AUTHORIZATION },
]
for (const { origin, authorization } of actors) {
  if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(origin ?? '') || !/^Development [a-f0-9]{64}$/.test(authorization ?? '')) {
    throw new Error('Use a stack local descartável de eventos.')
  }
}

export const options = {
  vus: 4,
  duration: '10s',
  thresholds: {
    'http_req_duration{name:create}': ['p(95)<500'],
    'http_req_duration{name:list}': ['p(95)<500'],
    http_req_failed: ['rate==0'],
    checks: ['rate==1'],
  },
}

export default function () {
  const { origin, authorization } = actors[(exec.vu.idInTest - 1) % actors.length]
  const index = exec.scenario.iterationInTest + 1
  const key = '00000000-0000-4000-8000-' + String(index).padStart(12, '0')
  const created = http.post(origin + '/api/v1/events',
    JSON.stringify({ name: 'Carga sintética ' + index, startsAt: '2032-02-29T01:30:00' }), {
      headers: { Authorization: authorization, Origin: origin, 'Content-Type': 'application/json', 'Idempotency-Key': key },
      tags: { name: 'create' }, timeout: '3s', redirects: 0,
    })
  check(created, { 'criação 201': response => response.status === 201 })
  const listed = http.get(origin + '/api/v1/events?pageSize=10', {
    headers: { Authorization: authorization },
    tags: { name: 'list' }, timeout: '3s', redirects: 0,
  })
  check(listed, {
    'listagem com até dez itens': response => response.status === 200
      && Array.isArray(response.json('items')) && response.json('items').length >= 1 && response.json('items').length <= 10,
  })
  sleep(0.5)
}

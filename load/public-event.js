import { check, sleep } from 'k6'
import exec from 'k6/execution'
import http from 'k6/http'

const origin = __ENV.SCRAPBOOK_LOAD_ORIGIN
const codes = [__ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_A, __ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_B]
if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(origin ?? '')
    || codes.some(code => !/^[A-Za-z0-9_-]{43}$/.test(code ?? '')) || codes[0] === codes[1]) {
  throw new Error('Use dois eventos sintéticos da stack local descartável.')
}

export const options = {
  scenarios: { guests: { executor: 'per-vu-iterations', vus: 200, iterations: 3, maxDuration: '30s' } },
  thresholds: { http_req_duration: ['p(95)<=500'], http_req_failed: ['rate==0'], checks: ['rate==1'] },
}

export default function () {
  const code = codes[(exec.vu.idInTest - 1) % codes.length]
  const result = http.get(origin + '/api/v1/public-events/' + code, {
    tags: { name: 'get_public_event', url: 'public-event' }, timeout: '3s', redirects: 0,
  })
  if (result.status !== 200) console.warn('Consulta pública retornou HTTP ' + result.status + ', código de transporte ' + (result.error_code ?? 'ausente'))
  check(result, { 'resposta pública sem cache': response => response.status === 200
    && response.headers['Cache-Control'] === 'no-store' && typeof response.json('name') === 'string' })
  sleep(0.2)
}

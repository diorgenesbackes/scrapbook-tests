import { check } from 'k6'
import { randomBytes } from 'k6/crypto'
import exec from 'k6/execution'
import http from 'k6/http'

const origin = __ENV.SCRAPBOOK_LOAD_ORIGIN
const codes = [__ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_A, __ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_B]
const noticeCode = __ENV.SCRAPBOOK_LOAD_NOTICE_CODE
const guestName = 'Carga sintética F08'
if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(origin ?? '')
    || codes.some(code => !/^[A-Za-z0-9_-]{43}$/.test(code ?? '')) || codes[0] === codes[1]
    || noticeCode !== 'fixture-f08-v1') {
  throw new Error('Use dois eventos e um aviso sintético da stack local descartável.')
}

export const options = {
  scenarios: { guests: { executor: 'per-vu-iterations', vus: 200, iterations: 1, maxDuration: '20s' } },
  thresholds: {
    'http_req_duration{name:confirm_text}': ['p(95)<=500'],
    'http_req_failed{name:confirm_text}': ['rate==0'],
    checks: ['rate==1'],
    iterations: ['count==200'],
  },
}

function endpoint(code, suffix) {
  return origin + '/api/v1/public-events/' + code + '/' + suffix
}

function hex(size) {
  return Array.from(new Uint8Array(randomBytes(size)), value => value.toString(16).padStart(2, '0')).join('')
}

export function setup() {
  return codes.map(code => {
    const answer = http.post(endpoint(code, 'submission-consents'), JSON.stringify({ guestName, noticeCode, accepted: true }), {
      headers: { 'Content-Type': 'application/json' }, tags: { name: 'prepare_consent', url: 'prepare_consent' }, redirects: 0,
    })
    if (answer.status !== 200) throw new Error('O consentimento sintético não foi aceito, HTTP ' + answer.status)
    return answer.json('consentProof')
  })
}

export default function (proofs) {
  const index = (exec.vu.idInTest - 1) % codes.length
  const uuid = hex(16).replace(/^(........)(....)(....)(....)(............)$/, '$1-$2-$3-$4-$5')
  const response = http.post(endpoint(codes[index], 'submissions'), JSON.stringify({
    category: 'testimonial', format: 'text', text: 'Depoimento local sob carga de texto.',
    identification: { guestName, noticeCode, consentProof: proofs[index] },
  }), {
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': uuid, Authorization: 'Bearer ' + hex(32) },
    tags: { name: 'confirm_text', url: 'confirm_text' }, redirects: 0, timeout: '8s',
  })
  check(response, { 'texto confirmado uma vez com recibo mínimo': reply => reply.status === 201
    && reply.json('state') === 'confirmed' && reply.headers['Cache-Control'] === 'no-store' })
}

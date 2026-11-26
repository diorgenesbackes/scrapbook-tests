import { check } from 'k6'
import { randomBytes, sha256 } from 'k6/crypto'
import exec from 'k6/execution'
import http from 'k6/http'

const origin = __ENV.SCRAPBOOK_LOAD_ORIGIN
const storage = __ENV.SCRAPBOOK_LOAD_STORAGE_ORIGIN
const codes = [__ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_A, __ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_B]
const noticeCode = __ENV.SCRAPBOOK_LOAD_NOTICE_CODE
const guestName = 'Carga sintética de foto F08'
const localOrigin = /^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/

if (!localOrigin.test(origin ?? '') || !localOrigin.test(storage ?? '') || origin === storage
    || codes.some(code => !/^[A-Za-z0-9_-]{43}$/.test(code ?? '')) || codes[0] === codes[1]
    || noticeCode !== 'fixture-f08-v1') {
  throw new Error('Informe dois eventos na stack de foto descartável em loopback.')
}

const photo = open(__ENV.SCRAPBOOK_LOAD_PHOTO, 'b')
const digest = sha256(photo, 'base64')

export const options = {
  scenarios: { guests: { executor: 'per-vu-iterations', vus: 200, iterations: 1, maxDuration: '45s' } },
  thresholds: {
    'http_req_duration{name:admit_photo}': ['p(95)<=500'],
    'http_req_duration{name:authorize_photo}': ['p(95)<=500'],
    checks: ['rate==1'],
    http_req_failed: ['rate==0'],
    iterations: ['count==200'],
  },
}

function endpoint(code, suffix) {
  return origin + '/api/v1/public-events/' + code + '/' + suffix
}

function randomHex(size) {
  return Array.from(new Uint8Array(randomBytes(size)), value => value.toString(16).padStart(2, '0')).join('')
}

function uniqueKey() {
  return randomHex(16).replace(/^(........)(....)(....)(....)(............)$/, '$1-$2-$3-$4-$5')
}

function requestOptions(name, headers) {
  return { headers, tags: { name, url: name }, redirects: 0, timeout: '15s' }
}

export function setup() {
  return codes.map(code => {
    const reply = http.post(endpoint(code, 'submission-consents'), JSON.stringify({ guestName, noticeCode, accepted: true }),
      requestOptions('photo_consent', { 'Content-Type': 'application/json' }))
    if (reply.status !== 200) throw new Error('Consentimento sintético recusado. HTTP ' + reply.status)
    return reply.json('consentProof')
  })
}

export default function (proofs) {
  const index = (exec.vu.idInTest - 1) % codes.length
  const path = endpoint(codes[index], 'submissions')
  const attemptKey = uniqueKey()
  const fileKey = uniqueKey()
  const headers = { 'Content-Type': 'application/json', Authorization: 'Bearer ' + randomHex(32) }
  const draft = {
    category: 'testimonial', format: 'photo',
    identification: { guestName, noticeCode, consentProof: proofs[index] },
    files: [{ fileKey, position: 1, declaredMediaType: 'image/png', sizeBytes: photo.byteLength, sha256: digest }],
  }
  const admitted = http.post(path, JSON.stringify(draft),
    requestOptions('admit_photo', { ...headers, 'Idempotency-Key': attemptKey }))
  if (!check(admitted, { 'admissão única da foto': reply => reply.status === 201 && reply.json('state') === 'receiving' })) return

  const authorization = http.post(path + '/' + attemptKey + '/files/' + fileKey + '/upload-authorizations', '{}',
    requestOptions('authorize_photo', headers))
  if (!check(authorization, { 'autorização PUT na fixture': reply => reply.status === 200 && reply.json('method') === 'PUT'
    && typeof reply.json('url') === 'string' && reply.json('url').startsWith(storage + '/') })) return

  const uploaded = http.put(authorization.json('url'), photo, requestOptions('upload_photo', authorization.json('headers')))
  check(uploaded, { 'bytes criaram uma versão temporária': reply => reply.status === 200 && !!reply.headers['X-Amz-Version-Id'] })
}

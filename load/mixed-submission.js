import { check, sleep } from 'k6'
import { randomBytes, sha256 } from 'k6/crypto'
import exec from 'k6/execution'
import http from 'k6/http'
import { Counter, Trend } from 'k6/metrics'

const origin = __ENV.SCRAPBOOK_LOAD_ORIGIN
const storage = __ENV.SCRAPBOOK_LOAD_STORAGE_ORIGIN
const codes = [__ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_A, __ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_B]
const noticeCode = __ENV.SCRAPBOOK_LOAD_NOTICE_CODE
const guestName = 'Convidado sintético da carga F08'
const localOrigin = /^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/

if (!localOrigin.test(origin ?? '') || !localOrigin.test(storage ?? '') || origin === storage
  || codes.some(code => !/^[A-Za-z0-9_-]{43}$/.test(code ?? '')) || codes[0] === codes[1]
  || noticeCode !== 'fixture-f08-v1') {
  throw new Error('Informe a stack descartável em loopback e dois eventos sintéticos.')
}

const photo = fixture('../photos/fixtures/palette.png', 'image/png')
const largePhoto = fixture(__ENV.SCRAPBOOK_LOAD_PHOTO, 'image/png')
const audio = fixture('../media/fixtures/tone.mp3', 'audio/mpeg')
const largerAudio = fixture('../media/fixtures/tone.wav', 'audio/wav')
const video = fixture('../media/fixtures/short.mp4', 'video/mp4')
const started = new Counter('mixed_started')
const completed = new Counter('mixed_completed')
const confirmed = new Counter('mixed_confirmed')
const receiptTime = new Trend('mixed_receipt_ms', true)
const stages = ['mixed_admit', 'mixed_authorize', 'mixed_completion', 'mixed_status']

export const options = {
  scenarios: { guests: { executor: 'per-vu-iterations', vus: 200, iterations: 1, maxDuration: '120s' } },
  thresholds: {
    ...Object.fromEntries(stages.map(name => [`http_req_duration{name:${name}}`, ['p(95)<=500']])),
    checks: ['rate==1'],
    http_req_failed: ['rate==0'],
    iterations: ['count==200'],
    mixed_confirmed: ['count==200'],
  },
}

function fixture(path, type) {
  const bytes = open(path, 'b')
  return { bytes, type, digest: sha256(bytes, 'base64') }
}

function content(index) {
  if (index < 25) return { category: 'testimonial', format: 'text', text: 'Depoimento sintético da carga F08' }
  if (index < 50) return { category: 'testimonial', format: 'photo', samples: [index % 2 ? largePhoto : photo] }
  if (index < 60) return { category: 'moment', format: 'photo', samples: [photo, largePhoto] }
  if (index < 80) return { category: 'testimonial', format: 'audio', samples: [index % 2 ? audio : largerAudio] }
  return { category: index % 2 ? 'moment' : 'testimonial', format: 'video', samples: [video] }
}

function hex(size) {
  return Array.from(new Uint8Array(randomBytes(size)), value => value.toString(16).padStart(2, '0')).join('')
}

function identifier() {
  return hex(16).replace(/^(........)(....)(....)(....)(............)$/, '$1-$2-$3-$4-$5')
}

function endpoint(event, suffix) {
  return origin + '/api/v1/public-events/' + codes[event] + '/' + suffix
}

function requestOptions(name, headers) {
  return { headers, tags: { name, url: name }, redirects: 0, timeout: '12s' }
}

function state(response) {
  try {
    return response.json('state')
  } catch {
    return undefined
  }
}

export function setup() {
  return codes.map((_, event) => {
    const response = http.post(endpoint(event, 'submission-consents'), JSON.stringify({ guestName, noticeCode, accepted: true }),
      requestOptions('mixed_prepare_consent', { 'Content-Type': 'application/json' }))
    if (response.status !== 200) throw new Error('Consentimento sintético recusado. HTTP ' + response.status)
    return response.json('consentProof')
  })
}

function upload(path, file, sample, headers) {
  const grant = http.post(`${path}/files/${file.fileKey}/upload-authorizations`, '{}', requestOptions('mixed_authorize', headers))
  if (!check(grant, { 'autorização PUT na fixture': reply => reply.status === 200 && reply.json('method') === 'PUT'
    && typeof reply.json('url') === 'string' && reply.json('url').startsWith(storage + '/')
    && reply.json('headers.x-amz-checksum-sha256') === sample.digest })) return false
  const uploaded = http.put(grant.json('url'), sample.bytes, requestOptions('mixed_transfer', grant.json('headers')))
  return check(uploaded, { 'PUT criou versão temporária': reply => reply.status === 200 && !!reply.headers['X-Amz-Version-Id'] })
}

function waitForReceipt(path, headers) {
  const end = Date.now() + 90_000
  while (Date.now() < end) {
    sleep(1 + Math.random())
    const response = http.get(path, requestOptions('mixed_status', headers))
    if (response.status !== 200) return check(response, { 'status recuperável': reply => reply.status === 200 })
    const current = state(response)
    if (current === 'confirmed') return check(response, { 'recibo mínimo': reply => !!reply.json('confirmedAt')
      && Object.keys(reply.json()).length === 2 })
    if (current === 'rejected' || current === 'expired') break
  }
  return check(false, { 'tentativa confirmou dentro da observação': result => result })
}

function submit(choice, event, proof) {
  const { samples, ...draft } = choice
  const files = samples?.map((sample, index) => ({
    fileKey: identifier(), position: index + 1, declaredMediaType: sample.type, sizeBytes: sample.bytes.byteLength, sha256: sample.digest,
  }))
  const attempt = identifier()
  const headers = { 'Content-Type': 'application/json', Authorization: 'Bearer ' + hex(32) }
  const response = http.post(endpoint(event, 'submissions'), JSON.stringify({
    ...draft, ...(files ? { files } : {}),
    identification: { guestName, noticeCode, consentProof: proof },
  }), requestOptions('mixed_admit', { ...headers, 'Idempotency-Key': attempt }))
  if (!check(response, { 'admissão da tentativa': reply => reply.status === 201 && state(reply) === (files ? 'receiving' : 'confirmed') })) return false
  if (!files) return check(response, { 'recibo de texto': reply => !!reply.json('confirmedAt') })
  const path = endpoint(event, 'submissions/' + attempt)
  if (!files.every((file, index) => upload(path, file, samples[index], headers))) return false
  const signal = http.post(path + '/completion', '{}', requestOptions('mixed_completion', headers))
  return check(signal, { 'sinal de conclusão reconhecido': reply => [200, 202].includes(reply.status) })
    && waitForReceipt(path, headers)
}

export default function (proofs) {
  const index = exec.vu.idInTest - 1
  const event = Math.floor(index / 100)
  const choice = content(index % 100)
  const tags = { format: choice.format, category: choice.category, synthetic_event: String(event) }
  const start = Date.now()
  started.add(1, tags)
  try {
    if (submit(choice, event, proofs[event])) {
      confirmed.add(1, tags)
      receiptTime.add(Date.now() - start, tags)
    }
  } finally {
    completed.add(1, tags)
  }
}

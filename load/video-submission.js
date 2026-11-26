import { check, sleep } from 'k6'
import { randomBytes } from 'k6/crypto'
import exec from 'k6/execution'
import http from 'k6/http'
import { Counter, Trend } from 'k6/metrics'
import { videoLoadProfile } from './video-submission-profile.js'

const api = __ENV.SCRAPBOOK_LOAD_ORIGIN
const storage = __ENV.SCRAPBOOK_LOAD_STORAGE_ORIGIN
const codes = [__ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_A, __ENV.SCRAPBOOK_LOAD_PUBLIC_CODE_B]
const noticeCode = __ENV.SCRAPBOOK_LOAD_NOTICE_CODE
const guestName = 'Carga sintética de vídeo F08'
const profile = __ENV.SCRAPBOOK_LOAD_VIDEO_PROFILE
const { vus, iterations: repetitions } = videoLoadProfile({ api, storage, codes, noticeCode, profile })
const partSize = 16 * 1024 * 1024
const fixture = JSON.parse(open(__ENV.SCRAPBOOK_LOAD_VIDEO_MANIFEST))
const video = profile === 'admission' ? null : open(__ENV.SCRAPBOOK_LOAD_VIDEO, 'b')
const videoSize = 3 * partSize
if (fixture.sizeBytes !== videoSize || fixture.partSha256.length !== 3 || (video && video.byteLength !== videoSize)) {
  throw new Error('Informe a fixture MP4 de 48 MiB com seu manifesto.')
}
const operations = {
  admit: 'video_admit',
  authorize: 'video_authorize',
  resume: 'video_read_parts',
  complete: 'video_complete',
  status: 'video_status',
}
const admitted = new Counter('video_admitted')
const confirmed = new Counter('video_confirmed')
const putBytes = new Counter('video_put_attempted_bytes')
const acceptedBytes = new Counter('video_put_accepted_bytes')
const resumedBytes = new Counter('video_recognized_bytes')
const retransmittedBytes = new Counter('video_retransmitted_bytes')
const completionTime = new Trend('video_completion_to_receipt_ms', true)
const journeyTime = new Trend('video_journey_to_receipt_ms', true)
const measuredOperations = profile === 'admission' ? [operations.admit] : Object.values(operations)
const latencies = measuredOperations.map(name => [
  'http_req_duration{name:' + name + '}', profile === 'warmup' ? [] : ['p(95)<=500'],
])
const total = vus * repetitions
const transferThresholds = profile === 'admission' ? {} : {
  video_confirmed: ['count==' + total],
  video_put_attempted_bytes: ['count==' + total * videoSize],
  video_put_accepted_bytes: ['count==' + total * videoSize],
  video_recognized_bytes: ['count==' + total * partSize],
  video_retransmitted_bytes: ['count==0'],
}

export const options = {
  scenarios: {
    guests: { executor: 'per-vu-iterations', vus, iterations: repetitions, maxDuration: '2m', gracefulStop: '0s' },
  },
  systemTags: ['name', 'method', 'status', 'error_code', 'scenario', 'expected_response', 'check'],
  summaryTrendStats: ['count', 'min', 'med', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    ...Object.fromEntries(latencies),
    ...transferThresholds,
    'http_req_duration{name:video_put_part}': [],
    'http_req_duration{name:video_authorize_first_part}': profile === 'resume' ? ['p(95)<=500'] : [],
    'video_admitted{event_slot:a}': ['count==' + Math.ceil(vus / 2) * repetitions],
    ...(vus > 1 ? { 'video_admitted{event_slot:b}': ['count==' + (vus / 2) * repetitions] } : {}),
    checks: [{ threshold: 'rate==1', abortOnFail: true }],
    http_req_failed: [{ threshold: 'rate==0', abortOnFail: true }],
    iterations: ['count==' + total],
    video_admitted: ['count==' + total],
  },
}

function randomHex(size) {
  return Array.from(new Uint8Array(randomBytes(size)), value => value.toString(16).padStart(2, '0')).join('')
}

function uniqueKey() {
  return randomHex(16).replace(/^(........)(....)(....)(....)(............)$/, '$1-$2-$3-$4-$5')
}

function endpoint(code, suffix) {
  return api + '/api/v1/public-events/' + code + '/' + suffix
}

function params(name, headers, timeout = '10s') {
  return { headers, tags: { name }, redirects: 0, timeout }
}

export function setup() {
  return codes.map(code => {
    const reply = http.post(endpoint(code, 'submission-consents'),
      JSON.stringify({ guestName, noticeCode, accepted: true }),
      params('video_consent_setup', { Origin: api, 'Content-Type': 'application/json' }))
    if (reply.status !== 200) throw new Error('Consentimento sintético recusado. HTTP ' + reply.status)
    return reply.json('consentProof')
  })
}

function transferPart(file, headers, number, sent) {
  const partDigest = fixture.partSha256[number - 1]
  const grant = http.post(file + '/upload-authorizations', JSON.stringify({ partNumber: number }),
    params(number === 1 ? 'video_authorize_first_part' : operations.authorize, headers))
  if (!check(grant, { 'autorização da parte emitida': reply => reply.status === 200
    && reply.json('method') === 'PUT'
    && reply.json('url').startsWith(storage + '/')
    && reply.json('headers')['x-amz-checksum-sha256'] === partDigest })) return false

  const bytes = video.slice((number - 1) * partSize, number * partSize)
  putBytes.add(bytes.byteLength)
  retransmittedBytes.add(sent.has(number) ? bytes.byteLength : 0)
  sent.add(number)
  const uploaded = http.put(grant.json('url'), bytes,
    params('video_put_part', { ...grant.json('headers'), 'Content-Type': 'application/octet-stream' }, '30s'))
  const accepted = check(uploaded, { 'parte aceita com checksum': reply => reply.status === 200
    && reply.headers['X-Amz-Checksum-Sha256'] === partDigest })
  if (accepted) acceptedBytes.add(bytes.byteLength)
  return accepted
}

function readParts(file, headers, count) {
  const response = http.get(file + '/upload-status', params(operations.resume, headers))
  if (!check(response, { 'consulta multipart válida': reply => reply.status === 200
    && reply.json('mode') === 'multipart' && reply.json('partSizeBytes') === partSize
    && Array.isArray(reply.json('uploadedParts')) })) return null
  const recognized = response.json('uploadedParts')
  if (!check(recognized, { 'partes reconhecidas correspondem às enviadas': parts => parts.length === count
    && parts.every((part, index) => part === index + 1) })) return null
  return recognized
}

function awaitReceipt(attempt, headers, started, completed) {
  const limit = completed + 60_000
  while (Date.now() < limit) {
    const status = http.get(attempt, params(operations.status, headers))
    if (!check(status, { 'status consultável até a confirmação': reply => reply.status === 200
      && ['receiving', 'validating', 'reconciling', 'confirmed'].includes(reply.json('state')) })) return
    if (status.json('state') === 'confirmed') {
      if (check(status, { 'confirmação contém somente recibo': reply =>
        Object.keys(reply.json()).length === 2 && typeof reply.json('confirmedAt') === 'string' })) {
        confirmed.add(1)
        completionTime.add(Date.now() - completed)
        journeyTime.add(Date.now() - started)
      }
      return
    }
    sleep(1)
  }
  check(false, { 'recibo observado em até 60 s na fixture': value => value })
}

export default function (proofs) {
  const started = Date.now()
  const index = (exec.vu.idInTest - 1) % codes.length
  const path = endpoint(codes[index], 'submissions')
  const attemptKey = uniqueKey()
  const fileKey = uniqueKey()
  const attempt = path + '/' + attemptKey
  const file = attempt + '/files/' + fileKey
  const headers = { Origin: api, 'Content-Type': 'application/json', Authorization: 'Bearer ' + randomHex(32) }
  const media = {
    category: index === 0 ? 'moment' : 'testimonial',
    format: 'video',
    identification: { guestName, noticeCode, consentProof: proofs[index] },
    files: [{ ...fixture, fileKey }],
  }
  const admission = http.post(path, JSON.stringify(media),
    params(operations.admit, { ...headers, 'Idempotency-Key': attemptKey }))
  if (!check(admission, { 'admissão de nova tentativa': reply => reply.status === 201
    && reply.json('state') === 'receiving' && reply.headers.Location.endsWith('/' + attemptKey) })) return

  admitted.add(1, { event_slot: index === 0 ? 'a' : 'b' })
  if (profile === 'admission') return

  const sent = new Set()
  if (!transferPart(file, headers, 1, sent)) return
  sleep(1)
  const recognized = readParts(file, headers, 1)
  if (!recognized) return
  resumedBytes.add(recognized.length * partSize)
  for (const number of [1, 2, 3]) {
    if (recognized.includes(number)) continue
    if (!transferPart(file, headers, number, sent)) return
  }
  if (!readParts(file, headers, 3)) return

  const completed = Date.now()
  const completion = http.post(attempt + '/completion', '{}', params(operations.complete, headers))
  if (!check(completion, { 'conclusão do transporte aceita': reply => [200, 202].includes(reply.status) })) return
  awaitReceipt(attempt, headers, started, completed)
}

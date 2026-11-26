import { expect, test, type Page, type Request, type Response } from '@playwright/test'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { appendFile, open, readFile, stat } from 'node:fs/promises'
import { devNull } from 'node:os'
import { promisify } from 'node:util'

type Category = 'moment' | 'testimonial'
type LocalStorage = { endpoint: string; bucket: string; queueUrl: string; region: string }
type Lab = { storage: LocalStorage; clock: string; opening: string; deadline: string; evidence?: string }
type VideoManifest = {
  fileKey: string
  position: number
  declaredMediaType: string
  sizeBytes: number
  sha256: string
  partSha256?: string[]
}

const first = process.env.SCRAPBOOK_TEST_PUBLIC_CODE!
const second = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE!
const eventCode = /^[A-Za-z0-9_-]{43}$/
if (!eventCode.test(first ?? '') || !eventCode.test(second ?? '') || first === second) {
  throw new Error('Informe dois códigos distintos de eventos sintéticos para o envio de vídeo.')
}

const partSize = 16 * 1024 * 1024
const multipartFile = process.env.SCRAPBOOK_TEST_VIDEO_MULTIPART_FILE!
if (!multipartFile) throw new Error('Informe uma fixture de vídeo multipart de 32 MiB ou mais.')

const guest = 'Convidada sintética do vídeo'
const caption = 'Legenda sintética ' + '🎥'.repeat(482)
const run = promisify(execFile)
const iso = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const samples = [
  { category: 'moment', name: 'moment-boundary.mp4', mimeType: 'video/mp4', duration: 60 },
  { category: 'testimonial', name: 'testimonial-boundary.mp4', mimeType: 'video/mp4', duration: 300 },
  { category: 'testimonial', name: 'short.mov', mimeType: 'video/quicktime', duration: 3 },
  { category: 'moment', name: 'short.webm', mimeType: 'video/webm', duration: 3 },
] as const

let lab: Lab | undefined
let uploadOrigin: string

test.beforeAll(async ({}, testInfo) => {
  if (testInfo.config.metadata.target === 'url') {
    const origin = process.env.SCRAPBOOK_TEST_VIDEO_UPLOAD_ORIGIN
    if (!origin || new URL(origin).origin !== origin || !origin.startsWith('https://')) {
      throw new Error('Informe a origem HTTPS do armazenamento de teste em SCRAPBOOK_TEST_VIDEO_UPLOAD_ORIGIN.')
    }
    uploadOrigin = origin
    return
  }

  const path = process.env.SCRAPBOOK_TEST_VIDEO_FIXTURE
  const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
  const opening = process.env.SCRAPBOOK_TEST_VIDEO_OPEN
  const deadline = process.env.SCRAPBOOK_TEST_VIDEO_DEADLINE
  if (!path || !clock || !opening || !deadline || !iso.test(opening) || !iso.test(deadline)
    || !(Date.parse(opening) < Date.parse(deadline))) {
    throw new Error('Informe a fixture e os marcos UTC da stack descartável de vídeo.')
  }
  const { endpoint, bucket, queueUrl, environment } = JSON.parse(await readFile(path, 'utf8'))
  const region = environment?.PhotoSubmissions__Region
  if (!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(endpoint) || !/^scrapbook-f08-media-[a-f0-9]{16}$/.test(bucket)
    || !new RegExp('^' + endpoint.replaceAll('.', '\\.') + '/[0-9]{12}/photo-receipts$').test(queueUrl)
    || !/^[a-z]{2}-[a-z]+-[0-9]$/.test(region)
    || environment.AWS_ACCESS_KEY_ID !== 'test' || environment.AWS_SECRET_ACCESS_KEY !== 'test') {
    throw new Error('O cenário local exige S3 e SQS descartáveis em loopback.')
  }
  uploadOrigin = endpoint
  lab = { storage: { endpoint, bucket, queueUrl, region }, clock, opening, deadline,
    evidence: process.env.SCRAPBOOK_TEST_VIDEO_BROWSER_EVIDENCE }
})

test.beforeEach(async () => {
  if (lab) await setTime(lab.clock, lab.opening)
})

function button(page: Page, name: string) {
  return page.getByRole('button', { name, exact: true })
}

function status(page: Page, message: string) {
  return page.getByRole('status').filter({ hasText: message })
}

function preview(page: Page) {
  return page.getByLabel('Prévia do vídeo', { exact: true })
}

function receipt(page: Page) {
  return page.getByRole('region', { name: 'Recebimento confirmado', exact: true })
}

async function setTime(clock: string, instant: string) {
  const handle = await open(clock, 'r+')
  try {
    await handle.write(instant, 0, 'ascii')
    await handle.sync()
  } finally {
    await handle.close()
  }
}

function digest(buffer: Buffer) {
  return createHash('sha256').update(buffer).digest('base64')
}

async function aws(storage: LocalStorage, service: string, operation: string, ...args: string[]) {
  const result = await run('aws', ['--endpoint-url', storage.endpoint, '--region', storage.region,
    '--output', 'json', '--no-cli-pager', service, operation, ...args], {
    timeout: 15_000,
    env: { PATH: process.env.PATH, AWS_CONFIG_FILE: devNull, AWS_SHARED_CREDENTIALS_FILE: devNull,
      AWS_EC2_METADATA_DISABLED: 'true', AWS_ACCESS_KEY_ID: 'test', AWS_SECRET_ACCESS_KEY: 'test' },
  }).catch(() => { throw new Error(`A operação ${service} ${operation} falhou na fixture local de vídeo.`) })
  return JSON.parse(result.stdout)
}

async function notify(lab: Lab, put: Response, manifest: VideoManifest,
  confirmationKind: 'Put' | 'CompleteMultipartUpload', at: string) {
  const { storage } = lab
  const key = decodeURIComponent(new URL(put.url()).pathname.slice(storage.bucket.length + 2))
  expect(key).toMatch(/^submissions\/temporary\/[0-9a-f]{32}$/)
  const listed = await aws(storage, 's3api', 'list-object-versions', '--bucket', storage.bucket, '--prefix', key)
  expect(listed.Versions).toHaveLength(1)
  const [version] = listed.Versions
  expect(version).toMatchObject({ Key: key, Size: manifest.sizeBytes, IsLatest: true })
  const versionId: string = version.VersionId
  expect(versionId).toMatch(uuid)
  if (confirmationKind === 'Put') expect(await put.headerValue('x-amz-version-id')).toBe(versionId)
  const envelope = { Records: [{
    eventVersion: '2.1', eventSource: 'aws:s3', awsRegion: storage.region, eventTime: at,
    eventName: 'ObjectCreated:' + confirmationKind,
    s3: { s3SchemaVersion: '1.0', configurationId: 'video-fixture',
      bucket: { name: storage.bucket, arn: 'arn:aws:s3:::' + storage.bucket },
      object: { key: encodeURIComponent(key), versionId, size: manifest.sizeBytes, sequencer: '0000000000000001' } },
  }] }
  await aws(storage, 'sqs', 'send-message', '--queue-url', storage.queueUrl, '--message-body', JSON.stringify(envelope))
  return versionId
}

function isUpload(url: URL) {
  return url.origin === uploadOrigin && url.pathname.includes('/submissions/temporary/')
}

function observeAttempt(page: Page, code: string) {
  const base = '/api/v1/public-events/' + code + '/submissions'
  const requests: Request[] = []
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (path === base || path.startsWith(base + '/')) requests.push(request)
  })
  return { base, requests }
}

async function startVideo(page: Page, code: string, category: Category) {
  await page.goto('/e/' + code + (category === 'moment' ? '/momento' : '/depoimento'), { waitUntil: 'domcontentloaded' })
  await button(page, 'Selecionar vídeo').press('Enter')
}

async function reviewWithCaption(page: Page, category: Category) {
  await expect(preview(page)).toBeVisible()
  await button(page, 'Deseja adicionar uma legenda?').press('Enter')
  await page.getByRole('textbox', { name: 'Legenda do vídeo' }).fill(caption)
  await expect(page.getByText('0 caracteres restantes na legenda.')).toBeVisible()
  await button(page, 'Revisar').press('Enter')
  await expect(page.getByRole('heading', { name: category === 'moment' ? 'Revisar momento' : 'Revisar depoimento' })).toBeFocused()
  await expect(page.getByRole('region', { name: 'Prévia da mídia' }).getByText(caption, { exact: true })).toBeVisible()
}

async function identify(page: Page) {
  await expect(button(page, 'Enviar')).toBeDisabled()
  await button(page, 'Confirmar identificação').press('Enter')
  await page.getByRole('textbox', { name: 'Nome ou apelido' }).fill(guest)
  await page.getByRole('checkbox', { name: 'Li o aviso de acesso.' }).check()
  await button(page, 'Continuar').press('Enter')
  await expect(button(page, 'Enviar')).toBeEnabled()
  const blob = await preview(page).getAttribute('src')
  expect(blob?.startsWith('blob:')).toBe(true)
  return blob!
}

async function admitted(attempt: ReturnType<typeof observeAttempt>, category: Category, expectedCaption: string | null = caption) {
  const posts = attempt.requests.filter(request => request.method() === 'POST' && new URL(request.url()).pathname === attempt.base)
  expect(posts).toHaveLength(1)
  const [post] = posts
  const draft = post.postDataJSON()
  expect(draft).toMatchObject({ category, format: 'video', identification: { guestName: guest } })
  expect(draft.caption).toBe(expectedCaption ?? undefined)
  expect(draft.files).toHaveLength(1)
  const manifest: VideoManifest = draft.files[0]
  expect(manifest.fileKey).toMatch(uuid)
  expect(manifest.position).toBe(1)
  const key = await post.headerValue('idempotency-key')
  const authorization = await post.headerValue('authorization')
  expect(Boolean(key && uuid.test(key) && authorization?.startsWith('Bearer '))).toBe(true)
  const path = attempt.base + '/' + key
  return { key, authorization: authorization!, path, url: new URL(path, post.url()).href, manifest }
}

function completionPath(base: string, url: URL) {
  return url.pathname.startsWith(base + '/') && url.pathname.endsWith('/completion')
}

function waitForCompletion(page: Page, base: string) {
  return page.waitForResponse(response => response.request().method() === 'POST' && completionPath(base, new URL(response.url())))
}

async function assertPreReceiptStatus(page: Page, message: string) {
  await expect(status(page, message)).toBeVisible()
  await expect(page.getByRole('progressbar')).toHaveCount(0)
  await expect(receipt(page)).toHaveCount(0)
}

async function confirm(page: Page, admission: Awaited<ReturnType<typeof admitted>>, oldBlob: string,
  category: Category, confirmationKind: 'Put' | 'CompleteMultipartUpload', versionId: string | undefined) {
  await expect(receipt(page).getByRole('heading')).toBeFocused({ timeout: 35_000 })
  await expect(receipt(page).getByText('Sua contribuição foi guardada.')).toBeVisible()
  const response = await page.request.get(admission.url, { headers: { Authorization: admission.authorization } })
  expect(response.status()).toBe(200)
  expect(response.headers()['cache-control']).toBe('no-store')
  const persisted = await response.json()
  expect(Object.keys(persisted).sort()).toEqual(['confirmedAt', 'state'])
  expect(persisted.state).toBe('confirmed')
  const when = Date.parse(persisted.confirmedAt)
  expect(Number.isFinite(when)).toBe(true)
  const timestamp = receipt(page).locator('time')
  expect(Date.parse((await timestamp.getAttribute('datetime')) ?? '')).toBe(when)
  const brasilia = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short', timeZone: 'America/Sao_Paulo' })
  await expect(timestamp).toHaveText(brasilia.format(new Date(when)))
  await expect(receipt(page).getByText('Horário de Brasília', { exact: false })).toBeVisible()
  await expect(page.getByLabel('Prévia do vídeo', { exact: true })).toHaveCount(0)
  await expect(receipt(page).getByText(caption, { exact: true })).toHaveCount(0)
  await expect(receipt(page).getByText(guest, { exact: true })).toHaveCount(0)
  await expect(receipt(page).getByRole('link', { name: /ver|abrir|baixar|excluir/i })).toHaveCount(0)
  expect(await page.evaluate(url => fetch(url).then(() => false, () => true), oldBlob)).toBe(true)
  if (lab?.evidence) {
    const { fileKey, sizeBytes, declaredMediaType } = admission.manifest
    const record = { caseName: test.info().title, category, fileKey, attemptKey: admission.key, sizeBytes, declaredMediaType,
      confirmationKind, confirmedAt: persisted.confirmedAt, versionId }
    await appendFile(lab.evidence, JSON.stringify(record) + '\n', { encoding: 'utf8', mode: 0o600 })
  }
}

async function sendSingle(page: Page, attempt: ReturnType<typeof observeAttempt>, category: Category, blob: string,
  expectedCaption: string | null = caption) {
  expect(attempt.requests).toHaveLength(0)
  const pending = page.waitForResponse(response => response.request().method() === 'PUT' && isUpload(new URL(response.url())))
  const completion = lab && waitForCompletion(page, attempt.base)
  await button(page, 'Enviar').press('Enter')
  const put = await pending
  expect(put.status()).toBe(200)
  const admission = await admitted(attempt, category, expectedCaption)
  let versionId: string | undefined
  if (lab && completion) {
    expect((await completion).status()).toBe(202)
    await assertPreReceiptStatus(page, 'Estamos verificando o recebimento do vídeo.')
    versionId = await notify(lab, put, admission.manifest, 'Put', lab.opening)
  }
  await confirm(page, admission, blob, category, 'Put', versionId)
  return admission.manifest
}

for (const sample of samples) {
  test(`CA1 e CA4 · envia ${sample.name} em ${sample.category} e recebe recibo`, async ({ page }) => {
    const code = sample.category === 'moment' ? first : second
    const attempt = observeAttempt(page, code)
    const fixture = await readFile(new URL('../media/fixtures/' + sample.name, import.meta.url))
    await startVideo(page, code, sample.category)
    await page.getByLabel('Selecionar vídeo', { exact: true }).setInputFiles({
      name: sample.name, mimeType: sample.mimeType, buffer: fixture,
    })
    await expect.poll(() => preview(page).evaluate((video: HTMLVideoElement) => video.duration)).toBeCloseTo(sample.duration, 1)
    await reviewWithCaption(page, sample.category)
    const blob = await identify(page)
    const manifest = await sendSingle(page, attempt, sample.category, blob)
    expect(manifest).toEqual({ fileKey: manifest.fileKey, position: 1, declaredMediaType: sample.mimeType,
      sizeBytes: fixture.length, sha256: digest(fixture) })
  })
}

test('CA1 e CA4 · envia vídeo gravado no navegador e recebe recibo', async ({ page }) => {
  const attempt = observeAttempt(page, second)
  await startVideo(page, second, 'testimonial')
  await button(page, 'Gravar vídeo').press('Enter')
  await expect(status(page, 'Gravando…')).toBeVisible()
  await expect.poll(() => page.getByLabel('Prévia da câmera').evaluate((video: HTMLVideoElement) => video.currentTime))
    .toBeGreaterThan(1.1)
  await button(page, 'Parar gravação').press('Enter')
  await expect(page.getByLabel('Prévia da câmera')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Revisar depoimento' })).toBeFocused()
  await expect(preview(page)).toBeVisible()
  const blob = await identify(page)
  const manifest = await sendSingle(page, attempt, 'testimonial', blob, null)
  expect(manifest.sizeBytes).toBeGreaterThan(0)
  expect(manifest.declaredMediaType).toMatch(/^video\/(mp4|webm)$/)
})

test('CA2 a CA4 · retoma somente partes faltantes e confirma depois de provar a conclusão', async ({ page }) => {
  const attempt = observeAttempt(page, first)
  const size = (await stat(multipartFile)).size
  const parts = Math.ceil(size / partSize)
  expect(parts).toBeGreaterThanOrEqual(2)
  const sent: number[] = []
  const firstMissing = Promise.withResolvers<void>()
  const retryMissing = Promise.withResolvers<void>()
  const releaseFirst = Promise.withResolvers<void>()
  const releaseRetry = Promise.withResolvers<void>()
  const releaseCompletion = Promise.withResolvers<void>()
  let secondGrants = 0
  await page.route(url => url.pathname.startsWith(attempt.base + '/') && url.pathname.endsWith('/upload-authorizations'), async route => {
    const request = route.request()
    if (request.method() === 'POST' && request.postDataJSON().partNumber === 2) {
      secondGrants++
      if (secondGrants === 1) {
        firstMissing.resolve()
        await releaseFirst.promise
      } else {
        retryMissing.resolve()
        await releaseRetry.promise
      }
    }
    await route.continue()
  })
  await page.route(isUpload, async route => {
    if (route.request().method() !== 'PUT') return route.continue()
    const number = Number(new URL(route.request().url()).searchParams.get('partNumber'))
    sent.push(number)
    if (number === 2 && sent.length === 2) return route.abort('connectionreset')
    await route.continue()
  })
  await page.route(url => completionPath(attempt.base, url), async route => {
    if (route.request().method() === 'POST') await releaseCompletion.promise
    await route.continue()
  })

  await startVideo(page, first, 'moment')
  await page.getByLabel('Selecionar vídeo', { exact: true }).setInputFiles(multipartFile)
  await reviewWithCaption(page, 'moment')
  const blob = await identify(page)
  expect(attempt.requests).toHaveLength(0)
  const progress = page.getByRole('progressbar', { name: 'Transferência do vídeo' })
  try {
    await button(page, 'Enviar').press('Enter')
    await expect(progress).toHaveAttribute('max', String(size), { timeout: 20_000 })
    await firstMissing.promise
    await expect(progress).toHaveAttribute('value', String(partSize))
    await expect(button(page, 'Enviar')).toHaveCount(0)
    await expect(receipt(page)).toHaveCount(0)
  } finally {
    releaseFirst.resolve()
  }
  await expect(status(page, 'O envio foi interrompido. Tente novamente.')).toBeVisible()
  await expect(receipt(page)).toHaveCount(0)
  if (lab) await setTime(lab.clock, new Date(Date.parse(lab.opening) + 11 * 60_000).toISOString())
  const readParts = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/upload-status'))
  const finalPut = page.waitForResponse(response => response.request().method() === 'PUT' && isUpload(new URL(response.url()))
    && Number(new URL(response.url()).searchParams.get('partNumber')) === parts)
  const completion = waitForCompletion(page, attempt.base)
  let put: Response
  try {
    try {
      await button(page, 'Tentar novamente').press('Enter')
      expect(await (await readParts).json()).toMatchObject({ mode: 'multipart', partSizeBytes: partSize, uploadedParts: [1] })
      await retryMissing.promise
      await expect(progress).toHaveAttribute('max', String(size))
      await expect(progress).toHaveAttribute('value', String(partSize))
      await expect(receipt(page)).toHaveCount(0)
    } finally {
      releaseRetry.resolve()
    }
    put = await finalPut
    expect(put.status()).toBe(200)
    await assertPreReceiptStatus(page, 'Validando o vídeo. Aguarde a confirmação do recebimento.')
  } finally {
    releaseCompletion.resolve()
  }
  const completionResponse = await completion
  expect([200, 202]).toContain(completionResponse.status())
  const admission = await admitted(attempt, 'moment')
  expect(admission.manifest).toMatchObject({ sizeBytes: size, declaredMediaType: 'video/mp4' })
  expect(admission.manifest.partSha256).toHaveLength(parts)
  expect(sent).toEqual([1, 2, ...Array.from({ length: parts - 1 }, (_, index) => index + 2)])
  const recovery = attempt.requests.filter(request => new URL(request.url()).pathname.startsWith(admission.path))
  expect(recovery.filter(request => new URL(request.url()).pathname.endsWith('/upload-authorizations'))
    .map(request => request.postDataJSON().partNumber)).toEqual(sent)
  const sameAttempt = await Promise.all(attempt.requests.filter(request => new URL(request.url()).pathname !== attempt.base)
    .map(async request => new URL(request.url()).pathname.startsWith(admission.path)
      && await request.headerValue('authorization') === admission.authorization))
  expect(sameAttempt.length).toBeGreaterThan(parts)
  expect(sameAttempt.every(Boolean)).toBe(true)

  let versionId: string | undefined
  if (lab) {
    expect((await completionResponse.json()).state).not.toBe('confirmed')
    await assertPreReceiptStatus(page, 'Estamos verificando o recebimento do vídeo.')
    await setTime(lab.clock, new Date(Date.parse(lab.deadline) + 60_000).toISOString())
    versionId = await notify(lab, put, admission.manifest, 'CompleteMultipartUpload', lab.deadline)
  }
  await confirm(page, admission, blob, 'moment', 'CompleteMultipartUpload', versionId)
  if (lab) {
    await expect(button(page, 'Registrar um momento')).toHaveCount(0)
    await expect(receipt(page).getByRole('link', { name: 'Deixar outro depoimento' })).toHaveCount(0)
  }
})

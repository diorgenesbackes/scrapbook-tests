import { expect, test, type Page, type Request, type Response } from '@playwright/test'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { open, readFile } from 'node:fs/promises'
import { devNull } from 'node:os'
import { promisify } from 'node:util'

type Lab = {
  endpoint: string
  bucket: string
  queueUrl: string
  region: string
  clock: string
  opening: string
  deadline: string
}

type AudioManifest = {
  fileKey: string
  position: number
  declaredMediaType: string
  sizeBytes: number
  sha256: string
}

const first = process.env.SCRAPBOOK_TEST_PUBLIC_CODE ?? ''
const second = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE ?? ''
const eventCode = /^[A-Za-z0-9_-]{43}$/
if (!eventCode.test(first) || !eventCode.test(second) || first === second) {
  throw new Error('Informe dois códigos distintos de eventos sintéticos para o envio de áudio.')
}

const samples = [
  ['tone.mp3', 'audio/mpeg'],
  ['tone.m4a', 'audio/mp4'],
  ['tone.wav', 'audio/wav'],
  ['tone.ogg', 'audio/ogg'],
  ['tone.webm', 'audio/webm'],
] as const
const guest = 'Convidada sintética do áudio'
const caption = 'Legenda sintética ' + '🎵'.repeat(482)
const iso = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const run = promisify(execFile)
let lab: Lab | undefined
let uploadOrigin: string

test.beforeAll(async ({}, testInfo) => {
  if (testInfo.config.metadata.target === 'url') {
    const origin = process.env.SCRAPBOOK_TEST_AUDIO_UPLOAD_ORIGIN
    if (!origin || new URL(origin).origin !== origin || !origin.startsWith('https://')) {
      throw new Error('Informe a origem HTTPS do armazenamento em SCRAPBOOK_TEST_AUDIO_UPLOAD_ORIGIN.')
    }
    uploadOrigin = origin
    return
  }

  const fixture = process.env.SCRAPBOOK_TEST_AUDIO_FIXTURE
  const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
  const opening = process.env.SCRAPBOOK_TEST_AUDIO_OPEN
  const deadline = process.env.SCRAPBOOK_TEST_AUDIO_DEADLINE
  if (!fixture || !clock || !opening || !deadline || !iso.test(opening) || !iso.test(deadline)
    || !(Date.parse(opening) < Date.parse(deadline))) {
    throw new Error('Informe a fixture e os marcos UTC da stack descartável de áudio.')
  }
  const { endpoint, bucket, queueUrl, environment } = JSON.parse(await readFile(fixture, 'utf8'))
  const region = environment?.PhotoSubmissions__Region
  if (!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(endpoint) || !/^scrapbook-f08-media-[a-f0-9]{16}$/.test(bucket)
    || !queueUrl?.startsWith(endpoint + '/') || !/^\/[0-9]{12}\/photo-receipts$/.test(queueUrl.slice(endpoint.length))
    || !/^[a-z]{2}-[a-z]+-[0-9]$/.test(region)
    || environment.AWS_ACCESS_KEY_ID !== 'test' || environment.AWS_SECRET_ACCESS_KEY !== 'test') {
    throw new Error('O cenário local exige S3 e SQS descartáveis em loopback.')
  }
  uploadOrigin = endpoint
  lab = { endpoint, bucket, queueUrl, region, clock, opening, deadline }
})

test.beforeEach(async () => {
  if (lab) await setTime(lab.clock, lab.opening)
})

function button(page: Page, name: string) {
  return page.getByRole('button', { name, exact: true })
}

function status(page: Page, text: string) {
  return page.getByRole('status').filter({ hasText: text })
}

function preview(page: Page) {
  return page.getByLabel('Prévia do áudio', { exact: true })
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

async function aws(storage: Lab, service: string, operation: string, ...args: string[]) {
  const result = await run('aws', ['--endpoint-url', storage.endpoint, '--region', storage.region,
    '--output', 'json', '--no-cli-pager', service, operation, ...args], {
    timeout: 10_000,
    env: { PATH: process.env.PATH, AWS_CONFIG_FILE: devNull, AWS_SHARED_CREDENTIALS_FILE: devNull,
      AWS_EC2_METADATA_DISABLED: 'true', AWS_ACCESS_KEY_ID: 'test', AWS_SECRET_ACCESS_KEY: 'test' },
  }).catch(() => { throw new Error(`A operação ${service} ${operation} falhou na fixture local de áudio.`) })
  return JSON.parse(result.stdout)
}

async function notify(storage: Lab, put: Response, manifest: AudioManifest, at: string) {
  const key = decodeURIComponent(new URL(put.url()).pathname.slice(storage.bucket.length + 2))
  expect(key).toMatch(/^submissions\/temporary\/[0-9a-f]{32}$/)
  const listed = await aws(storage, 's3api', 'list-object-versions', '--bucket', storage.bucket, '--prefix', key)
  expect(listed.Versions).toHaveLength(1)
  const [version] = listed.Versions
  expect(version).toMatchObject({ Key: key, Size: manifest.sizeBytes, IsLatest: true })
  const versionId: string = version.VersionId
  expect(versionId).toMatch(uuid)
  expect(await put.headerValue('x-amz-version-id')).toBe(versionId)
  const envelope = { Records: [{
    eventVersion: '2.1', eventSource: 'aws:s3', awsRegion: storage.region, eventTime: at,
    eventName: 'ObjectCreated:Put',
    s3: {
      s3SchemaVersion: '1.0', configurationId: 'audio-fixture',
      bucket: { name: storage.bucket, arn: 'arn:aws:s3:::' + storage.bucket },
      object: { key: encodeURIComponent(key), versionId, size: manifest.sizeBytes, sequencer: '0000000000000001' },
    },
  }] }
  await aws(storage, 'sqs', 'send-message', '--queue-url', storage.queueUrl, '--message-body', JSON.stringify(envelope))
}

function isUpload(url: URL) {
  return url.origin === uploadOrigin && /\/submissions\/temporary\/[0-9a-f]{32}$/.test(url.pathname)
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

async function selectAudio(page: Page, code: string) {
  await page.goto('/e/' + code + '/depoimento', { waitUntil: 'domcontentloaded' })
  await button(page, 'Selecionar áudio').press('Enter')
  return page.getByLabel('Selecionar áudio', { exact: true })
}

async function reviewSelected(page: Page, expectedCaption?: string) {
  await expect(preview(page)).toBeVisible()
  await expect.poll(() => preview(page).evaluate((audio: HTMLAudioElement) => audio.duration)).toBeGreaterThan(0)
  if (expectedCaption) {
    await button(page, 'Deseja adicionar uma legenda?').press('Enter')
    await page.getByRole('textbox', { name: 'Legenda do áudio' }).fill(expectedCaption)
    await expect(page.getByText('0 caracteres restantes na legenda.')).toBeVisible()
  }
  await button(page, 'Revisar').press('Enter')
  if (expectedCaption) {
    await expect(page.getByRole('region', { name: 'Prévia da mídia' }).getByText(expectedCaption, { exact: true })).toBeVisible()
  }
}

async function identify(page: Page) {
  await expect(page.getByRole('heading', { name: 'Revisar depoimento' })).toBeFocused()
  await expect(button(page, 'Enviar')).toBeDisabled()
  await button(page, 'Confirmar identificação').press('Enter')
  await page.getByRole('textbox', { name: 'Nome ou apelido' }).fill(guest)
  await page.getByRole('checkbox', { name: 'Li o aviso de acesso.' }).check()
  await button(page, 'Continuar').press('Enter')
  await expect(button(page, 'Enviar')).toBeEnabled()
  const blob = await preview(page).getAttribute('src')
  expect(blob).toMatch(/^blob:/)
  return blob!
}

async function admitted(attempt: ReturnType<typeof observeAttempt>, expectedCaption?: string) {
  const admissions = attempt.requests.filter(request => request.method() === 'POST' && new URL(request.url()).pathname === attempt.base)
  expect(admissions).toHaveLength(1)
  const [post] = admissions
  const draft = post.postDataJSON()
  expect(draft).toMatchObject({ category: 'testimonial', format: 'audio', identification: { guestName: guest } })
  expect(draft.caption).toBe(expectedCaption)
  expect(draft.files).toHaveLength(1)
  const manifest: AudioManifest = draft.files[0]
  expect(manifest.fileKey).toMatch(uuid)
  expect(manifest.position).toBe(1)
  expect(Object.keys(manifest).sort()).toEqual(['declaredMediaType', 'fileKey', 'position', 'sha256', 'sizeBytes'])
  const key = await post.headerValue('idempotency-key')
  const authorization = await post.headerValue('authorization')
  expect(Boolean(key && uuid.test(key) && authorization?.startsWith('Bearer '))).toBe(true)
  const path = attempt.base + '/' + key
  return { path, url: new URL(path, post.url()).href, authorization: authorization!, manifest }
}

function waitForPut(page: Page) {
  return page.waitForResponse(response => response.request().method() === 'PUT' && isUpload(new URL(response.url())))
}

function waitForCompletion(page: Page, base: string) {
  return page.waitForResponse(response => {
    const path = new URL(response.url()).pathname
    return response.request().method() === 'POST' && path.startsWith(base + '/') && path.endsWith('/completion')
  })
}

async function expectPending(page: Page, completion: Response) {
  expect(completion.status()).toBe(202)
  expect((await completion.json()).state).toBe('receiving')
  await expect(status(page, 'Estamos verificando o recebimento do áudio.')).toBeVisible()
  await expect(page.getByRole('progressbar')).toHaveCount(0)
  await expect(receipt(page)).toHaveCount(0)
}

async function confirm(page: Page, admission: Awaited<ReturnType<typeof admitted>>, blob: string, isOpen: boolean) {
  await expect(receipt(page).getByRole('heading')).toBeFocused({ timeout: 30_000 })
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
  await expect(timestamp).toHaveText(brasilia.format(when))
  await expect(receipt(page).getByText('Horário de Brasília', { exact: false })).toBeVisible()
  await expect(preview(page)).toHaveCount(0)
  await expect(receipt(page).getByText(caption, { exact: true })).toHaveCount(0)
  await expect(receipt(page).getByText(guest, { exact: true })).toHaveCount(0)
  await expect(receipt(page).getByRole('link')).toHaveText(isOpen ? ['Registrar um momento', 'Voltar ao evento'] : ['Voltar ao evento'])
  await expect(receipt(page).getByRole('button')).toHaveText(isOpen ? ['Deixar outro depoimento'] : [])
  expect(await page.evaluate(url => fetch(url).then(() => false, () => true), blob)).toBe(true)
  return when
}

async function send(page: Page, attempt: ReturnType<typeof observeAttempt>, blob: string, expectedCaption?: string) {
  expect(attempt.requests).toHaveLength(0)
  const put = waitForPut(page)
  const completion = lab && waitForCompletion(page, attempt.base)
  await button(page, 'Enviar').press('Enter')
  const uploaded = await put
  expect(uploaded.status()).toBe(200)
  const admission = await admitted(attempt, expectedCaption)
  if (lab && completion) {
    await expectPending(page, await completion)
    await notify(lab, uploaded, admission.manifest, lab.opening)
  }
  await confirm(page, admission, blob, true)
  return admission.manifest
}

for (const [name, mimeType] of samples) {
  test(`CA1 e CA3 · envia ${name} selecionado e recebe somente o recibo`, async ({ page }) => {
    const attempt = observeAttempt(page, first)
    const buffer = await readFile(new URL('../media/fixtures/' + name, import.meta.url))
    const input = await selectAudio(page, first)
    await input.setInputFiles({ name, mimeType, buffer })
    await reviewSelected(page, caption)
    const blob = await identify(page)
    const manifest = await send(page, attempt, blob, caption)
    expect(manifest).toEqual({
      fileKey: manifest.fileKey, position: 1, declaredMediaType: mimeType, sizeBytes: buffer.length,
      sha256: createHash('sha256').update(buffer).digest('base64'),
    })
  })
}

test('CA1 e CA3 · grava pelo microfone, revisa e envia sem legenda', async ({ page }) => {
  const attempt = observeAttempt(page, second)
  await selectAudio(page, second)
  await button(page, 'Gravar áudio').press('Enter')
  await expect(status(page, 'Gravando…')).toBeVisible()
  await expect(page.getByRole('timer', { name: 'Tempo restante' })).toHaveText('4:58')
  await button(page, 'Parar gravação').press('Enter')
  await expect(preview(page)).toBeVisible()
  await expect.poll(() => preview(page).evaluate((audio: HTMLAudioElement) => audio.duration)).toBeGreaterThan(0)
  const blob = await identify(page)
  const manifest = await send(page, attempt, blob)
  expect(manifest.sizeBytes).toBeGreaterThan(0)
  expect(manifest.declaredMediaType).toMatch(/^audio\/(mp4|ogg|webm)$/)
})

test('CA2 e CA3 · retoma o PUT na mesma tentativa e aguarda a confirmação persistida', async ({ page }) => {
  test.setTimeout(60_000)
  const attempt = observeAttempt(page, second)
  const [name, mimeType] = samples[2]
  const buffer = await readFile(new URL('../media/fixtures/' + name, import.meta.url))
  const putPaths: string[] = []
  const releaseFirstPut = Promise.withResolvers<void>()
  const releaseCompletion = Promise.withResolvers<void>()
  await page.route(isUpload, async route => {
    const request = route.request()
    if (request.method() !== 'PUT') return route.continue()
    putPaths.push(new URL(request.url()).pathname)
    if (putPaths.length === 1) {
      await releaseFirstPut.promise
      return route.abort('connectionreset')
    }
    return route.continue()
  })
  if (lab) {
    await page.route(url => url.pathname.startsWith(attempt.base + '/') && url.pathname.endsWith('/completion'), async route => {
      if (route.request().method() === 'POST') await releaseCompletion.promise
      await route.continue()
    })
  }

  const input = await selectAudio(page, second)
  await input.setInputFiles({ name, mimeType, buffer })
  await reviewSelected(page)
  const blob = await identify(page)
  expect(attempt.requests).toHaveLength(0)
  try {
    await button(page, 'Enviar').press('Enter')
    const progress = page.getByRole('progressbar', { name: 'Transferência do áudio' })
    await expect(progress).toHaveAttribute('max', String(buffer.length))
    await expect(button(page, 'Enviar')).toHaveCount(0)
    await expect(receipt(page)).toHaveCount(0)
  } finally {
    releaseFirstPut.resolve()
  }
  await expect(status(page, 'O envio foi interrompido. Tente novamente.')).toBeVisible()
  await expect(receipt(page)).toHaveCount(0)

  const admission = await admitted(attempt)
  const filePath = admission.path + '/files/' + admission.manifest.fileKey
  const uploadStatus = page.waitForResponse(response => response.request().method() === 'GET'
    && new URL(response.url()).pathname === filePath + '/upload-status')
  const put = waitForPut(page)
  const completion = lab && waitForCompletion(page, attempt.base)
  let uploaded: Response
  try {
    await button(page, 'Tentar novamente').press('Enter')
    expect(await (await uploadStatus).json()).toMatchObject({ mode: 'put', uploadedParts: [] })
    uploaded = await put
    expect(uploaded.status()).toBe(200)
    if (lab) {
      await expect(status(page, 'Validando o áudio. Aguarde a confirmação do recebimento.')).toBeVisible()
      await expect(receipt(page)).toHaveCount(0)
    }
  } finally {
    releaseCompletion.resolve()
  }
  expect(putPaths).toHaveLength(2)
  expect(putPaths[1]).toBe(putPaths[0])
  const replay = await admitted(attempt)
  expect(replay.path === admission.path && replay.authorization === admission.authorization).toBe(true)
  const statusReads = attempt.requests.filter(request => new URL(request.url()).pathname === admission.path && request.method() === 'GET')
  expect(statusReads.length).toBeGreaterThanOrEqual(2)
  const authorizations = attempt.requests.filter(request => new URL(request.url()).pathname === filePath + '/upload-authorizations')
  expect(authorizations).toHaveLength(2)
  expect(authorizations.map(request => request.postDataJSON())).toEqual([{}, {}])
  for (const request of [...statusReads, ...authorizations]) {
    expect(await request.headerValue('authorization') === admission.authorization).toBe(true)
  }

  if (lab && completion) {
    const storage = lab
    await test.step('Confirma após as 13h com prova de recebimento no instante do limite', async () => {
      await expectPending(page, await completion)
      const deadline = Date.parse(storage.deadline)
      await setTime(storage.clock, new Date(deadline + 60_000).toISOString())
      const publicEventPath = attempt.base.slice(0, -'/submissions'.length)
      const refresh = page.waitForResponse(response => response.request().method() === 'GET'
        && new URL(response.url()).pathname === publicEventPath)
      await notify(storage, uploaded, admission.manifest, storage.deadline)
      expect(await confirm(page, admission, blob, false)).toBeGreaterThan(deadline)
      expect(await (await refresh).json()).toMatchObject({ submissionWindow: { state: 'closed' } })
    })
  } else {
    await confirm(page, admission, blob, true)
  }
})

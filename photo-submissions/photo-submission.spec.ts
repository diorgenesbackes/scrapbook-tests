import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Request, type Response } from '@playwright/test'
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { open, readFile } from 'node:fs/promises'
import { devNull } from 'node:os'
import { promisify } from 'node:util'

const first = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const second = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
const fixturePath = process.env.SCRAPBOOK_TEST_PHOTO_FIXTURE
const photoPath = process.env.SCRAPBOOK_TEST_PHOTO_PERFORMANCE_FILE
const publicCode = /^[A-Za-z0-9_-]{43}$/
if (!first || !second || first === second || !publicCode.test(first) || !publicCode.test(second)
  || !clock || !fixturePath || !photoPath) {
  throw new Error('Informe o relógio, a fixture de mídia e os dois eventos da stack descartável.')
}

const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
const { endpoint, bucket, queueUrl, environment } = fixture
const region = environment?.PhotoSubmissions__Region
if (!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(endpoint)
  || !/^scrapbook-f08-media-[a-f0-9]{16}$/.test(bucket)
  || new URL(queueUrl).origin !== endpoint || !/^\/[0-9]{12}\/photo-receipts$/.test(new URL(queueUrl).pathname)
  || new URL(queueUrl).search || new URL(queueUrl).hash || new URL(queueUrl).username || new URL(queueUrl).password
  || !/^[a-z]{2}-[a-z]+-[0-9]$/.test(region)
  || environment.AWS_ACCESS_KEY_ID !== 'test' || environment.AWS_SECRET_ACCESS_KEY !== 'test') {
  throw new Error('O teste exige S3 e SQS descartáveis em loopback.')
}

const sample = await readFile(photoPath)
const opening = '2032-02-29T02:00:00.000Z'
const deadline = '2032-03-01T16:00:00.000Z'
const afterDeadline = '2032-03-01T16:01:00.000Z'
const caption = 'Legenda sintética com acento e emoji 📷'
const guestName = 'Convidada sintética da foto'
const filename = 'depoimento-sintetico.png'
const run = promisify(execFile)

async function setTime(value: string) {
  const handle = await open(clock!, 'r+')
  try {
    await handle.write(value, 0, 'ascii')
    await handle.sync()
  } finally {
    await handle.close()
  }
}

function buttons(page: Page, name: string) {
  return page.getByRole('button', { name, exact: true })
}

function status(page: Page, message: string) {
  return page.getByRole('status').filter({ hasText: message })
}

function isPhotoPut(request: Request) {
  const url = new URL(request.url())
  return request.method() === 'PUT' && url.origin === endpoint
    && url.pathname.startsWith('/' + bucket + '/submissions/temporary/')
}

async function notifyPhoto(response: Response, size: number, eventTime: string) {
  expect(response.status()).toBe(200)
  const versionId = await response.headerValue('x-amz-version-id')
  expect(Boolean(versionId && versionId !== 'null')).toBe(true)
  const key = decodeURIComponent(new URL(response.url()).pathname.slice(bucket.length + 2))
  const envelope = { Records: [{
    eventVersion: '2.1', eventSource: 'aws:s3', awsRegion: region, eventTime, eventName: 'ObjectCreated:Put',
    s3: { s3SchemaVersion: '1.0', configurationId: 'photo-fixture', bucket: {
      name: bucket, arn: 'arn:aws:s3:::' + bucket,
    }, object: { key: encodeURIComponent(key).replace(/%20/g, '+'), versionId, size, sequencer: '0000000000000001' } },
  }] }
  await run('aws', ['--endpoint-url', endpoint, '--region', region, '--no-cli-pager', 'sqs', 'send-message',
    '--queue-url', queueUrl, '--message-body', JSON.stringify(envelope)], {
    timeout: 15_000,
    env: { PATH: process.env.PATH, AWS_CONFIG_FILE: devNull, AWS_SHARED_CREDENTIALS_FILE: devNull,
      AWS_EC2_METADATA_DISABLED: 'true', AWS_ACCESS_KEY_ID: 'test', AWS_SECRET_ACCESS_KEY: 'test' },
  })
}

async function startPhoto(page: Page, code: string) {
  await page.goto('/e/' + code + '/depoimento', { waitUntil: 'domcontentloaded' })
  await buttons(page, 'Selecionar fotos').press('Enter')
}

function preview(page: Page) {
  return page.getByRole('img', { name: 'Foto 1', exact: true })
}

async function identify(page: Page) {
  await expect(preview(page)).toBeVisible()
  await buttons(page, 'Deseja adicionar uma legenda?').press('Enter')
  await page.getByRole('textbox', { name: 'Legenda das fotos' }).fill(caption)
  await buttons(page, 'Revisar').press('Enter')
  await expect(page.getByRole('heading', { name: 'Revisar depoimento' })).toBeFocused()
  await expect(page.getByText(caption, { exact: true })).toBeVisible()
  await expect(buttons(page, 'Enviar')).toBeDisabled()
  await buttons(page, 'Confirmar identificação').press('Enter')
  await expect(page.getByText('Aviso sintético de acesso a contribuições.')).toBeVisible()
  await page.getByRole('textbox', { name: 'Nome ou apelido' }).fill(guestName)
  await page.getByRole('checkbox', { name: 'Li o aviso de acesso.' }).check()
  await buttons(page, 'Continuar').press('Enter')
  await expect(buttons(page, 'Enviar')).toBeEnabled()
}

function observeAttempt(page: Page, code: string) {
  const path = '/api/v1/public-events/' + code + '/submissions'
  const requests: Request[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === path || url.pathname.startsWith(path + '/')) requests.push(request)
  })
  return { path, requests }
}

async function checkValidation(page: Page) {
  await expect(status(page, 'Validando a foto. Aguarde a confirmação do recebimento.')).toBeVisible()
  await expect(page.getByRole('progressbar')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Recebimento confirmado' })).toHaveCount(0)
  await expect(buttons(page, 'Enviar')).toHaveCount(0)
}

async function checkReceipt(page: Page, oldPhoto: string, confirmedAt: string) {
  const receipt = page.getByRole('region', { name: 'Recebimento confirmado' })
  await expect(receipt.getByRole('heading')).toBeFocused({ timeout: 20_000 })
  await expect(receipt.getByText('Sua contribuição foi guardada.')).toBeVisible()
  const presentedTime = await receipt.locator('time').getAttribute('datetime')
  expect(Date.parse(presentedTime ?? '')).toBe(Date.parse(confirmedAt))
  const brasilia = new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short', timeStyle: 'short', timeZone: 'America/Sao_Paulo',
  })
  await expect(receipt.locator('time')).toHaveText(brasilia.format(new Date(confirmedAt)))
  await expect(receipt.getByText('Horário de Brasília', { exact: false })).toBeVisible()
  await expect(receipt.getByRole('img')).toHaveCount(0)
  await expect(receipt.getByText(caption)).toHaveCount(0)
  await expect(receipt.getByText(guestName)).toHaveCount(0)
  await expect(receipt.getByRole('link', { name: /ver|abrir|baixar|excluir/i })).toHaveCount(0)
  expect(await page.evaluate(url => fetch(url).then(() => false, () => true), oldPhoto)).toBe(true)
}

test.beforeEach(async () => setTime(opening))

test('CA1–CA2: seleciona e revisa foto, transfere bytes por teclado e reinicia após o recibo', async ({ page, context }) => {
  const attempt = observeAttempt(page, first!)
  const network = await context.newCDPSession(page)
  await network.send('Network.enable')
  await network.send('Network.emulateNetworkConditions', {
    offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: 300_000,
  })
  await startPhoto(page, first!)
  await page.getByLabel('Selecionar fotos', { exact: true }).setInputFiles({ name: filename, mimeType: 'image/png', buffer: sample })
  await identify(page)
  expect(attempt.requests).toHaveLength(0)
  const oldPhoto = (await preview(page).getAttribute('src'))!
  const put = page.waitForResponse(response => isPhotoPut(response.request()))
  await buttons(page, 'Enviar').press('Enter')
  await expect(status(page, 'Transferindo a foto.')).toBeVisible()
  const progress = page.getByRole('progressbar', { name: 'Transferência da foto' })
  await expect(progress).toHaveAttribute('max', String(sample.length))
  await expect.poll(() => progress.evaluate((element: HTMLProgressElement) => element.value > 0 && element.value < element.max))
    .toBe(true)
  await checkValidation(page)
  const response = await put
  const admissions = attempt.requests.filter(request => request.method() === 'POST' && new URL(request.url()).pathname === attempt.path)
  expect(admissions).toHaveLength(1)
  const draft = admissions[0].postDataJSON()
  expect(draft).toMatchObject({ category: 'testimonial', format: 'photo', caption, files: [{
    position: 1, declaredMediaType: 'image/png', sizeBytes: sample.length,
    sha256: createHash('sha256').update(sample).digest('base64'),
  }] })
  expect(draft.files).toHaveLength(1)
  expect(JSON.stringify(draft).includes(filename)).toBe(false)

  await notifyPhoto(response, sample.length, opening)
  await checkReceipt(page, oldPhoto, opening)
  await expect(page.getByRole('link', { name: 'Registrar um momento' })).toHaveAttribute('href', '/e/' + first + '/momento')
  expect((await new AxeBuilder({ page }).include('.testimonial').analyze()).violations).toEqual([])
  await buttons(page, 'Deixar outro depoimento').press('Enter')
  await buttons(page, 'Selecionar fotos').press('Enter')
  await expect(preview(page)).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: 'Legenda das fotos' })).toHaveCount(0)
  await expect(buttons(page, 'Revisar')).toBeDisabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('CA2–CA3: retoma foto capturada pela mesma tentativa e recebe confirmação após fechar a janela', async ({ page }) => {
  const attempt = observeAttempt(page, second!)
  let uploads = 0
  await page.route(url => url.origin === endpoint && url.pathname.startsWith('/' + bucket + '/submissions/temporary/'), async route => {
    if (!isPhotoPut(route.request())) return route.continue()
    uploads += 1
    if (uploads === 1) await route.abort('connectionreset')
    else await route.continue()
  })

  await startPhoto(page, second!)
  await buttons(page, 'Fotografar').press('Enter')
  await expect(buttons(page, 'Tirar foto')).toBeFocused()
  await buttons(page, 'Tirar foto').press('Enter')
  await expect(page.getByLabel('Prévia da câmera')).toHaveCount(0)
  await identify(page)
  const oldPhoto = (await preview(page).getAttribute('src'))!
  expect(attempt.requests).toHaveLength(0)
  await buttons(page, 'Enviar').press('Enter')
  await expect(status(page, 'O envio foi interrompido. Tente novamente.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Recebimento confirmado' })).toHaveCount(0)
  const put = page.waitForResponse(response => isPhotoPut(response.request()))
  await buttons(page, 'Tentar novamente').press('Enter')
  await checkValidation(page)
  const response = await put
  expect(uploads).toBe(2)
  const admissions = attempt.requests.filter(request => request.method() === 'POST' && new URL(request.url()).pathname === attempt.path)
  expect(admissions).toHaveLength(1)
  const { files } = admissions[0].postDataJSON()
  expect(files).toMatchObject([{ position: 1, declaredMediaType: 'image/jpeg' }])
  const key = await admissions[0].headerValue('Idempotency-Key')
  const credential = await admissions[0].headerValue('Authorization')
  expect(Boolean(key && credential)).toBe(true)
  const remaining = attempt.requests.filter(request => request !== admissions[0])
  expect(remaining.filter(request => new URL(request.url()).pathname.endsWith('/upload-authorizations'))).toHaveLength(2)
  expect(remaining.some(request => request.method() === 'GET')).toBe(true)
  const sameAttempt = await Promise.all(remaining.map(async request =>
    new URL(request.url()).pathname.split('/').includes(key!)
    && await request.headerValue('Authorization') === credential))
  expect(sameAttempt.every(Boolean)).toBe(true)

  await setTime(afterDeadline)
  await notifyPhoto(response, files[0].sizeBytes, deadline)
  await checkReceipt(page, oldPhoto, afterDeadline)
  await expect(buttons(page, 'Deixar outro depoimento')).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Registrar um momento' })).toHaveCount(0)
})

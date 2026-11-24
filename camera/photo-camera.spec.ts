import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { open, readFile } from 'node:fs/promises'

const code = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const second = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
const publicCode = /^[A-Za-z0-9_-]{43}$/
if (!code || !second || code === second || !publicCode.test(code) || !publicCode.test(second) || !clock) {
  throw new Error('Informe o relógio e os dois eventos da stack local descartável.')
}

const event = '/e/' + code
const testimonial = event + '/depoimento'
const moment = event + '/momento'
const recovery = 'Permita o acesso no navegador ou selecione um arquivo do dispositivo.'
const privateCaption = 'Legenda de câmera sintética'

type CameraProbe = {
  requests: MediaStreamConstraints[]
  tracks: MediaStreamTrack[]
  revoked: string[]
  failures: string[]
  holdStream: boolean
  releaseStream: (() => void) | null
}

async function observeCamera(page: Page) {
  await page.addInitScript(() => {
    const probe: CameraProbe = { requests: [], tracks: [], revoked: [], failures: [], holdStream: false, releaseStream: null }
    Reflect.set(window, 'testCamera', probe)
    const request = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
    const revoke = URL.revokeObjectURL.bind(URL)
    navigator.mediaDevices.getUserMedia = async constraints => {
      probe.requests.push(constraints ?? {})
      try {
        const stream = await request(constraints)
        probe.tracks.push(...stream.getTracks())
        if (probe.holdStream) await new Promise<void>(resolve => { probe.releaseStream = resolve })
        return stream
      } catch (error) {
        if (error instanceof DOMException) probe.failures.push(error.name)
        throw error
      }
    }
    URL.revokeObjectURL = url => {
      probe.revoked.push(url)
      revoke(url)
    }
  })
}

function cameraState(page: Page) {
  return page.evaluate(() => {
    const probe = Reflect.get(window, 'testCamera') as CameraProbe
    return { requests: probe.requests, tracks: probe.tracks.map(track => track.readyState), revoked: probe.revoked, failures: probe.failures }
  })
}

async function selectPhotoFormat(page: Page, path: string) {
  const response = await page.goto(path)
  await page.getByRole('button', { name: 'Selecionar fotos', exact: true }).click()
  return response
}

function shoot(page: Page) { return page.getByRole('button', { name: 'Tirar foto', exact: true }) }
function cameraButton(page: Page, label = 'Fotografar') { return page.getByRole('button', { name: label, exact: true }) }
function photo(page: Page, index: number) { return page.getByRole('img', { name: 'Foto ' + index, exact: true }) }
function review(page: Page) { return page.getByRole('button', { name: 'Revisar', exact: true }) }

async function file(name: string) {
  return { name, mimeType: 'image/png', buffer: await readFile(new URL('../photos/fixtures/palette.png', import.meta.url)) }
}

async function capture(page: Page, label?: string) {
  await cameraButton(page, label).click()
  await expect(shoot(page)).toBeEnabled()
  await shoot(page).click()
  await expect(page.getByLabel('Prévia da câmera', { exact: true })).toHaveCount(0)
  await expect.poll(async () => (await cameraState(page)).tracks.every(state => state === 'ended')).toBe(true)
}

test.beforeEach(async () => {
  const handle = await open(clock!, 'r+')
  try {
    await handle.write('2032-02-29T04:00:00.000Z', 0, 'ascii')
    await handle.sync()
  } finally { await handle.close() }
})

if (process.env.SCRAPBOOK_TEST_CAMERA_4G === 'true') {
  test('mede prontidão, abertura, foto e encerramento de trilhas em 4G', async ({ browser, baseURL, browserName }) => {
    test.setTimeout(90_000)
    expect(browserName).toBe('chromium')
    const editorTimes: number[] = []
    const cameraTimes: number[] = []
    const previewTimes: number[] = []
    const stoppedTimes: number[] = []
    for (let sample = 0; sample < 5; sample++) {
      const context = await browser.newContext({ baseURL, viewport: { width: 393, height: 851 }, isMobile: true, hasTouch: true, permissions: ['camera'] })
      try {
        const page = await context.newPage()
        await observeCamera(page)
        const network = await context.newCDPSession(page)
        await network.send('Network.enable')
        await network.send('Network.setCacheDisabled', { cacheDisabled: true })
        await network.send('Network.emulateNetworkConditions', {
          offline: false, latency: 150, downloadThroughput: 200_000, uploadThroughput: 93_750, connectionType: 'cellular4g',
        })
        await selectPhotoFormat(page, testimonial)
        await expect(cameraButton(page)).toBeEnabled()
        editorTimes.push(await page.evaluate(() => Math.round(performance.now())))
        for (let attempt = 0; attempt < 2; attempt++) {
          const started = await page.evaluate(() => performance.now())
          await cameraButton(page, attempt === 0 ? 'Fotografar' : 'Refazer foto 1').click()
          await expect(shoot(page)).toBeEnabled()
          cameraTimes.push(await page.evaluate(start => Math.round(performance.now() - start), started))
          const measured = await page.evaluate(() => {
            const probe = Reflect.get(window, 'testCamera') as CameraProbe
            const track = probe.tracks.at(-1)!
            const stop = track.stop.bind(track)
            return new Promise<{ stop: number; preview: number }>(resolve => {
              const shutter = Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Tirar foto')!
              const previous = document.querySelector<HTMLImageElement>('.photo-thumbnails img')?.src
              let start = 0
              let stopped = 0
              track.stop = () => { stop(); stopped = performance.now() }
              shutter.addEventListener('click', () => { start = performance.now() }, { capture: true, once: true })
              const observer = new MutationObserver(() => {
                const image = document.querySelector<HTMLImageElement>('.photo-thumbnails img')
                if (!image || image.src === previous) return
                observer.disconnect()
                void image.decode().then(() => resolve({ stop: Math.round(stopped - start), preview: Math.round(performance.now() - start) }))
              })
              observer.observe(document.body, { childList: true, attributes: true, subtree: true })
              shutter.click()
            })
          })
          stoppedTimes.push(measured.stop)
          previewTimes.push(measured.preview)
          expect(measured.stop).toBeGreaterThanOrEqual(0)
          expect(measured.stop).toBeLessThanOrEqual(measured.preview)
          expect((await cameraState(page)).tracks).toEqual(Array(attempt + 1).fill('ended'))
        }
      } finally { await context.close() }
    }
    console.log(`Câmera 4G | seletor ms ${editorTimes.join(', ')} | abertura ms ${cameraTimes.join(', ')} | trilha liberada ms ${stoppedTimes.join(', ')} | prévia ms ${previewTimes.join(', ')}`)
    expect(Math.max(...editorTimes)).toBeLessThanOrEqual(3000)
  })
} else {
  test('CA1 e CA3 aceitam uma foto, refazem com nova imagem e revisam a legenda', async ({ page, context }) => {
    await context.grantPermissions(['camera'])
    await observeCamera(page)
    const response = await selectPhotoFormat(page, testimonial)
    expect(response?.headers()['permissions-policy']).toBe('camera=(self), microphone=(), geolocation=()')
    expect((await cameraState(page)).requests).toHaveLength(0)
    await capture(page)
    await expect(photo(page, 1)).toBeVisible()
    await expect(cameraButton(page)).toBeDisabled()
    const original = await photo(page, 1).getAttribute('src')
    await capture(page, 'Refazer foto 1')
    await expect(photo(page, 1)).not.toHaveAttribute('src', original!)
    expect((await cameraState(page)).revoked).toContain(original)
    expect((await cameraState(page)).requests).toEqual([
      expect.objectContaining({ audio: false, video: expect.any(Object) }),
      expect.objectContaining({ audio: false, video: expect.any(Object) }),
    ])
    await page.getByRole('button', { name: 'Deseja adicionar uma legenda?' }).click()
    await page.getByLabel('Legenda das fotos').fill(privateCaption)
    await review(page).click()
    await expect(page.getByRole('heading', { name: 'Revisar depoimento' })).toBeVisible()
    await expect(page.getByText(privateCaption, { exact: true })).toBeVisible()
    await expect(photo(page, 1)).toBeVisible()
    await page.getByRole('button', { name: 'Editar fotos e legenda' }).click()
    await page.getByRole('button', { name: 'Remover foto 1' }).click()
    await expect(photo(page, 1)).toHaveCount(0)
    await expect(cameraButton(page)).toBeEnabled()
  })

  test('CA1 combina arquivo e câmera respeitando a borda de dez fotos em momento', async ({ page, context }) => {
    await context.grantPermissions(['camera'])
    await observeCamera(page)
    await selectPhotoFormat(page, moment)
    await page.getByLabel('Selecionar fotos', { exact: true }).setInputFiles(await Promise.all(
      Array.from({ length: 9 }, (_, index) => file('foto-' + index + '.png')),
    ))
    await expect(photo(page, 9)).toBeVisible()
    await capture(page)
    await expect(photo(page, 10)).toBeVisible()
    await expect(cameraButton(page)).toBeDisabled()
    await expect(page.getByLabel('Selecionar fotos', { exact: true })).toBeDisabled()
    await review(page).click()
    await expect(page.getByRole('heading', { name: 'Revisar momento' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Prévia das fotos' }).getByRole('img')).toHaveCount(10)
  })

  test('CA2 recupera acesso recusado por meio do seletor e preserva o conjunto', async ({ page, context }) => {
    await context.grantPermissions(['camera'])
    await observeCamera(page)
    await page.route(url => url.pathname === moment, async route => {
      const response = await route.fetch()
      await route.fulfill({ response, headers: { ...response.headers(), 'permissions-policy': 'camera=()' } })
    })
    await selectPhotoFormat(page, moment)
    const field = page.getByLabel('Selecionar fotos', { exact: true })
    await field.setInputFiles(await file('primeira-foto.png'))
    await expect(photo(page, 1)).toBeVisible()
    await cameraButton(page).click()
    await expect(page.getByRole('alert')).toHaveText(recovery)
    expect((await cameraState(page)).failures).toEqual(['NotAllowedError'])
    await field.setInputFiles(await file('segunda-foto.png'))
    await expect(photo(page, 2)).toBeVisible()
    await expect(review(page)).toBeEnabled()
    expect((await cameraState(page)).tracks).toHaveLength(0)
  })

  test('CA3 recupera falha de captura, preserva a foto e aceita nova tentativa', async ({ page, context }) => {
    await context.grantPermissions(['camera'])
    await observeCamera(page)
    await selectPhotoFormat(page, testimonial)
    await capture(page)
    const original = await photo(page, 1).getAttribute('src')
    await page.evaluate(() => {
      const encode = HTMLCanvasElement.prototype.toBlob
      HTMLCanvasElement.prototype.toBlob = callback => {
        HTMLCanvasElement.prototype.toBlob = encode
        callback(null)
      }
    })
    await capture(page, 'Refazer foto 1')
    await expect(page.getByRole('alert')).toContainText('Não foi possível preparar a prévia.')
    await expect(photo(page, 1)).toHaveAttribute('src', original!)
    await capture(page, 'Refazer foto 1')
    await expect(photo(page, 1)).not.toHaveAttribute('src', original!)
  })

  test('CA4 encerra pedidos tardios, limpa o descarte e não envia dados', async ({ page, context }) => {
    await context.grantPermissions(['camera'])
    await observeCamera(page)
    const writes: string[] = []
    page.on('request', request => {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(request.method())
    })
    await selectPhotoFormat(page, testimonial)
    await page.evaluate(() => { (Reflect.get(window, 'testCamera') as CameraProbe).holdStream = true })
    await cameraButton(page).click()
    await expect(page.getByRole('status')).toContainText('Aguardando permissão')
    await expect.poll(async () => (await cameraState(page)).tracks).toEqual(['live'])
    await page.getByRole('button', { name: 'Voltar aos formatos' }).click()
    await expect(page.getByRole('button', { name: 'Selecionar vídeo' })).toBeVisible()
    await page.evaluate(() => {
      const probe = Reflect.get(window, 'testCamera') as CameraProbe
      probe.holdStream = false
      probe.releaseStream?.()
    })
    await expect.poll(async () => (await cameraState(page)).tracks).toEqual(['ended'])
    await page.getByRole('button', { name: 'Selecionar fotos', exact: true }).click()
    await capture(page)
    const original = await photo(page, 1).getAttribute('src')
    await page.getByRole('button', { name: 'Deseja adicionar uma legenda?' }).click()
    await page.getByLabel('Legenda das fotos').fill(privateCaption)
    await cameraButton(page, 'Refazer foto 1').click()
    await expect(shoot(page)).toBeEnabled()
    expect((await new AxeBuilder({ page }).include('.photo-editor').analyze()).violations).toEqual([])
    await page.getByRole('button', { name: 'Descartar', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect.poll(async () => (await cameraState(page)).tracks).toEqual(['ended', 'ended', 'ended'])
    await page.getByRole('button', { name: 'Permanecer' }).click()
    await expect(photo(page, 1)).toHaveAttribute('src', original!)
    await cameraButton(page, 'Refazer foto 1').click()
    await expect(shoot(page)).toBeEnabled()
    await page.getByRole('link', { name: 'Voltar ao evento' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    expect((await cameraState(page)).tracks).toEqual(['ended', 'ended', 'ended', 'ended'])
    await page.getByRole('button', { name: 'Permanecer' }).click()
    await page.getByRole('button', { name: 'Descartar', exact: true }).click()
    await page.getByRole('button', { name: 'Descartar rascunho' }).click()
    await expect(photo(page, 1)).toHaveCount(0)
    expect((await cameraState(page)).revoked).toContain(original)
    const saved = await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]))
    expect(saved).not.toContain(privateCaption)
    expect(saved).not.toContain('Foto da câmera')
    await selectPhotoFormat(page, '/e/' + second + '/depoimento')
    await expect(photo(page, 1)).toHaveCount(0)
    expect((await cameraState(page)).requests).toHaveLength(0)
    expect(writes).toEqual([])
  })
}

import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { open, readFile } from 'node:fs/promises'

const code = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const second = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
const eventCode = /^[A-Za-z0-9_-]{43}$/
if (!code || !second || code === second || !eventCode.test(code) || !eventCode.test(second) || !clock) {
  throw new Error('Informe o relógio e os eventos da stack local descartável.')
}

const testimonial = '/e/' + code + '/depoimento'
const moment = '/e/' + code + '/momento'
const privateCaption = 'Legenda privada da gravação sintética'

type Trace = {
  requests: MediaStreamConstraints[]
  tracks: MediaStreamTrack[]
  recording: { mime: string; start: number; stop?: number; lastRelease?: number; bytes: number }[]
  revoked: string[]
  errors: string[]
}

async function observe(page: Page, denied = false) {
  await page.addInitScript(denied => {
    const trace: Trace = { requests: [], tracks: [], recording: [], revoked: [], errors: [] }
    Reflect.set(window, 'recordingTrace', trace)
    const thisOriginDevice = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
    navigator.mediaDevices.getUserMedia = async constraints => {
      trace.requests.push(constraints ?? {})
      try {
        if (denied) throw new DOMException('Permissão recusada na fixture sintética.', 'NotAllowedError')
        const stream = await thisOriginDevice(constraints)
        trace.tracks.push(...stream.getTracks())
        return stream
      } catch (error) {
        if (error instanceof DOMException) trace.errors.push(error.name)
        throw error
      }
    }
    const revoke = URL.revokeObjectURL.bind(URL)
    URL.revokeObjectURL = url => { trace.revoked.push(url); revoke(url) }
    const native = window.MediaRecorder
    window.MediaRecorder = class extends native {
      override start(timeslice?: number) {
        const entry: Trace['recording'][number] = { mime: this.mimeType, start: performance.now(), bytes: 0 }
        trace.recording.push(entry)
        this.addEventListener('dataavailable', event => { entry.bytes += event.data.size })
        for (const track of this.stream.getTracks()) {
          const stop = track.stop.bind(track)
          track.stop = () => { stop(); entry.lastRelease = performance.now() }
        }
        super.start(timeslice)
      }

      override stop() {
        const entry = trace.recording.at(-1)
        if (entry) entry.stop = performance.now()
        super.stop()
      }
    }
  }, denied)
}

function trace(page: Page) {
  return page.evaluate(() => {
    const current = Reflect.get(window, 'recordingTrace') as Trace
    return { ...current, tracks: current.tracks.map(track => [track.kind, track.readyState]) }
  })
}

function stop(page: Page) { return page.getByRole('button', { name: 'Parar gravação' }) }
function timer(page: Page) { return page.getByRole('timer', { name: 'Tempo restante' }) }
function record(page: Page, kind: 'vídeo' | 'áudio') { return page.getByRole('button', { name: 'Gravar ' + kind, exact: true }) }
function review(page: Page) { return page.getByRole('region', { name: 'Prévia da mídia' }) }

async function editor(page: Page, path: string, kind: 'vídeo' | 'áudio') {
  const response = await page.goto(path, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'Selecionar ' + kind, exact: true }).click()
  return response
}

async function waitForMedia(page: Page, kind: 'vídeo' | 'áudio') {
  if (kind === 'vídeo') {
    await expect.poll(() => page.getByLabel('Prévia da câmera').evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.3)
  } else {
    await expect.poll(async () => (await trace(page)).recording.at(-1)?.bytes ?? 0).toBeGreaterThan(0)
  }
}

async function player(page: Page, kind: 'vídeo' | 'áudio') {
  const preview = page.getByLabel('Prévia do ' + kind, { exact: true })
  await expect(preview).toBeVisible()
  await expect.poll(() => preview.evaluate((element: HTMLMediaElement) => element.duration)).toBeGreaterThan(0)
  const duration = await preview.evaluate((element: HTMLMediaElement) => element.duration)
  expect(Number.isFinite(duration)).toBe(true)
  return { preview, duration }
}

async function file(name: string, mimeType: string) {
  return { name, mimeType, buffer: await readFile(new URL('../media/fixtures/' + name, import.meta.url)) }
}

test.beforeEach(async () => {
  const handle = await open(clock!, 'r+')
  try {
    await handle.write('2032-02-29T04:00:00.000Z', 0, 'ascii')
    await handle.sync()
  } finally { await handle.close() }
})

if (process.env.SCRAPBOOK_TEST_RECORDING_4G === 'true') {
  test('mede 4G, parada real de 60 segundos e liberação em gravações repetidas', async ({ page, context, browserName }) => {
    test.setTimeout(100_000)
    expect(browserName).toBe('chromium')
    await observe(page)
    const network = await context.newCDPSession(page)
    await network.send('Network.enable')
    await network.send('Network.setCacheDisabled', { cacheDisabled: true })
    await network.send('Network.emulateNetworkConditions', {
      offline: false, latency: 150, downloadThroughput: 200_000, uploadThroughput: 93_750, connectionType: 'cellular4g',
    })
    await editor(page, moment, 'vídeo')
    const readiness = await page.evaluate(() => Math.round(performance.now()))
    expect(readiness).toBeLessThanOrEqual(3000)
    const samples = []
    for (let attempt = 0; attempt < 3; attempt++) {
      if (attempt === 0) await record(page, 'vídeo').click()
      else {
        await page.getByRole('button', { name: 'Editar vídeo e legenda' }).click()
        await page.getByRole('button', { name: 'Refazer gravação' }).click()
      }
      await expect(stop(page)).toBeEnabled()
      if (attempt > 0) {
        await waitForMedia(page, 'vídeo')
        await stop(page).click()
      }
      await expect(review(page)).toBeVisible({ timeout: 62_000 })
      const clip = await player(page, 'vídeo')
      const finish = await page.evaluate(() => performance.now())
      const entry = (await trace(page)).recording[attempt]!
      expect(clip.duration).toBeLessThanOrEqual(60)
      expect(entry.stop).toBeDefined()
      expect(entry.lastRelease).toBeDefined()
      const elapsed = entry.stop! - entry.start
      expect((await trace(page)).tracks.every(([, ready]) => ready === 'ended')).toBe(true)
      samples.push({ elapsed: Math.round(elapsed), release: Math.round(entry.lastRelease! - entry.stop!),
        preview: Math.round(finish - entry.stop!), seconds: clip.duration, bytes: entry.bytes })
    }
    console.log('Gravação 4G', JSON.stringify({ readiness, samples }))
  })
} else {
  test('CA1 e CA4 gravam e revisam vídeo, refazem e preservam a legenda', async ({ page, browserName }) => {
    await observe(page)
    const response = await editor(page, testimonial, 'vídeo')
    expect(response?.headers()['permissions-policy']).toBe('camera=(self), microphone=(self), geolocation=()')
    expect((await trace(page)).requests).toEqual([])
    await record(page, 'vídeo').click()
    await expect(timer(page)).toHaveText('5:00')
    await expect(stop(page)).toBeFocused()
    await waitForMedia(page, 'vídeo')
    await stop(page).click()
    await expect(review(page)).toBeVisible()
    const { preview } = await player(page, 'vídeo')
    expect(await preview.evaluate((element: HTMLVideoElement) => element.paused && !element.autoplay)).toBe(true)
    const previous = await preview.getAttribute('src')
    await page.getByRole('button', { name: 'Editar vídeo e legenda' }).click()
    await page.getByRole('button', { name: 'Deseja adicionar uma legenda?' }).click()
    await page.getByLabel('Legenda do vídeo').fill(privateCaption)
    await page.getByRole('button', { name: 'Refazer gravação' }).click()
    await expect(stop(page)).toBeEnabled()
    expect((await new AxeBuilder({ page }).include('.media-recording').analyze()).violations).toEqual([])
    await waitForMedia(page, 'vídeo')
    await stop(page).click()
    await expect(review(page).getByText(privateCaption)).toBeVisible()
    const replay = await player(page, 'vídeo')
    await expect(replay.preview).not.toHaveAttribute('src', previous!)
    const proof = await trace(page)
    expect(proof.revoked).toContain(previous)
    expect(proof.requests).toEqual(Array(2).fill(expect.objectContaining({ audio: true, video: expect.any(Object) })))
    expect(proof.tracks.every(([, ready]) => ready === 'ended')).toBe(true)
    console.log(browserName + ' vídeo → ' + proof.recording[0]?.mime)
  })

  for (const { path, kind, seconds } of [
    { path: moment, kind: 'vídeo', seconds: 60 },
    { path: testimonial, kind: 'vídeo', seconds: 300 },
    { path: testimonial, kind: 'áudio', seconds: 300 },
  ] as const) {
    test(`CA1 e CA4 encerram ${kind} em ${seconds} s e abrem revisão local`, async ({ page, browserName }) => {
      await observe(page)
      await page.clock.install({ time: new Date('2041-03-01T12:00:00Z') })
      await page.clock.pauseAt(new Date('2041-03-01T12:00:01Z'))
      const writes: string[] = []
      page.on('request', request => {
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(request.method())
      })
      await editor(page, path, kind)
      if (path === moment) await expect(page.getByRole('button', { name: 'Selecionar áudio' })).toHaveCount(0)
      await record(page, kind).click()
      await expect(timer(page)).toHaveText(seconds === 60 ? '1:00' : '5:00')
      await waitForMedia(page, kind)
      await page.clock.fastForward((seconds - 1) * 1000)
      await expect(timer(page)).toHaveText('0:01')
      await expect(stop(page)).toBeEnabled()
      await page.clock.fastForward(1000)
      await expect(review(page)).toBeVisible()
      const result = await player(page, kind)
      expect(result.duration).toBeLessThanOrEqual(seconds)
      const proof = await trace(page)
      expect(proof.recording[0]!.stop! - proof.recording[0]!.start).toBe(seconds * 1000)
      expect(proof.tracks.every(([, ready]) => ready === 'ended')).toBe(true)
      if (kind === 'áudio') expect(proof.requests).toEqual([{ audio: true, video: false }])
      expect(writes).toEqual([])
      console.log(browserName + ' ' + kind + ' → ' + proof.recording[0]?.mime)
    })
  }

  test('CA2 e CA3 preservam a seleção quando permissão ou codec impedem gravação', async ({ page, browserName }) => {
    await observe(page, browserName === 'firefox')
    if (browserName === 'chromium') {
      await page.route(url => url.pathname === testimonial, async route => {
        const response = await route.fetch()
        await route.fulfill({ response, headers: { ...response.headers(), 'permissions-policy': 'microphone=()' } })
      })
    }
    await editor(page, testimonial, 'áudio')
    await record(page, 'áudio').click()
    await expect(page.getByRole('alert')).toHaveText('Permita o acesso no navegador ou selecione um arquivo do dispositivo.')
    expect((await trace(page)).errors).toEqual(['NotAllowedError'])
    await page.getByLabel('Selecionar áudio', { exact: true }).setInputFiles(await file('tone.wav', 'audio/wav'))
    const { preview } = await player(page, 'áudio')
    const original = await preview.getAttribute('src')
    await page.evaluate(() => { MediaRecorder.isTypeSupported = () => false })
    await page.getByRole('button', { name: 'Refazer gravação' }).click()
    await expect(page.getByRole('alert')).toContainText('A gravação não está disponível neste navegador.')
    await expect(preview).toHaveAttribute('src', original!)
    expect((await trace(page)).requests).toHaveLength(1)
    await expect(page.getByRole('button', { name: 'Revisar', exact: true })).toBeEnabled()
  })

  test('CA2 e CA4 preservam mídia válida, confirmam descarte e liberam recursos', async ({ page }) => {
    await observe(page)
    const writes: string[] = []
    page.on('request', request => {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(request.method())
    })
    await editor(page, testimonial, 'vídeo')
    await page.getByLabel('Selecionar vídeo', { exact: true }).setInputFiles(await file('short.mp4', 'video/mp4'))
    const { preview } = await player(page, 'vídeo')
    const original = await preview.getAttribute('src')
    await page.getByRole('button', { name: 'Deseja adicionar uma legenda?' }).click()
    await page.getByLabel('Legenda do vídeo').fill(privateCaption)
    await page.getByRole('button', { name: 'Refazer gravação' }).click()
    await expect(stop(page)).toBeEnabled()
    const ended = await page.evaluate(() => {
      const [track] = (Reflect.get(window, 'recordingTrace') as Trace).tracks
      track!.stop()
      return track!.readyState
    })
    expect(ended).toBe('ended')
    await expect(page.getByRole('alert')).toContainText('Não foi possível preparar a prévia.')
    await expect(preview).toHaveAttribute('src', original!)
    await expect(page.getByLabel('Legenda do vídeo')).toHaveValue(privateCaption)
    await page.getByRole('button', { name: 'Refazer gravação' }).click()
    await expect(stop(page)).toBeEnabled()
    await page.getByRole('link', { name: 'Voltar ao evento' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.getByRole('button', { name: 'Permanecer' }).click()
    await expect(stop(page)).toBeEnabled()
    await page.getByRole('button', { name: 'Descartar', exact: true }).click()
    await page.getByRole('button', { name: 'Descartar rascunho' }).click()
    await expect(page.getByRole('button', { name: 'Selecionar vídeo' })).toBeVisible()
    const proof = await trace(page)
    expect(proof.revoked).toContain(original)
    expect(proof.tracks.every(([, ready]) => ready === 'ended')).toBe(true)
    expect(writes).toEqual([])
    const stored = await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]))
    expect(stored).not.toContain(privateCaption)
    await editor(page, '/e/' + second + '/depoimento', 'vídeo')
    await expect(preview).toHaveCount(0)
    expect((await trace(page)).requests).toHaveLength(0)
  })
}

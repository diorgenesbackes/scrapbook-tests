import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { open, readFile } from 'node:fs/promises'

const code = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const other = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
const pattern = /^[A-Za-z0-9_-]{43}$/
if (!code || !other || code === other || !pattern.test(code) || !pattern.test(other) || !clock) {
  throw new Error('Informe o relógio e os eventos da stack local descartável.')
}

const eventPath = '/e/' + code
const testimonial = eventPath + '/depoimento'
const moment = eventPath + '/momento'
const videos = [['short.mp4', 'video/mp4', 'MP4/H.264'], ['short.mov', 'video/quicktime', 'MOV/H.264'], ['short.webm', 'video/webm', 'WebM/VP8']] as const
const audios = [['tone.mp3', 'audio/mpeg', 'MP3/Layer III'], ['tone.m4a', 'audio/mp4', 'M4A/AAC'], ['tone.wav', 'audio/wav', 'WAV/PCM'], ['tone.ogg', 'audio/ogg', 'OGG/Vorbis'], ['tone.webm', 'audio/webm', 'WebM/Opus']] as const

async function setTime(value: string) {
  const handle = await open(clock!, 'r+')
  try {
    await handle.write(new Date(value).toISOString(), 0, 'ascii')
    await handle.sync()
  } finally { await handle.close() }
}

function sample(name: string) { return readFile(new URL('./fixtures/' + name, import.meta.url)) }

async function payload(name: string, mimeType: string, alias = name) { return { name: alias, mimeType, buffer: await sample(name) } }

async function editor(page: Page, tag: 'video' | 'audio') {
  const label = tag === 'video' ? 'Selecionar vídeo' : 'Selecionar áudio'
  await page.getByRole('button', { name: label }).click()
  return page.getByLabel(label, { exact: true })
}

function review(page: Page) { return page.getByRole('button', { name: 'Revisar', exact: true }) }

function wav(seconds: number) {
  const sampleRate = 8000
  const size = seconds * sampleRate * 2
  const result = Buffer.alloc(44 + size)
  result.write('RIFF', 0); result.writeUInt32LE(36 + size, 4); result.write('WAVEfmt ', 8)
  result.writeUInt32LE(16, 16); result.writeUInt16LE(1, 20); result.writeUInt16LE(1, 22)
  result.writeUInt32LE(sampleRate, 24); result.writeUInt32LE(sampleRate * 2, 28)
  result.writeUInt16LE(2, 32); result.writeUInt16LE(16, 34); result.write('data', 36); result.writeUInt32LE(size, 40)
  return { name: 'duração-' + seconds + '.wav', mimeType: 'audio/wav', buffer: result }
}

async function nativeSupport(page: Page, buffer: Buffer, mime: string, tag: 'video' | 'audio') {
  return page.evaluate(({ encoded, mime, tag }) => new Promise<boolean>(resolve => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0))
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }))
    const video = document.createElement('video')
    const timeout = window.setTimeout(() => done(false), 5_000)
    function done(result: boolean) {
      window.clearTimeout(timeout)
      video.onloadeddata = null
      video.onerror = null
      video.removeAttribute('src')
      video.load()
      URL.revokeObjectURL(url)
      resolve(result)
    }
    video.preload = 'auto'
    video.onloadeddata = () => done(Number.isFinite(video.duration) && (video.videoWidth > 0) === (tag === 'video'))
    video.onerror = () => done(false)
    video.src = url
  }), { encoded: buffer.toString('base64'), mime, tag })
}

test.beforeEach(async () => setTime('2032-02-29T04:00:00Z'))

if (process.env.SCRAPBOOK_TEST_MEDIA_4G === 'true') {
  test('mede prontidão, metadados e prévia em 4G para MP4, WebM e WAV', async ({ browser, baseURL, browserName }) => {
    test.setTimeout(100_000)
    expect(browserName).toBe('chromium')
    for (const [name, mime, codec] of [videos[0], videos[2], audios[2]]) {
      const file = await payload(name, mime)
      const editorTimes: number[] = []
      const metadataTimes: number[] = []
      const previewTimes: number[] = []
      for (let attempt = 0; attempt < 5; attempt++) {
        const context = await browser.newContext({ baseURL, viewport: { width: 393, height: 851 }, isMobile: true, hasTouch: true })
        try {
          const page = await context.newPage()
          await page.addInitScript(() => {
            const create = document.createElement.bind(document)
            document.createElement = (name, options) => {
              const element = create(name, options)
              if (name === 'video') element.addEventListener('loadedmetadata', () => {
                if (!Reflect.get(window, 'testMetadataAt')) Reflect.set(window, 'testMetadataAt', performance.now())
              }, { once: true })
              return element
            }
          })
          const network = await context.newCDPSession(page)
          await network.send('Network.enable')
          await network.send('Network.setCacheDisabled', { cacheDisabled: true })
          await network.send('Network.emulateNetworkConditions', {
            offline: false, latency: 150, downloadThroughput: 200_000, uploadThroughput: 93_750, connectionType: 'cellular4g',
          })
          await page.goto(testimonial, { waitUntil: 'domcontentloaded' })
          const field = await editor(page, mime.startsWith('video') ? 'video' : 'audio')
          await expect(field).toBeEnabled()
          const start = await page.evaluate(() => performance.now())
          editorTimes.push(Math.round(start))
          await field.setInputFiles(file)
          await expect(review(page)).toBeEnabled()
          const finish = await page.evaluate(() => ({ now: performance.now(), metadata: Number(Reflect.get(window, 'testMetadataAt')) }))
          expect(finish.metadata).toBeGreaterThanOrEqual(start)
          metadataTimes.push(Math.round(finish.metadata - start))
          previewTimes.push(Math.round(finish.now - start))
        } finally { await context.close() }
      }
      console.log(`${codec} ${file.buffer.length} bytes | seletor ms ${editorTimes.join(', ')} | metadados ms ${metadataTimes.join(', ')} | prévia ms ${previewTimes.join(', ')}`)
      expect(Math.max(...editorTimes)).toBeLessThanOrEqual(3000)
    }
  })
} else {
  test('CA1: restringe formatos por categoria e aplica as duas bordas de duração do vídeo', async ({ page }) => {
    await page.goto(eventPath)
    await page.getByRole('link', { name: 'Registre um momento' }).click()
    await expect(page.getByRole('button', { name: 'Selecionar áudio' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Escrever depoimento' })).toHaveCount(0)
    const field = await editor(page, 'video')
    await field.setInputFiles(await payload('moment-over.mp4', 'video/mp4'))
    await expect(page.getByRole('alert')).toContainText('Este vídeo de momento deve ter até 1 minuto e 500 MB.')
    await expect(review(page)).toBeDisabled()
    await field.setInputFiles(await payload('moment-boundary.mp4', 'video/mp4'))
    await expect(review(page)).toBeEnabled()
    await review(page).click()
    await expect(page.getByRole('heading', { name: 'Revisar momento' })).toBeVisible()
    await expect.poll(() => page.locator('video').evaluate(video => video.duration)).toBe(60)
    await page.getByRole('button', { name: 'Descartar', exact: true }).click()
    await page.getByRole('button', { name: 'Descartar rascunho' }).click()

    await page.goto(testimonial)
    const video = await editor(page, 'video')
    await video.setInputFiles(await payload('testimonial-over.mp4', 'video/mp4'))
    await expect(page.getByRole('alert')).toContainText('Este vídeo de depoimento deve ter até 5 minutos e 500 MB.')
    await video.setInputFiles(await payload('testimonial-boundary.mp4', 'video/mp4'))
    await expect(review(page)).toBeEnabled()
    await review(page).click()
    await expect.poll(() => page.locator('video').evaluate(video => video.duration)).toBe(300)
  })

  test('CA1 e CA2: valida áudio vazio, faixa incompatível e a duração de 300 segundos', async ({ page }) => {
    await page.goto(testimonial)
    const field = await editor(page, 'audio')
    await field.setInputFiles({ name: 'vazio.wav', mimeType: 'audio/wav', buffer: Buffer.alloc(0) })
    await expect(page.getByRole('alert')).toContainText('vazio.wav O arquivo está vazio.')
    await field.setInputFiles(await payload('short.mp4', 'audio/mp4', 'faixa-incompatível.m4a'))
    await expect(page.getByRole('alert')).toContainText('faixa-incompatível.m4a')
    await expect(review(page)).toBeDisabled()
    await field.setInputFiles(wav(301))
    await expect(page.getByRole('alert')).toContainText('Este áudio de depoimento deve ter até 5 minutos e 50 MB.')
    await field.setInputFiles(wav(300))
    await expect(review(page)).toBeEnabled()
    await review(page).click()
    await expect.poll(() => page.locator('audio').evaluate(audio => audio.duration)).toBe(300)
    expect(await page.locator('audio').evaluate(audio => audio.paused && audio.controls && !audio.autoplay)).toBe(true)
  })

  test('CA2: registra a prévia real para os oito pares de container e codec', async ({ page }, info) => {
    test.setTimeout(90_000)
    for (const [tag, files] of [['video', videos], ['audio', audios]] as const) {
      await page.goto(testimonial)
      const field = await editor(page, tag)
      for (const [name, mime, codec] of files) {
        const fixture = await payload(name, mime)
        const supported = await nativeSupport(page, fixture.buffer, mime, tag)
        await field.setInputFiles(fixture)
        if (supported) {
          await expect(review(page)).toBeEnabled({ timeout: 17_000 })
          await expect.poll(() => page.locator(tag).evaluate((media: HTMLMediaElement) => media.duration)).toBeGreaterThan(0)
          await page.getByRole('button', { name: tag === 'video' ? 'Remover vídeo' : 'Remover áudio' }).click()
        } else {
          await expect(page.getByRole('alert')).toContainText('Não foi possível preparar a prévia.', { timeout: 17_000 })
          await expect(review(page)).toBeDisabled()
        }
        console.log(`${info.project.name} ${codec}: ${supported ? 'prévia disponível' : 'recuperação'}`)
      }
    }
  })

  test('CA2–CA4: recupera a prévia, preserva a legenda e libera a reprodução e URLs', async ({ page }) => {
    await page.addInitScript(() => {
      const urls = new Set<string>()
      Reflect.set(window, 'testMediaUrls', urls)
      const create = URL.createObjectURL.bind(URL)
      const revoke = URL.revokeObjectURL.bind(URL)
      URL.createObjectURL = value => { const url = create(value); urls.add(url); return url }
      URL.revokeObjectURL = url => { urls.delete(url); revoke(url) }
    })
    const writes: string[] = []
    const messages: string[] = []
    page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.method()) })
    page.on('console', message => messages.push(message.text()))
    await page.setViewportSize({ width: 320, height: 740 })
    await page.goto(testimonial)
    const field = await editor(page, 'video')
    await field.setInputFiles({ name: 'disfarce.mp4', mimeType: 'video/mp4', buffer: Buffer.from('<html>') })
    await expect(page.getByRole('alert')).toContainText('Selecione um vídeo MP4, MOV ou WebM.')
    await field.setInputFiles({ name: 'corrompido.mp4', mimeType: 'video/mp4', buffer: (await sample('short.mp4')).subarray(0, 64) })
    await expect(page.getByRole('alert')).toContainText('corrompido.mp4')
    await expect(review(page)).toBeDisabled()
    await field.setInputFiles(await payload('short.mp4', 'video/mp4', 'vídeo-privado.mp4'))
    await expect(review(page)).toBeEnabled()
    await page.getByRole('button', { name: 'Deseja adicionar uma legenda?' }).click()
    const caption = page.getByRole('textbox', { name: 'Legenda do vídeo' })
    await caption.fill('Legenda privada de mídia')
    await page.locator('video').dispatchEvent('error')
    await expect(page.getByRole('alert')).toContainText('Não foi possível preparar a prévia.')
    await expect(caption).toHaveValue('Legenda privada de mídia')
    await expect(review(page)).toBeDisabled()
    await page.getByRole('button', { name: 'Tentar novamente' }).click()
    await review(page).click()
    await expect(page.getByText('Legenda privada de mídia')).toBeVisible()
    const player = page.locator('video')
    expect(await player.evaluate(video => video.paused && video.controls && !video.autoplay)).toBe(true)
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations.map(({ id }) => id)).toEqual([])
    await player.evaluate(async video => { video.muted = true; await video.play(); Reflect.set(window, 'testRetiredPlayer', video) })
    await page.getByRole('button', { name: 'Editar vídeo e legenda' }).click()
    expect(await page.evaluate(() => {
      const previous = Reflect.get(window, 'testRetiredPlayer') as HTMLVideoElement
      return previous.paused && !previous.hasAttribute('src')
    })).toBe(true)
    await page.getByLabel('Trocar vídeo', { exact: true }).setInputFiles(await payload('moment-boundary.mp4', 'video/mp4'))
    await expect(review(page)).toBeEnabled()
    await expect.poll(() => page.evaluate(() => (Reflect.get(window, 'testMediaUrls') as Set<string>).size)).toBe(1)
    await expect(caption).toHaveValue('Legenda privada de mídia')
    const storage = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))
    expect(storage).not.toContain('vídeo-privado.mp4')
    expect(storage).not.toContain('Legenda privada de mídia')
    expect(messages.join('\n')).not.toContain('vídeo-privado.mp4')
    expect(writes).toEqual([])

    await page.getByRole('link', { name: 'Voltar ao evento' }).click()
    await expect(page.getByRole('button', { name: 'Permanecer' })).toBeFocused()
    await page.getByRole('button', { name: 'Permanecer' }).click()
    await expect(caption).toHaveValue('Legenda privada de mídia')
    await page.getByRole('button', { name: 'Descartar', exact: true }).click()
    await page.getByRole('button', { name: 'Descartar rascunho' }).click()
    await expect.poll(() => page.evaluate(() => (Reflect.get(window, 'testMediaUrls') as Set<string>).size)).toBe(0)
    await page.goto('/e/' + other + '/depoimento')
    await editor(page, 'video')
    await expect(review(page)).toBeDisabled()
  })
}

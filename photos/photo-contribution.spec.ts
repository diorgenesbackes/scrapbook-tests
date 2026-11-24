import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { open, readFile } from 'node:fs/promises'

const code = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const other = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
const pattern = /^[A-Za-z0-9_-]{43}$/
if (!code || !other || code === other || !pattern.test(code) || !pattern.test(other) || !clock) {
  throw new Error('Informe o relógio e dois eventos da stack descartável.')
}

const guestPath = '/e/' + code
const moments = guestPath + '/momento'
const testimonial = guestPath + '/depoimento'
const opening = '2032-02-29T04:00:00Z'
const png = await readFile(new URL('./fixtures/palette.png', import.meta.url))
const jpeg = await readFile(new URL('./fixtures/palette.jpg', import.meta.url))
const heic = await readFile(new URL('./fixtures/palette.heic', import.meta.url))

async function setTime(value: string) {
  const handle = await open(clock!, 'r+')
  try {
    await handle.write(new Date(value).toISOString(), 0, 'ascii')
    await handle.sync()
  } finally {
    await handle.close()
  }
}

function photoFile(name: string, buffer = png, mimeType = 'image/png') {
  return { name, buffer, mimeType }
}

async function enterPhotos(page: Page) {
  await page.getByRole('button', { name: 'Selecionar fotos', exact: true }).click()
  return page.getByLabel('Selecionar fotos', { exact: true })
}

function thumbnails(page: Page) {
  return page.getByRole('img', { name: /^Foto \d+$/ })
}

function sources(page: Page) {
  return thumbnails(page).evaluateAll(images => images.map(image => image.getAttribute('src')))
}

function openUrls() {
  return (Reflect.get(window, 'photoTestUrls') as Set<string>).size
}

test.beforeEach(async () => setTime(opening))

if (process.env.SCRAPBOOK_TEST_PHOTOS_4G === 'true') {
  test('mede prontidão e prévias de uma e dez fotos em perfil móvel', async ({ browser, baseURL, browserName }) => {
    test.setTimeout(75_000)
    expect(browserName).toBe('chromium')
    const file = process.env.SCRAPBOOK_TEST_PHOTO_PERFORMANCE_FILE
    if (!file) throw new Error('Informe a fixture de desempenho.')
    const sample = await readFile(file)
    for (const count of [1, 10]) {
      const readiness: number[] = []
      const preview: number[] = []
      for (let attempt = 0; attempt < 5; attempt++) {
        const context = await browser.newContext({ baseURL, viewport: { width: 393, height: 851 }, isMobile: true, hasTouch: true })
        try {
          const page = await context.newPage()
          const network = await context.newCDPSession(page)
          await network.send('Network.enable')
          await network.send('Network.setCacheDisabled', { cacheDisabled: true })
          await network.send('Network.emulateNetworkConditions', {
            offline: false, latency: 150, downloadThroughput: 200_000, uploadThroughput: 93_750, connectionType: 'cellular4g',
          })
          await page.goto(count === 1 ? testimonial : moments, { waitUntil: 'domcontentloaded' })
          const picker = await enterPhotos(page)
          await expect(picker).toBeEnabled()
          readiness.push(Math.round(await page.evaluate(() => performance.now())))
          const start = await page.evaluate(() => performance.now())
          await picker.setInputFiles(Array.from({ length: count }, (_, i) => photoFile('foto-' + i + '.png', sample)))
          await expect(thumbnails(page)).toHaveCount(count)
          await expect.poll(() => thumbnails(page).evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true)
          preview.push(Math.round(await page.evaluate(() => performance.now()) - start))
        } finally {
          await context.close()
        }
      }
      console.log(`Fotos ${count}, ${sample.length} bytes por arquivo, editor ms: ${readiness.join(', ')}; prévias ms: ${preview.join(', ')}`)
      expect(Math.max(...readiness)).toBeLessThanOrEqual(3000)
    }
  })
} else {
  test('CA1: abre as duas rotas por evento e respeita a janela da F05', async ({ page }) => {
    await setTime('2032-02-29T01:59:58Z')
    await page.goto(moments)
    await expect(page.getByText('Novos envios ainda não estão disponíveis.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Selecionar fotos' })).toHaveCount(0)
    await setTime('2032-03-01T15:00:00Z')
    await page.reload()
    await expect(page.getByText('O prazo para novos envios terminou.')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Registre um momento' })).toHaveCount(0)

    await setTime(opening)
    await page.goto(guestPath)
    await expect(page.getByRole('link', { name: 'Registre um momento' })).toHaveAttribute('href', moments)
    await page.getByRole('link', { name: 'Deixe um depoimento' }).click()
    const picker = await enterPhotos(page)
    expect(await picker.evaluate((element: HTMLInputElement) => element.multiple)).toBe(false)
    await picker.setInputFiles(photoFile('depoimento.jpg', jpeg, 'image/jpeg'))
    await expect(thumbnails(page)).toHaveCount(1)
    await expect(picker).toBeDisabled()
    await page.getByRole('button', { name: 'Revisar', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Revisar depoimento' })).toBeVisible()
    await page.getByRole('button', { name: 'Confirmar identificação' }).click()
    await page.getByRole('textbox', { name: 'Nome ou apelido' }).fill('Convidada sintética das fotos')
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: 'Continuar', exact: true }).click()
    await expect(page.getByText('Identificação confirmada.')).toBeVisible()
    await expect(thumbnails(page)).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'Enviar' })).toBeDisabled()
  })

  test('CA1 e CA3: mantém a ordem, troca e remove fotos e limita a legenda do conjunto', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 740 })
    await page.goto(moments)
    await expect(page.getByRole('button', { name: 'Escrever depoimento' })).toHaveCount(0)
    const picker = await enterPhotos(page)
    await picker.setInputFiles(Array.from({ length: 11 }, (_, i) => photoFile('imagem-' + i + '.png')))
    await expect(thumbnails(page)).toHaveCount(10)
    await expect(page.getByRole('alert')).toContainText('imagem-10.png O momento aceita até dez fotos.')
    const review = page.getByRole('button', { name: 'Revisar', exact: true })
    await expect(review).toBeDisabled()
    await page.getByRole('button', { name: 'Descartar arquivos rejeitados' }).click()
    const original = await sources(page)
    await page.getByLabel('Trocar foto 2', { exact: true }).setInputFiles(photoFile('substituta.jpg', jpeg, 'image/jpeg'))
    await expect.poll(async () => (await sources(page))[1]).not.toBe(original[1])
    const switched = await sources(page)
    expect([switched[0], ...switched.slice(2)]).toEqual([original[0], ...original.slice(2)])
    await page.getByRole('button', { name: 'Remover foto 1', exact: true }).click()
    expect(await sources(page)).toEqual(switched.slice(1))

    await page.getByRole('button', { name: 'Deseja adicionar uma legenda?' }).click()
    const caption = page.getByRole('textbox', { name: 'Legenda das fotos' })
    await caption.fill('a'.repeat(399))
    await expect(page.getByText(/caracteres restantes na legenda/)).toHaveCount(0)
    await caption.fill('a'.repeat(400))
    await expect(page.getByText('100 caracteres restantes na legenda.')).toBeVisible()
    await caption.fill('😀'.repeat(500))
    await expect(page.getByText('0 caracteres restantes na legenda.')).toBeVisible()
    await caption.fill('😀'.repeat(501))
    await expect(page.getByRole('alert')).toHaveText('A legenda deve ter até 500 caracteres.')
    await caption.fill('Uma legenda para o conjunto.')
    await review.click()
    expect(await sources(page)).toEqual(switched.slice(1))
    await expect(page.getByText('Uma legenda para o conjunto.', { exact: true })).toHaveCount(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations.map(({ id }) => id)).toEqual([])
    await page.getByRole('button', { name: 'Editar fotos e legenda' }).click()
    await expect(caption).toHaveValue('Uma legenda para o conjunto.')
  })

  test('CA1 e CA2: separa erros de tamanho, formato, decodificação e compatibilidade HEIC', async ({ page }, info) => {
    await page.goto(moments)
    const picker = await enterPhotos(page)
    await picker.setInputFiles([
      photoFile('valida.png'), photoFile('vazia.png', Buffer.alloc(0)),
      photoFile('grande.png', Buffer.alloc(20_000_001)), photoFile('marcacao-privada.png', Buffer.from('<svg/>')),
      photoFile('corrompida.png', png.subarray(0, 20)),
    ])
    const errors = page.getByRole('alert')
    await expect(errors).toContainText('vazia.png O arquivo está vazio.')
    await expect(errors).toContainText('grande.png Cada foto deve ter até 20 MB.')
    await expect(errors).toContainText('marcacao-privada.png Selecione uma foto JPEG, PNG ou HEIC/HEIF.')
    await expect(errors).toContainText('corrompida.png Não foi possível exibir a prévia desta foto.')
    await expect(thumbnails(page)).toHaveCount(1)

    const native = await page.evaluate(async bytes => {
      const url = URL.createObjectURL(new Blob([Uint8Array.from(bytes)], { type: 'image/heic' }))
      try {
        const image = new Image()
        image.src = url
        await image.decode()
        return image.naturalWidth > 0
      } catch { return false }
      finally { URL.revokeObjectURL(url) }
    }, Array.from(heic))
    await picker.setInputFiles(photoFile('amostra.heic', heic, 'image/heic'))
    if (native) await expect(thumbnails(page)).toHaveCount(2)
    else {
      await expect(errors).toContainText('amostra.heic Não foi possível exibir a prévia desta foto.')
      await expect(thumbnails(page)).toHaveCount(1)
    }
    console.log(`HEIC sintético em ${info.project.name}: ${native ? 'decodificado' : 'prévia indisponível com recuperação'}`)
  })

  test('CA4: protege a saída, revoga prévias e mantém arquivos e legenda locais', async ({ page }) => {
    await page.addInitScript(() => {
      const urls = new Set<string>()
      Reflect.set(window, 'photoTestUrls', urls)
      const create = URL.createObjectURL.bind(URL)
      const revoke = URL.revokeObjectURL.bind(URL)
      URL.createObjectURL = object => {
        const url = create(object)
        urls.add(url)
        return url
      }
      URL.revokeObjectURL = url => { urls.delete(url); revoke(url) }
    })
    const writes: string[] = []
    const consoleLines: string[] = []
    page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.method()) })
    page.on('console', message => consoleLines.push(message.text()))
    await page.goto(moments)
    const picker = await enterPhotos(page)
    await picker.setInputFiles([photoFile('arquivo-privado.png'), photoFile('segunda.png')])
    await expect(thumbnails(page)).toHaveCount(2)
    await expect.poll(() => page.evaluate(openUrls)).toBe(2)
    await page.getByLabel('Trocar foto 2', { exact: true }).setInputFiles(photoFile('nova.jpg', jpeg, 'image/jpeg'))
    await expect(picker).toBeEnabled()
    await expect.poll(() => page.evaluate(openUrls)).toBe(2)
    await page.getByRole('button', { name: 'Remover foto 2', exact: true }).click()
    await expect.poll(() => page.evaluate(openUrls)).toBe(1)
    await page.getByRole('button', { name: 'Deseja adicionar uma legenda?' }).click()
    await page.getByRole('textbox', { name: 'Legenda das fotos' }).fill('Legenda privada de teste')
    await page.getByRole('button', { name: 'Revisar', exact: true }).click()
    const back = page.getByRole('link', { name: 'Voltar ao evento' })
    await back.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Permanecer' })).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(back).toBeFocused()
    await expect(thumbnails(page)).toHaveCount(1)
    const storage = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))
    expect(storage).not.toContain('arquivo-privado.png')
    expect(storage).not.toContain('Legenda privada de teste')
    expect(consoleLines.join('\n')).not.toContain('arquivo-privado.png')
    expect(writes).toEqual([])
    await page.getByRole('button', { name: 'Descartar', exact: true }).click()
    await page.getByRole('button', { name: 'Descartar rascunho' }).click()
    await expect.poll(() => page.evaluate(openUrls)).toBe(0)
    await page.goto('/e/' + other + '/momento')
    await enterPhotos(page)
    await expect(thumbnails(page)).toHaveCount(0)
  })
}

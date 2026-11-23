import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import jsQR from 'jsqr'

async function decodeQr(page: Page, mime: string, source: Buffer) {
  const url = 'data:' + mime + ';base64,' + source.toString('base64')
  const rendered = await page.evaluate(async dataUrl => {
    const image = new Image()
    image.src = dataUrl
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 384
    const context = canvas.getContext('2d')!
    context.imageSmoothingEnabled = false
    context.drawImage(image, 0, 0, 384, 384)
    return { width: image.naturalWidth, pixels: Array.from(context.getImageData(0, 0, 384, 384).data) }
  }, url)
  if (mime === 'image/png') expect(rendered.width).toBeGreaterThanOrEqual(1024)
  return jsQR(new Uint8ClampedArray(rendered.pixels), 384, 384)?.data
}

if (process.env.SCRAPBOOK_TEST_PUBLIC_4G !== 'true') {
  test('compartilha e imprime o mesmo destino que os convidados abrem', async ({ page }) => {
    const eventId = process.env.SCRAPBOOK_TEST_PUBLIC_EVENT_ID
    const authorization = process.env.SCRAPBOOK_TEST_AUTHORIZATION
    const code = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
    if (!eventId || !authorization || !code) throw new Error('Use a stack descartável de compartilhamento.')

    await page.route('**/api/v1/events/**', route => route.continue({
      headers: { ...route.request().headers(), authorization },
    }))
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: async (value: string) => {
          if (document.documentElement.dataset.copied) throw new Error('Bloqueado na fixture')
          document.documentElement.dataset.copied = value
        } },
      })
    })
    await page.goto('/meus-eventos/' + eventId)
    const openShare = page.getByRole('link', { name: 'Compartilhar com convidados' })
    await openShare.focus()
    await page.keyboard.press('Enter')
    const heading = page.getByRole('heading', { name: 'Celebração pública sintética', level: 1 })
    await expect(heading).toBeVisible()
    const expected = new URL('/e/' + code, page.url()).href
    const link = page.getByRole('textbox', { name: 'Link dos convidados' })
    await expect(link).toHaveValue(expected)
    await page.getByRole('button', { name: 'Copiar link' }).press('Enter')
    await expect(page.getByRole('status').getByText('Link copiado.')).toBeVisible()
    expect(await page.locator('html').getAttribute('data-copied')).toBe(expected)
    await page.getByRole('button', { name: 'Copiar link' }).press('Enter')
    await expect(link).toBeFocused()
    expect(await link.evaluate((field: HTMLInputElement) => [field.selectionStart, field.selectionEnd]))
      .toEqual([0, expected.length])

    const qr = page.getByRole('img', { name: 'QR code da página dos convidados' })
    await expect(qr).toBeVisible()
    const inline = await qr.evaluate(image => new XMLSerializer().serializeToString(image))
    expect(await decodeQr(page, 'image/svg+xml', Buffer.from(inline))).toBe(expected)
    for (const [extension, mime] of [['png', 'image/png'], ['svg', 'image/svg+xml']]) {
      const [download] = await Promise.all([
        page.waitForEvent('download'), page.getByRole('button', { name: 'Baixar ' + extension.toUpperCase() }).click(),
      ])
      expect(download.suggestedFilename()).toBe('qr-convidados.' + extension)
      expect(await decodeQr(page, mime, await readFile((await download.path())!))).toBe(expected)
    }

    await page.evaluate(() => { window.print = () => { document.documentElement.dataset.printed = 'true' } })
    await page.getByRole('button', { name: 'Imprimir' }).press('Enter')
    await expect(page.locator('html')).toHaveAttribute('data-printed', 'true')
    await page.emulateMedia({ media: 'print' })
    await expect(heading).toBeVisible()
    await expect(page.getByText(expected, { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Imprimir' })).toBeHidden()
    await expect(page.locator('.site-header')).toBeHidden()
    const printed = await qr.boundingBox()
    expect(printed?.width).toBeGreaterThanOrEqual(226.7)
    expect(printed?.height).toBeGreaterThanOrEqual(226.7)

    await page.emulateMedia({ media: 'screen' })
    await page.goto(expected)
    await expect(heading).toBeVisible()
    await expect(qr).toBeVisible()
    const guest = await qr.evaluate(image => new XMLSerializer().serializeToString(image))
    expect(await decodeQr(page, 'image/svg+xml', Buffer.from(guest))).toBe(expected)
  })
}

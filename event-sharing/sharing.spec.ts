import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { PNG } from 'pngjs'
import jsQR from 'jsqr'

const event = JSON.parse(readFileSync(process.env.SCRAPBOOK_SHARING_MANIFEST!, 'utf8')) as { id: string, code: string, name: string, publicUrl: string }
function decode(bytes: Buffer) {
  const png = PNG.sync.read(bytes)
  const qr = jsQR(Uint8ClampedArray.from(png.data), png.width, png.height, { inversionAttempts: 'dontInvert' })
  expect(qr).not.toBeNull()
  return { png, qr: qr! }
}

test('owner opens sharing, decodes preview and exports a readable PNG', async ({ page }) => {
  await page.goto(`/events/${event.id}`)
  await page.getByRole('link', { name: 'Compartilhar evento' }).click()
  await expect(page.getByRole('heading', { name: 'Compartilhar evento' })).toBeVisible()
  const image = page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })
  await expect(image).toBeVisible()
  const source = await image.getAttribute('src')
  const preview = decode(Buffer.from(source!.split(',')[1]!, 'base64'))
  expect(preview.qr.data).toBe(event.publicUrl)
  // At least four modules of white margin in the original generated bitmap.
  let minX = preview.png.width, minY = preview.png.height, maxX = 0
  for (let y = 0; y < preview.png.height; y++) for (let x = 0; x < preview.png.width; x++) {
    if (preview.png.data[(y * preview.png.width + x) * 4]! < 128) { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x) }
  }
  const moduleSize = (maxX - minX + 1) / (17 + 4 * preview.qr.version)
  expect(Math.min(minX, minY) / moduleSize).toBeGreaterThanOrEqual(3.8)
  expect(decode(await image.screenshot()).qr.data).toBe(event.publicUrl)
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Baixar PNG para compartilhar' }).click()
  const file = await downloaded
  expect(file.suggestedFilename()).toBe(`scrapbook-evento-${event.code}.png`)
  const bytes = readFileSync((await file.path())!)
  const exported = decode(bytes)
  expect(exported.qr.data).toBe(event.publicUrl)
  expect(exported.png.width).toBe(1280)
  expect(exported.png.height).toBeGreaterThan(1024)
  await file.saveAs(`artifacts/event-sharing-material-${test.info().project.name}.png`)
  await page.screenshot({ path: `artifacts/event-sharing-${test.info().project.name}.png`, fullPage: true })
})

test('guest QR is public and its decoded URL opens the same event without login', async ({ page, context }) => {
  await page.goto(`/e/${event.code}`)
  const image = page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })
  await expect(image).toBeVisible()
  const link = decode(await image.screenshot()).qr.data
  const tab = await context.newPage()
  await tab.goto(link)
  await expect(tab.getByRole('heading', { name: event.name })).toBeVisible()
  expect(await context.cookies()).toEqual([])
  await expect(page.getByRole('button', { name: 'Baixar PNG para compartilhar' })).toHaveCount(0)
  await tab.close()
})

test('manual copy and print media preserve the exact link and hide management controls', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => Promise.reject(new Error('denied')) } })
    window.print = () => {}
  })
  await page.goto(`/events/${event.id}/share`)
  await expect(page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })).toBeVisible()
  await page.getByRole('button', { name: 'Copiar link' }).click()
  const input = page.getByRole('textbox', { name: 'Link do evento para copiar' })
  await expect(input).toBeFocused()
  await expect(input).toHaveValue(event.publicUrl)
  await page.getByRole('button', { name: 'Imprimir QR code' }).click()
  await page.emulateMedia({ media: 'print' })
  await expect(page.locator('.event-print-card')).toBeVisible()
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor)).toBe('rgb(255, 255, 255)')
  await expect(page.locator('nav')).not.toBeVisible()
  await expect(page.locator('.share-actions')).not.toBeVisible()
  await expect(page.locator('.event-print-card h1')).toHaveText(event.name)
  await expect(page.locator('.event-print-card p')).toHaveText(event.publicUrl)
  expect(decode(await page.locator('.event-print-card').screenshot()).qr.data).toBe(event.publicUrl)
  await page.screenshot({ path: `artifacts/event-sharing-print-${test.info().project.name}.png`, fullPage: true })
  await page.evaluate(() => window.dispatchEvent(new Event('afterprint')))
})

test('a failed PNG export never starts a download', async ({ page }) => {
  await page.addInitScript(() => { HTMLCanvasElement.prototype.toBlob = callback => { callback(null) } })
  let downloads = 0
  page.on('download', () => downloads++)
  await page.goto(`/events/${event.id}/share`)
  await expect(page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })).toBeVisible()
  await page.getByRole('button', { name: 'Baixar PNG para compartilhar' }).click()
  await expect(page.getByText('Não foi possível preparar o PNG. Tente novamente.')).toBeVisible()
  expect(downloads).toBe(0)
})

test('a configured external destination is not replaced by the current local origin', async ({ page }) => {
  const external = `https://sharing.example.invalid/e/${event.code}`
  await page.route(`**/api/v1/public/events/${event.code}`, async route => {
    const response = await route.fetch()
    await route.fulfill({ response, json: { ...await response.json(), publicUrl: external } })
  })
  await page.goto(`/e/${event.code}`)
  const image = page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })
  await expect(image).toBeVisible()
  expect(decode(await image.screenshot()).qr.data).toBe(external)
  await expect(page.getByRole('link', { name: external })).toHaveAttribute('href', external)
})

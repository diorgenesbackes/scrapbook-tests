import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { PNG } from 'pngjs'
import jsQR from 'jsqr'

const data = JSON.parse(readFileSync(process.env.SCRAPBOOK_IDENTIFICATION_MANIFEST!, 'utf8'))
const marker = 'IC2_PRIVATE_GUEST_🎉'
const key = (code: string) => `scrapbook.guest-identification.v1:${code}`
const path = (code: string) => `/e/${code}/identification`
const record = (name = marker, noticeCode = data.notice.code, code = data.first.code) => ({ version: 1, eventCode: code, name, noticeCode, consent: true })

async function identify(page: Page, code = data.first.code, name = marker) {
  await page.goto(path(code))
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
  await page.getByRole('textbox').fill(name)
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Confirmar identificação' }).click()
  await expect(page.getByText('Sua identificação foi lembrada neste navegador para este evento.')).toBeVisible()
}

test('reload and a new tab reuse only the confirmed event identification', async ({ page, context, browser, baseURL }) => {
  await identify(page)
  expect(await page.evaluate(k => JSON.parse(localStorage.getItem(k)!), key(data.first.code))).toEqual(record())
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Revise sua identificação' })).toBeVisible()
  await expect(page.getByRole('textbox')).toHaveValue(marker)
  await expect(page.getByRole('checkbox')).toBeChecked()
  const tab = await context.newPage()
  await tab.goto(baseURL + path(data.first.code))
  await expect(tab.getByRole('textbox')).toHaveValue(marker)
  await expect(tab.getByRole('checkbox')).toBeChecked()
  const isolated = await browser.newContext()
  try {
    const other = await isolated.newPage()
    await other.goto(baseURL + path(data.first.code))
    await expect(other.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
    await expect(other.getByRole('textbox')).toHaveValue('')
    await expect(other.getByRole('checkbox')).not.toBeChecked()
  } finally { await isolated.close() }
})

test('A to B to A retains distinct names and never applies a delayed context', async ({ page }) => {
  await identify(page)
  await page.goto(path(data.second.code))
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
  await expect(page.getByRole('textbox')).toHaveValue('')
  await identify(page, data.second.code, 'Bia')
  await page.goto(path(data.first.code))
  await expect(page.getByRole('textbox')).toHaveValue(marker)
  await expect(page.getByRole('checkbox')).toBeChecked()
  await page.goto(path(data.second.code))
  await expect(page.getByRole('textbox')).toHaveValue('Bia')
  expect(await page.evaluate(k => JSON.parse(localStorage.getItem(k)!), key(data.first.code))).toEqual(record())
})

test('stored consent is not reused while the current notice is pending or different', async ({ page }) => {
  await identify(page)
  let release: (() => void) | undefined
  const held = new Promise<void>(resolve => { release = resolve })
  await page.route(`**/api/v1/public/events/${data.first.code}/consent-notice`, async route => {
    await held
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ code: 'updated-notice', body: 'Aviso atualizado para o teste.' }) })
  })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('textbox')).toHaveValue('')
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeDisabled()
  release?.()
  await expect(page.getByRole('textbox')).toHaveValue(marker)
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  expect(await page.evaluate(k => JSON.parse(localStorage.getItem(k)!).noticeCode, key(data.first.code))).toBe(data.notice.code)
})

test('deleting one event record asks again and leaves the other event untouched', async ({ page }) => {
  await identify(page)
  await identify(page, data.second.code, 'Bia')
  await page.evaluate(k => localStorage.removeItem(k), key(data.first.code))
  await page.goto(path(data.first.code))
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
  await expect(page.getByRole('textbox')).toHaveValue('')
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  expect(await page.evaluate(k => JSON.parse(localStorage.getItem(k)!).name, key(data.second.code))).toBe('Bia')
})

test('storage getter or quota failure preserves a usable memory-only identification', async ({ browser, baseURL }) => {
  for (const mode of ['getter', 'write']) {
    const context = await browser.newContext()
    try {
      await context.addInitScript(mode => {
        if (mode === 'getter') Object.defineProperty(window, 'localStorage', { configurable: true, get: () => { throw new DOMException('Denied', 'SecurityError') } })
        else Storage.prototype.setItem = () => { throw new DOMException('Quota', 'QuotaExceededError') }
      }, mode)
      const page = await context.newPage()
      const errors: string[] = []
      page.on('pageerror', error => errors.push(error.name))
      await page.goto(baseURL + path(data.first.code))
      await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
      await page.getByRole('textbox').fill(marker)
      await page.getByRole('checkbox').check()
      await page.getByRole('button', { name: 'Confirmar identificação' }).click()
      await expect(page.getByText('Sua identificação está confirmada apenas nesta página.')).toBeVisible()
      await expect(page.getByText(/Não foi possível lembrar/)).toBeVisible()
      await page.reload()
      await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
      await expect(page.getByRole('textbox')).toHaveValue('')
      expect(errors).toEqual([])
    } finally { await context.close() }
  }
})

test('a remembered name can be reviewed and a draft never replaces its confirmed record', async ({ page }, info) => {
  await identify(page)
  await page.reload()
  await page.getByRole('button', { name: 'Alterar nome' }).click()
  await expect(page.getByRole('textbox')).toBeFocused()
  await page.getByRole('textbox').fill('Novo nome')
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  expect(await page.evaluate(k => JSON.parse(localStorage.getItem(k)!).name, key(data.first.code))).toBe(marker)
  await page.reload()
  await expect(page.getByRole('textbox')).toHaveValue(marker)
  await page.setViewportSize({ width: 320, height: 740 })
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: `artifacts/remembered-identification-${info.project.name}-320-text200.png`, fullPage: true })
})

test('another browser origin does not inherit the same event identification', async ({ page, baseURL }) => {
  await identify(page)
  const other = new URL(baseURL!)
  other.hostname = 'localhost'
  await page.goto(other.origin + path(data.first.code))
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
  await expect(page.getByRole('textbox')).toHaveValue('')
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  expect(await page.evaluate(k => localStorage.getItem(k), key(data.first.code))).toBeNull()
})

test('remembered identity stays out of public DOM, traffic, logs and QR exports', async ({ page }) => {
  const requests: { url: string; body: string | null }[] = []
  const consoleMessages: string[] = []
  page.on('request', request => requests.push({ url: request.url(), body: request.postData() }))
  page.on('console', message => consoleMessages.push(message.text()))
  await identify(page)
  await page.goto(`/e/${data.first.code}`)
  await expect(page.getByRole('heading', { name: data.first.name })).toBeVisible()
  expect(await page.locator('body').textContent()).not.toContain(marker)
  expect(requests.some(request => new URL(request.url).pathname.startsWith('/api/v1/events'))).toBe(false)
  const qrImage = page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })
  await expect(qrImage).toBeVisible()
  const decode = (bytes: Buffer) => {
    const png = PNG.sync.read(bytes)
    return jsQR(Uint8ClampedArray.from(png.data), png.width, png.height, { inversionAttempts: 'dontInvert' })?.data
  }
  expect(decode(Buffer.from((await qrImage.getAttribute('src'))!.split(',')[1]!, 'base64'))).toBe(data.first.publicUrl)
  await page.goto(`/events/${data.first.id}/share`)
  await expect(page.getByRole('button', { name: 'Baixar PNG para compartilhar' })).toBeEnabled()
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Baixar PNG para compartilhar' }).click()
  const download = await downloading
  expect(decode(readFileSync((await download.path())!))).toBe(data.first.publicUrl)
  expect(download.suggestedFilename()).not.toContain(marker)
  expect(await page.locator('body').textContent()).not.toContain(marker)
  expect(JSON.stringify(requests)).not.toContain('IC2_PRIVATE_GUEST')
  expect(consoleMessages.join('\n')).not.toContain('IC2_PRIVATE_GUEST')
  expect(await page.title()).not.toContain('IC2_PRIVATE_GUEST')
})

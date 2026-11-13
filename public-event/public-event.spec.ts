import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

const manifest = JSON.parse(readFileSync(process.env.SCRAPBOOK_PUBLIC_EVENT_MANIFEST!, 'utf8')) as { code: string, otherCode: string }

test('anonymous direct link, reload and new tab in the Nginx container', async ({ page, context, browserName }) => {
  const calls: string[] = []
  const credentials: string[] = []
  page.on('request', request => {
    if (request.url().includes('/api/')) {
      calls.push(new URL(request.url()).pathname)
      const headers = request.headers()
      for (const key of ['cookie', 'authorization', 'x-development-account-id']) if (headers[key]) credentials.push(key)
    }
  })
  await page.goto(`/e/${manifest.code}`)
  await expect(page.getByText('Acesso ao evento confirmado.')).toBeVisible()
  await expect(page.locator('h1')).toBeFocused()
  await expect(page.getByRole('link', { name: 'Meus eventos' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /enviar|registrar/i })).toHaveCount(0)
  expect(calls).toEqual([`/api/v1/public/events/${manifest.code}`])
  expect(credentials).toEqual([])
  expect(await context.cookies()).toEqual([])
  // WebKit's default keyboard mode includes links with Option+Tab.
  await page.keyboard.press(browserName === 'webkit' ? 'Alt+Shift+Tab' : 'Shift+Tab')
  await expect(page.getByRole('link', { name: 'Ir para o conteúdo' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('main')).toBeFocused()
  await page.reload()
  await expect(page.getByText('Acesso ao evento confirmado.')).toBeVisible()
  const tab = await context.newPage()
  await tab.goto(`/e/${manifest.otherCode}`)
  await expect(tab.getByText('Acesso ao evento confirmado.')).toBeVisible()
  await tab.close()
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([])
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([])
  await page.screenshot({ path: `artifacts/public-event-${test.info().project.name}.png`, fullPage: true })
})

test('invalid code and numeric PK remain in guest layout without login', async ({ page }) => {
  for (const suffix of ['1', 'missing', 'A'.repeat(43), '']) {
    await page.goto(`/e/${suffix}`)
    await expect(page.getByText('Evento não encontrado. Confira o link recebido.')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Meus eventos' })).toHaveCount(0)
    expect(new URL(page.url()).pathname).toBe(`/e/${suffix}`)
  }
})

test('guest layout fits mobile widths and 200 percent text', async ({ page }) => {
  await page.goto(`/e/${manifest.code}`)
  await expect(page.getByText('Acesso ao evento confirmado.')).toBeVisible()
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }
  await page.setViewportSize({ width: 320, height: 900 })
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await expect(page.locator('h1')).toBeVisible()
})

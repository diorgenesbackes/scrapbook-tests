import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

const fixtures = JSON.parse(readFileSync(process.env.SCRAPBOOK_PRESENTATION_MANIFEST!, 'utf8')) as Record<string, { code: string, name: string, effectiveWelcomeMessage: string }>
const normal = fixtures['normal']!

test('renders real public presentation and only requests approved public resources', async ({ page, browserName }) => {
  const forbidden: string[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/v1/public/events/')) forbidden.push(url.pathname)
    if (url.hostname !== '127.0.0.1') forbidden.push(url.origin)
  })
  await page.goto(`/e/${normal.code}`)
  await expect(page.getByRole('heading', { name: normal.name })).toBeFocused()
  await expect(page.getByRole('img')).toBeVisible()
  await expect(page.locator('.guest-message')).toHaveText(normal.effectiveWelcomeMessage)
  expect(await page.locator('.guest-message').evaluate(element => getComputedStyle(element).whiteSpace)).toBe('pre-wrap')
  const tree = await page.locator('main').ariaSnapshot()
  expect(tree).toContain(normal.name)
  expect(tree).toContain('Ilustração de uma paisagem')
  expect(tree).toContain('Acesso ao evento confirmado.')
  await page.keyboard.press(browserName === 'webkit' ? 'Alt+Shift+Tab' : 'Shift+Tab')
  await expect(page.getByRole('link', { name: 'Ir para o conteúdo' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('main')).toBeFocused()
  expect(forbidden).toEqual([])
  await page.screenshot({ path: `artifacts/public-presentation-${test.info().project.name}.png`, fullPage: true })
})

test('shows server default and personalized Unicode as safe text', async ({ page }) => {
  for (const key of ['default', 'markup']) {
    const event = fixtures[key]!
    await page.goto(`/e/${event.code}`)
    await expect(page.getByRole('heading', { name: event.name })).toBeVisible()
    expect(await page.locator('.guest-message').textContent()).toBe(event.effectiveWelcomeMessage)
    expect(await page.evaluate(() => '__PP_XSS' in globalThis)).toBe(false)
    await expect(page.locator('main script, main [onerror]')).toHaveCount(0)
  }
})

test('keeps all text visible at supported widths and 200 percent text size', async ({ page }) => {
  const event = fixtures['long']!
  await page.goto(`/e/${event.code}`)
  await expect(page.getByRole('heading', { name: event.name })).toBeVisible()
  expect(await page.locator('.guest-message').textContent()).toBe(event.effectiveWelcomeMessage)
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }
  await page.setViewportSize({ width: 320, height: 900 })
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('isolates a cover failure and preserves its reserved dimensions', async ({ page }) => {
  await page.route('**/assets/event-cover-default-v1.svg', route => route.abort())
  await page.goto(`/e/${normal.code}`)
  await expect(page.getByText('Capa indisponível.')).toBeVisible()
  await expect(page.getByRole('heading', { name: normal.name })).toBeVisible()
  await expect(page.locator('.guest-message')).toHaveText(normal.effectiveWelcomeMessage)
  const box = await page.locator('.guest-cover').boundingBox()
  expect(box!.width / box!.height).toBeCloseTo(1.6, 1)
})

test('revalidates on focus and does not reuse a different event', async ({ page }) => {
  let calls = 0
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/public/events/')) calls++ })
  await page.goto(`/e/${normal.code}`)
  await expect(page.getByRole('heading', { name: normal.name })).toBeVisible()
  const before = calls
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')) })
  await expect.poll(() => calls).toBe(before + 1)
  const other = fixtures['default']!
  await page.goto(`/e/${other.code}`)
  await expect(page.getByRole('heading', { name: other.name })).toBeVisible()
  await expect(page.getByRole('heading', { name: normal.name })).toHaveCount(0)
})

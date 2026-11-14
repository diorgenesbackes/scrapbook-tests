import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
const data = JSON.parse(readFileSync(process.env.SCRAPBOOK_IDENTIFICATION_MANIFEST!, 'utf8'))
const path = `/e/${data.first.code}/identification`
const noticePath = `**/api/v1/public/events/${data.first.code}/consent-notice`
const marker = 'IC_PRIVATE_GUEST_🎉'

test('anonymous S16 to S17 requires name and consent and returns to the event', async ({ page }) => {
  const writes: string[] = []
  page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.method()) })
  await page.goto(`/e/${data.first.code}`)
  await page.getByRole('link', { name: 'Identificar-me para participar' }).click()
  await expect(page.getByRole('heading', { name: 'Como podemos chamar você?' })).toBeFocused()
  await expect(page.getByText(data.notice.body, { exact: true })).toBeVisible()
  const submit = page.getByRole('button', { name: 'Confirmar identificação' })
  await expect(submit).toBeEnabled()
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  await submit.click()
  await expect(page.getByRole('textbox', { name: 'Nome ou apelido' })).toBeFocused()
  await page.getByRole('textbox').fill(marker)
  await submit.click()
  await expect(page.getByRole('checkbox')).toBeFocused()
  await page.getByRole('checkbox').check()
  await submit.click()
  await expect(page.getByRole('heading', { name: 'Identificação confirmada' })).toBeFocused()
  await expect(page.getByText(/Nenhuma lembrança foi enviada/)).toBeVisible()
  expect(await page.evaluate(() => localStorage.length)).toBe(1)
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0)
  expect(page.url()).not.toContain('IC_PRIVATE')
  await page.getByRole('link', { name: 'Voltar ao evento' }).click()
  await expect(page).toHaveURL(new RegExp(`/e/${data.first.code}$`))
  expect(writes).toEqual([])
})

test('name Unicode limits and explicit renewed consent prevent incomplete confirmation', async ({ page }) => {
  await page.goto(path)
  const input = page.getByRole('textbox')
  const check = page.getByRole('checkbox')
  const submit = page.getByRole('button', { name: 'Confirmar identificação' })
  await expect(submit).toBeEnabled()
  await input.fill('🎉'.repeat(121))
  await check.check()
  await submit.click()
  await expect(input).toHaveAttribute('aria-invalid', 'true')
  await input.fill('🎉'.repeat(120))
  await expect(check).not.toBeChecked()
  await check.check()
  await submit.click()
  await expect(page.getByRole('heading', { name: 'Identificação confirmada' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('textbox')).toHaveValue('🎉'.repeat(120))
  await expect(page.getByRole('checkbox')).toBeChecked()
})

test('notice outage retries safely while preserving a valid draft', async ({ page }) => {
  let unavailable = true
  await page.route(noticePath, async route => {
    if (unavailable) await route.fulfill({ status: 503, contentType: 'application/problem+json', body: JSON.stringify({ code: 'CONSENT_NOTICE_UNAVAILABLE' }) })
    else await route.continue()
  })
  await page.goto(path)
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toBeVisible()
  await page.getByRole('textbox').fill(marker)
  await expect(page.getByRole('checkbox')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeDisabled()
  unavailable = false
  await page.getByRole('button', { name: 'Tentar novamente' }).click()
  await expect(page.getByText(data.notice.body, { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox')).toHaveValue(marker)
  await expect(page.getByRole('checkbox')).not.toBeChecked()
})

test('changing event discards the old draft and a delayed notice response', async ({ page }) => {
  let release: (() => void) | undefined
  const held = new Promise<void>(resolve => { release = resolve })
  await page.route(noticePath, async route => { await held; await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ code: 'old', body: 'OLD_NOTICE_MARKER' }) }).catch(() => {}) })
  await page.goto(path, { waitUntil: 'domcontentloaded' })
  await page.getByRole('textbox').fill(marker)
  await page.evaluate(code => { history.pushState(null, '', `/e/${code}/identification`); dispatchEvent(new PopStateEvent('popstate')) }, data.second.code)
  await expect(page.getByText(data.second.name, { exact: true })).toBeVisible()
  await expect(page.getByText(data.notice.body, { exact: true })).toBeVisible()
  release?.()
  await expect(page.getByRole('textbox')).toHaveValue('')
  await expect(page.getByRole('checkbox')).not.toBeChecked()
  await expect(page.getByText('OLD_NOTICE_MARKER')).toHaveCount(0)
})

test('invalid event never offers a form or a login redirect', async ({ page }) => {
  await page.goto('/e/bad/identification')
  await expect(page.getByText('Evento não encontrado. Confira o link recebido.')).toBeVisible()
  await expect(page.getByRole('checkbox')).toHaveCount(0)
  await expect(page).toHaveURL(/\/e\/bad\/identification$/)
})

test('markup stays text and guest identity never enters requests or navigation metadata', async ({ page }) => {
  const requests: { url: string; data: string | null; referer: string }[] = []
  page.on('request', request => requests.push({ url: request.url(), data: request.postData(), referer: request.headers()['referer'] ?? '' }))
  await page.route(noticePath, route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ code: 'markup', body: '<script>globalThis.__IC_XSS=1</script>' }) }))
  await page.goto(path)
  await expect(page.getByText('<script>globalThis.__IC_XSS=1</script>', { exact: true })).toBeVisible()
  await page.getByRole('textbox').fill('<b>' + marker + '</b>')
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Confirmar identificação' }).click()
  expect(await page.evaluate(() => Reflect.get(globalThis, '__IC_XSS'))).toBeUndefined()
  expect(JSON.stringify(requests)).not.toContain('IC_PRIVATE_GUEST')
  expect(await page.title()).not.toContain('IC_PRIVATE_GUEST')
  expect(await page.evaluate(() => JSON.stringify(history.state))).not.toContain('IC_PRIVATE_GUEST')
})

test('320px and 200% text keep form controls visible with keyboard focus', async ({ page, browserName }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 740 })
  await page.goto(path)
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.getByRole('textbox').focus()
  await page.keyboard.type('Ana')
  await page.keyboard.press(browserName === 'webkit' ? 'Alt+Tab' : 'Tab')
  await expect(page.getByRole('checkbox')).toBeFocused()
  await page.keyboard.press('Space')
  await page.keyboard.press(browserName === 'webkit' ? 'Alt+Tab' : 'Tab')
  await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeFocused()
  const color = await page.getByRole('textbox').evaluate(input => ({ ink: getComputedStyle(input).color, background: getComputedStyle(input).backgroundColor }))
  expect(color).toEqual({ ink: 'rgb(37, 40, 35)', background: 'rgb(255, 255, 255)' })
  await page.screenshot({ path: `artifacts/identification-${testInfo.project.name}-320-text200.png`, fullPage: true })
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Identificação confirmada' })).toBeFocused()
})

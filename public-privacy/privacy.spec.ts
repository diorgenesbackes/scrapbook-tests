import { expect, test, type Request } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { PNG } from 'pngjs'
import jsQR from 'jsqr'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'

type Event = { id: string, code: string, name: string, publicUrl: string }
const { events, markers } = JSON.parse(readFileSync(process.env.SCRAPBOOK_PRIVACY_MANIFEST!, 'utf8')) as { events: Event[], markers: string[] }
const first = events[0]!, other = events[2]!
const path = (event: Event) => `/api/v1/public/events/${event.code}`
function decode(bytes: Buffer) {
  const png = PNG.sync.read(bytes)
  const result = jsQR(Uint8ClampedArray.from(png.data), png.width, png.height)
  expect(result).not.toBeNull()
  return result!.data
}

test('guest traffic and DOM contain only the public projection, with no cookies or Referer', async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: 'scrapbook-session', value: 'PRIVATE_GUEST_TEXT', url: baseURL!, httpOnly: true }])
  const requests: Request[] = []
  page.on('request', request => { requests.push(request) })
  const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === path(first))
  await page.goto(`/e/${first.code}`)
  await expect(page.getByRole('heading', { name: first.name, exact: true })).toBeVisible()
  const response = await responsePromise
  const data = await response.json()
  expect(Object.keys(data).sort()).toEqual(['code', 'name', 'effectiveWelcomeMessage', 'cover', 'publicUrl', 'serverTime', 'submissionWindow'].sort())
  expect(Object.keys(data.cover).sort()).toEqual(['assetKey', 'kind'])
  expect(Object.keys(data.submissionWindow).sort()).toEqual(['closesAt', 'opensAt', 'revalidateAfterMs', 'state', 'timeZone'])
  const captured = await Promise.all(requests.map(async request => ({ url: request.url(), headers: await request.allHeaders() })))
  const calls = captured.filter(request => new URL(request.url).pathname.startsWith('/api/'))
  expect(calls.length).toBeGreaterThan(0)
  expect(calls.every(request => new URL(request.url).pathname === path(first))).toBe(true)
  expect(calls.every(request => !request.headers.cookie && !request.headers.authorization && !request.headers.referer)).toBe(true)
  expect(captured.every(request => !request.headers.referer)).toBe(true)
  const dom = await page.content()
  for (const marker of markers) { expect(dom).not.toContain(marker); expect(JSON.stringify(data)).not.toContain(marker) }
  expect(dom).not.toMatch(/firstRecord|accessVersion|recordCount/)
  await expect(page.locator('.guest-message strong')).toHaveCount(0)
  await expect(page.locator('.guest-message')).toContainText('<strong>como texto</strong>')
  await expect(page.getByRole('button', { name: 'Baixar PNG para compartilhar' })).toHaveCount(0)
})

test('same SPA leaves management and replaces all previous event state while loading another code', async ({ page }) => {
  await page.goto(`/events/${first.id}`)
  await expect(page.getByRole('heading', { name: first.name, exact: true })).toBeVisible()
  const privateCalls: string[] = []
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/events')) privateCalls.push(request.url()) })
  await page.evaluate(code => { history.pushState(null, '', `/e/${code}`); dispatchEvent(new PopStateEvent('popstate')) }, first.code)
  await expect(page.locator('.guest-shell')).toBeVisible()
  await expect(page.getByRole('heading', { name: first.name, exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Meus eventos' })).toHaveCount(0)
  await expect(page.locator('input[name], .event-print-card')).toHaveCount(0)
  await page.route(`**${path(other)}`, async route => { await new Promise(resolve => setTimeout(resolve, 300)); await route.continue() })
  await page.evaluate(code => { history.pushState(null, '', `/e/${code}`); dispatchEvent(new PopStateEvent('popstate')) }, other.code)
  await expect(page.getByRole('heading', { name: first.name, exact: true })).toHaveCount(0)
  await expect(page.locator(`a[href="${first.publicUrl}"]`)).toHaveCount(0)
  await expect(page.getByRole('heading', { name: other.name, exact: true })).toBeVisible()
  expect(privateCalls).toEqual([])
  for (const marker of markers) expect(await page.content()).not.toContain(marker)
  await page.screenshot({ path: `artifacts/public-privacy-navigation-${test.info().project.name}.png`, fullPage: true })
})

test('guest reload and second event do not persist responses in storage or caches', async ({ page }) => {
  for (const event of [first, other]) {
    await page.goto(`/e/${event.code}`)
    await expect(page.getByRole('heading', { name: event.name, exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: event.name, exact: true })).toBeVisible()
    const state = await page.evaluate(async () => ({
      local: { ...localStorage }, session: { ...sessionStorage }, cacheNames: await caches.keys(),
      workers: (await navigator.serviceWorker.getRegistrations()).length, databases: await indexedDB.databases(),
      history: history.state,
    }))
    expect(state.local).toEqual({}); expect(state.session).toEqual({})
    expect(state.cacheNames).toEqual([]); expect(state.workers).toBe(0); expect(state.databases).toEqual([])
    expect(JSON.stringify(state)).not.toContain(event.code)
    for (const marker of markers) expect(JSON.stringify(state)).not.toContain(marker)
  }
})

test('read redirects cannot reach private routes and document navigation suppresses Referer', async ({ page, baseURL }) => {
  const privateCalls: string[] = []
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/events')) privateCalls.push(request.url()) })
  // WebKit não permite route.fulfill(302): produzir resposta HTTP real em loopback.
  let redirectRequests = 0
  const server = createServer((_, response) => {
    redirectRequests++
    response.writeHead(302, { Location: new URL(`/api/v1/events/${first.id}`, baseURL).href, 'Access-Control-Allow-Origin': '*' })
    response.end()
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/redirect`
    await page.route(`**${path(first)}`, route => route.continue({ url }))
    await page.goto(`/e/${first.code}`)
    await expect(page.getByText(/Confira sua conexão/)).toBeVisible()
    expect(redirectRequests).toBeGreaterThan(0)
    expect(privateCalls).toEqual([])
    await page.unroute(`**${path(first)}`)
  } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())) }
  await page.getByRole('button', { name: 'Tentar novamente' }).click()
  await expect(page.getByRole('heading', { name: first.name, exact: true })).toBeVisible()
  const requestPromise = page.waitForRequest(request => new URL(request.url()).pathname === '/privacy-receiver')
  await page.evaluate(() => { const link = document.createElement('a'); link.href = '/privacy-receiver'; document.body.append(link); link.click() })
  const request = await requestPromise
  expect((await request.allHeaders()).referer).toBeUndefined()
})

test('QR, exported PNG filename and print card contain only the specified public presentation', async ({ page }) => {
  await page.goto(`/e/${first.code}`)
  const image = page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })
  await expect(image).toBeVisible()
  expect(decode(Buffer.from((await image.getAttribute('src'))!.split(',')[1]!, 'base64'))).toBe(first.publicUrl)
  await expect(page.locator('.event-print-card')).toHaveCount(0)
  await page.goto(`/events/${first.id}/share`)
  await expect(image).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Baixar PNG para compartilhar' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe(`scrapbook-evento-${first.code}.png`)
  const bytes = readFileSync((await download.path())!)
  expect(decode(bytes)).toBe(first.publicUrl)
  for (const marker of markers) expect(bytes.toString()).not.toContain(marker)
  await page.evaluate(() => { window.print = () => {} })
  await page.getByRole('button', { name: 'Imprimir QR code' }).click()
  await page.emulateMedia({ media: 'print' })
  const card = page.locator('.event-print-card')
  await expect(card).toBeVisible()
  expect(await card.textContent()).toBe(first.name + first.publicUrl)
  expect(decode(await card.screenshot())).toBe(first.publicUrl)
  for (const marker of markers) expect(await card.innerHTML()).not.toContain(marker)
})

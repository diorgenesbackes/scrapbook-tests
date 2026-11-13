import { expect, test, type Page } from '@playwright/test'
import { readFileSync, writeFileSync, renameSync } from 'node:fs'
import { execFileSync } from 'node:child_process'

const manifest = JSON.parse(readFileSync(process.env.SCRAPBOOK_WINDOW_MANIFEST!, 'utf8')) as {
  event: { id: string; code: string; name: string; startsAt: string }
  closedEvent: { code: string; name: string }
  clock: string; host: string; project: string; envFile: string; compose: string
}
if (!manifest.project.startsWith('scrapbook-docker-test-') || !new URL(manifest.host).hostname.match(/^(127\.0\.0\.1|localhost)$/)) {
  throw new Error('O controle temporal exige laboratório descartável loopback.')
}
const path = `/api/v1/public/events/${manifest.event.code}`
const setClock = (instant: string) => { writeFileSync(manifest.clock + '.next', instant, { mode: 0o600 }); renameSync(manifest.clock + '.next', manifest.clock) }
const refresh = (page: Page) => page.evaluate(() => { window.dispatchEvent(new Event('focus')) })
async function useClockHost(page: Page) {
  await page.clock.setFixedTime(new Date('2099-01-01T00:00:00Z'))
  await page.route(`**${path}`, async route => {
    const response = await route.fetch({ url: manifest.host + path })
    await route.fulfill({ response })
  })
}
const qr = (page: Page) => page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })

test('open page revalidates both boundaries from the server and keeps focus, presentation and QR', async ({ page }) => {
  setClock('2030-06-15T18:59:58.000Z')
  await useClockHost(page)
  await page.goto(`/e/${manifest.event.code}`)
  await expect(page.getByText('Os envios ainda não abriram.')).toBeVisible()
  await expect(page.getByText('15/06/2030 às 16:00')).toBeVisible()
  await expect(qr(page)).toBeVisible()
  await page.screenshot({ path: `artifacts/submission-window-before-${test.info().project.name}.png`, fullPage: true })
  setClock('2030-06-15T19:00:00.000Z')
  await expect(page.getByText('A janela de novos envios está aberta.')).toBeVisible()
  setClock('2030-06-16T14:59:58.000Z')
  const nearClosing = page.waitForResponse(response => new URL(response.url()).pathname === path && response.status() === 200)
  await refresh(page)
  expect((await (await nearClosing).json()).serverTime).toBe('2030-06-16T14:59:58.000Z')
  await expect(page.getByText('Acesso ao evento confirmado.')).toBeVisible()
  setClock('2030-06-16T15:00:00.000Z')
  await expect(page.getByText('Novos envios encerrados.')).toBeVisible()
  await expect(page.getByRole('heading', { name: manifest.event.name })).toBeFocused()
  expect(await page.locator('.guest-window').ariaSnapshot()).toContain('Novos envios encerrados.')
  await expect(qr(page)).toBeVisible()
  await expect(page.getByRole('link', { name: /Deixe um depoimento|Registre um momento/ })).toHaveCount(0)
  await page.screenshot({ path: `artifacts/submission-window-closed-${test.info().project.name}.png`, fullPage: true })
})

test('a revalidation error withdraws open availability and recovers at 13h without reopening', async ({ page }) => {
  setClock('2030-06-15T19:00:00.000Z'); await useClockHost(page)
  await page.goto(`/e/${manifest.event.code}`)
  await expect(page.getByText('A janela de novos envios está aberta.')).toBeVisible()
  await expect(qr(page)).toBeVisible()
  await page.route(`**${path}`, route => route.fulfill({ status: 503, contentType: 'application/problem+json', body: '{}' }), { times: 1 })
  await refresh(page)
  await expect(page.getByText('Não foi possível confirmar a disponibilidade de novos envios.')).toBeVisible()
  await expect(page.getByText('A janela de novos envios está aberta.')).toHaveCount(0)
  await expect(qr(page)).toBeVisible()
  await expect(page.getByRole('heading', { name: manifest.event.name })).toBeVisible()
  setClock('2030-06-16T16:00:00.000Z')
  await page.getByRole('button', { name: 'Tentar novamente' }).click()
  await expect(page.getByText('Novos envios encerrados.')).toBeVisible()
})

test('management date edits are reflected on focus and online revalidation', async ({ page, request }) => {
  setClock('2030-06-15T19:00:00.000Z'); await useClockHost(page)
  await page.goto(`/e/${manifest.event.code}`)
  await expect(page.getByText('A janela de novos envios está aberta.')).toBeVisible()
  const detail = `/api/v1/events/${manifest.event.id}`
  const current = await (await request.get(detail)).json()
  try {
    const changed = await request.put(detail, { data: { name: current.name, startsAt: '2030-07-01T18:00:00', welcomeMessage: current.welcomeMessage, expectedVersion: current.version } })
    expect(changed.status()).toBe(200)
    await page.evaluate(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online')) })
    await expect(page.getByText('Os envios ainda não abriram.')).toBeVisible()
    await expect(page.getByText('01/07/2030 às 16:00')).toBeVisible()
    await expect(qr(page)).toBeVisible()
  } finally {
    const latest = await (await request.get(detail)).json()
    const restored = await request.put(detail, { data: { name: current.name, startsAt: current.startsAt, welcomeMessage: current.welcomeMessage, expectedVersion: latest.version } })
    expect(restored.status()).toBe(200)
  }
})

test('an older server response cannot restore availability', async ({ page }) => {
  setClock('2030-06-15T19:00:00.000Z'); await useClockHost(page)
  await page.goto(`/e/${manifest.event.code}`)
  await expect(page.getByText('A janela de novos envios está aberta.')).toBeVisible()
  setClock('2030-06-15T18:00:00.000Z'); await refresh(page)
  await expect(page.getByText('Não foi possível confirmar a disponibilidade de novos envios.')).toBeVisible()
  await expect(page.getByText('Os envios ainda não abriram.')).toHaveCount(0)
  await expect(page.getByText('A janela de novos envios está aberta.')).toHaveCount(0)
  setClock('2030-06-16T15:00:00.000Z'); await refresh(page)
  await expect(page.getByText('Novos envios encerrados.')).toBeVisible()
})

test('real Compose service stop and restart preserve fixed deadlines and closed state', async ({ page, request }) => {
  const event = manifest.closedEvent
  const publicPath = `/api/v1/public/events/${event.code}`
  const before = await (await request.get(publicPath)).json()
  await page.goto(`/e/${event.code}`)
  await expect(page.getByText('Novos envios encerrados.')).toBeVisible()
  await expect(qr(page)).toBeVisible()
  const compose = (...args: string[]) => execFileSync('docker', ['compose', '--project-name', manifest.project, '--env-file', manifest.envFile, '-f', manifest.compose, ...args], { stdio: 'pipe', timeout: 120_000 })
  try {
    compose('stop', 'service'); await refresh(page)
    await expect(page.getByText('Não foi possível confirmar a disponibilidade de novos envios.')).toBeVisible()
    await expect(page.getByRole('heading', { name: event.name })).toBeVisible()
    await expect(qr(page)).toBeVisible()
  } finally { compose('up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'service') }
  await expect.poll(async () => (await request.get('/health')).status(), { timeout: 30_000 }).toBe(200)
  await page.getByRole('button', { name: 'Tentar novamente' }).click()
  await expect(page.getByText('Novos envios encerrados.')).toBeVisible()
  const after = await (await request.get(publicPath)).json()
  expect(after.submissionWindow).toEqual(before.submissionWindow)
})

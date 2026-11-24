import { expect, test } from '@playwright/test'
import { open } from 'node:fs/promises'

const code = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const file = process.env.SCRAPBOOK_TEST_CLOCK_FILE
if (!code || !/^[A-Za-z0-9_-]{43}$/.test(code) || !file) {
  throw new Error('Informe o código e o relógio da stack local descartável da página pública.')
}
const path = '/e/' + code
const qrName = 'QR code da página dos convidados'

async function setServerTime(value: string) {
  const handle = await open(file!, 'r+')
  try {
    await handle.write(new Date(value).toISOString(), 0, 'ascii')
    await handle.sync()
  } finally {
    await handle.close()
  }
}

if (process.env.SCRAPBOOK_TEST_PUBLIC_4G === 'true') {
  test('mede página pronta com cache frio em 4G', async ({ page }) => {
    await setServerTime('2032-02-29T01:59:58Z')
    const network = await page.context().newCDPSession(page)
    await network.send('Network.enable')
    await network.send('Network.setCacheDisabled', { cacheDisabled: true })
    await network.send('Network.emulateNetworkConditions', {
      offline: false, latency: 150, downloadThroughput: 200_000, uploadThroughput: 93_750, connectionType: 'cellular4g',
    })
    await page.goto(path, { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Celebração pública sintética' })).toBeVisible()
    await expect(page.getByText(/Os envios abrem em/)).toBeVisible()
    await expect(page.locator('.guest-cover')).toBeVisible()
    await expect.poll(() => page.locator('.guest-cover').evaluate((cover: HTMLImageElement) => cover.naturalWidth)).toBeGreaterThan(0)
    await expect(page.getByRole('img', { name: qrName })).toBeVisible()
    const readyMs = await page.evaluate(() => performance.now())
    console.log('Página e QR prontos em 4G com cache frio em ' + readyMs.toFixed(0) + ' ms.')
    expect(readyMs).toBeLessThanOrEqual(3_000)
  })
} else {
  test('CA1–CA3: abre sem sessão e atualiza a janela sem recarregar', async ({ page }) => {
    await setServerTime('2032-02-29T01:59:58Z')
    await page.clock.install({ time: new Date('2041-03-01T12:00:00Z') })
    await page.clock.pauseAt(new Date('2041-03-01T12:00:02Z'))
    const response = await page.goto(path + '?compartilhado=1')
    expect(response?.headers()['x-robots-tag']).toBe('noindex, nofollow')
    expect(response?.headers()['referrer-policy']).toBe('no-referrer')
    await expect(page.getByRole('heading', { name: 'Celebração pública sintética', level: 1 })).toBeVisible()
    await expect(page.getByText('Boas-vindas sintéticas.')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Meus eventos' })).toHaveCount(0)
    await expect(page.getByText(/28\/02\/2032 às 23:00/)).toBeVisible()
    await expect(page.getByRole('link', { name: 'Deixe um depoimento' })).toHaveCount(0)
    const qr = page.getByRole('img', { name: qrName })
    await expect(qr).toBeVisible()
    const matrix = await qr.locator('path').getAttribute('d')
    await expect(page.getByRole('region', { name: 'Compartilhar com outro convidado' }).getByRole('link'))
      .toHaveAttribute('href', new URL(path, page.url()).href)
    await page.getByRole('link', { name: 'Ir para o conteúdo' }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('main')).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

    await setServerTime('2032-02-29T02:00:00Z')
    await page.clock.runFor(4_100)
    await expect(page.getByRole('link', { name: 'Deixe um depoimento' })).toHaveAttribute('href', path + '/depoimento')
    await expect(page.getByRole('button', { name: 'Registre um momento' })).toBeDisabled()
    await expect(qr.locator('path')).toHaveAttribute('d', matrix!)

    await setServerTime('2032-03-01T14:59:58Z')
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
    await expect(page.getByRole('link', { name: 'Deixe um depoimento' })).toHaveAttribute('href', path + '/depoimento')
    await setServerTime('2032-03-01T15:00:00Z')
    await page.clock.runFor(2_100)
    await expect(page.getByText('O prazo para novos envios terminou.')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Deixe um depoimento' })).toHaveCount(0)
    await expect(qr.locator('path')).toHaveAttribute('d', matrix!)
  })

  test('CA4: mostra indisponibilidade e oferece recuperação depois de falha transitória', async ({ page }) => {
    await page.goto('/e/invalido')
    await expect(page.getByRole('heading', { name: 'Evento indisponível.' })).toBeVisible()
    await page.goto('/e/' + 'Q'.repeat(43))
    await expect(page.getByRole('heading', { name: 'Evento indisponível.' })).toBeVisible()
    await page.route('**/api/v1/public-events/**', route => route.fulfill({ status: 503, body: '' }), { times: 1 })
    await page.goto(path)
    await expect(page.getByRole('alert')).toContainText('Serviço temporariamente indisponível.')
    await page.getByRole('button', { name: 'Tentar novamente' }).click()
    await expect(page.getByRole('heading', { name: 'Celebração pública sintética' })).toBeVisible()
  })
}

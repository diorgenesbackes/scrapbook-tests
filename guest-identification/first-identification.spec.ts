import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

const firstCode = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const secondCode = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const codePattern = /^[A-Za-z0-9_-]{43}$/
if (!firstCode || !secondCode || !codePattern.test(firstCode) || !codePattern.test(secondCode) || firstCode === secondCode) {
  throw new Error('Informe dois códigos distintos da stack local descartável.')
}

const first = '/e/' + firstCode + '/identificacao'
const second = '/e/' + secondCode + '/identificacao'
const guestName = 'Convidada sintética da F06'

if (process.env.SCRAPBOOK_TEST_IDENTIFICATION_4G === 'true') {
  test('mede cinco aberturas a frio da S17 em 4G', async ({ browser, baseURL, browserName }) => {
    expect(browserName).toBe('chromium')
    const samples: number[] = []
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
        await page.goto(first, { waitUntil: 'domcontentloaded' })
        await expect(page.getByRole('textbox', { name: 'Nome ou apelido' })).toBeEditable()
        await expect(page.getByRole('checkbox', { name: 'Li o aviso de acesso.' })).toBeVisible()
        await expect(page.getByRole('button', { name: 'Continuar' })).toBeEnabled()
        samples.push(Math.round(await page.evaluate(() => performance.now())))
      } finally {
        await context.close()
      }
    }
    console.log('S17 em 4G, cache frio, amostras em ms: ' + samples.join(', '))
    expect(Math.max(...samples)).toBeLessThanOrEqual(3_000)
  })
} else {
  test('CA1–CA4: identifica por teclado e confirma somente após nome e aviso', async ({ page, browserName }, testInfo) => {
    const nextControl = browserName === 'webkit' ? 'Alt+Tab' : 'Tab'
    if (testInfo.project.name === 'chromium-desktop') await page.setViewportSize({ width: 1280, height: 800 })
    const response = await page.goto(first)
    expect(response?.headers()['referrer-policy']).toBe('no-referrer')
    expect(response?.headers()['x-robots-tag']).toBe('noindex, nofollow')
    await expect(page.getByRole('heading', { name: 'Identificação', level: 1 })).toBeVisible()
    await expect(page.getByText('Primeiro evento sintético F06')).toBeVisible()
    await expect(page.getByText(/Apenas os responsáveis pelo evento podem acessar as contribuições/)).toBeVisible()
    const name = page.getByRole('textbox', { name: 'Nome ou apelido' })
    const consent = page.getByRole('checkbox', { name: 'Li o aviso de acesso.' })
    const button = page.getByRole('button', { name: 'Continuar' })
    await expect(consent).not.toBeChecked()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

    await name.focus()
    await page.keyboard.press(nextControl)
    await expect(consent).toBeFocused()
    await page.keyboard.press(nextControl)
    await expect(button).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(name).toBeFocused()
    await expect(name).toHaveAccessibleDescription('Informe seu nome ou apelido.')
    await name.fill('  ')
    await button.press('Enter')
    await expect(name).toBeFocused()
    await name.fill('  ' + guestName + '  ')
    await button.press('Enter')
    await expect(consent).toBeFocused()
    await expect(consent).toHaveAccessibleDescription(/Confirme que você leu o aviso para continuar\./)
    await page.keyboard.press('Space')
    await expect(consent).toBeChecked()
    await page.keyboard.press(nextControl)
    await expect(button).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('status')).toHaveText('Identificação confirmada.')
    await expect(page.getByRole('status')).toBeFocused()
    await expect(page.getByRole('link', { name: 'Voltar ao evento' })).toHaveAttribute('href', '/e/' + firstCode)
  })

  test('CA1, CA4: mantém eventos separados e preserva a indisponibilidade da F05', async ({ page }) => {
    const requests: string[] = []
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.pathname.startsWith('/api/')) requests.push(`${request.method()} ${url.pathname}${url.search}`)
    })
    const diagnostics: string[] = []
    page.on('console', message => diagnostics.push(message.text()))
    await page.goto(first)
    const name = page.getByRole('textbox', { name: 'Nome ou apelido' })
    const consent = page.getByRole('checkbox')
    await name.fill(guestName)
    await consent.check()
    await page.getByRole('button', { name: 'Continuar' }).click()
    await expect(page.getByRole('status')).toHaveText('Identificação confirmada.')
    expect(new URL(page.url()).pathname).toBe(first)
    expect(new URL(page.url()).search).toBe('')

    await page.goto(second)
    await expect(page.getByText('Segundo evento sintético F06')).toBeVisible()
    await expect(name).toHaveValue('')
    await expect(consent).not.toBeChecked()
    expect(requests).toEqual([
      'GET /api/v1/public-events/' + firstCode,
      'GET /api/v1/public-events/' + secondCode,
    ])
    expect(diagnostics.join('\n')).not.toContain(guestName)
    expect(diagnostics.join('\n')).not.toContain('f06-local-v1')

    const unknownCode = 'Q'.repeat(43)
    await page.goto('/e/' + unknownCode + '/identificacao')
    await expect(page.getByRole('heading', { name: 'Evento indisponível.' })).toBeVisible()
    await expect(name).toHaveCount(0)
  })

  test('S17 a 320 px sem transbordamento e com controles acessíveis', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 740 })
    await page.goto(first)
    await expect(page.getByRole('textbox', { name: 'Nome ou apelido' })).toBeEditable()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    const scan = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()
    expect(scan.violations.map(({ id }) => id)).toEqual([])
  })
}

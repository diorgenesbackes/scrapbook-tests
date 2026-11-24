import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Browser, type Page } from '@playwright/test'

const firstCode = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const secondCode = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const codePattern = /^[A-Za-z0-9_-]{43}$/
if (!firstCode || !secondCode || !codePattern.test(firstCode) || !codePattern.test(secondCode) || firstCode === secondCode) {
  throw new Error('Informe dois códigos distintos da stack local descartável.')
}

const first = '/e/' + firstCode + '/identificacao'
const second = '/e/' + secondCode + '/identificacao'
const guestName = 'Convidada sintética da F06'
const updatedName = 'Convidada atualizada da F06'
const storageKey = 'scrapbook:guest-identification:v1:' + firstCode
const otherKey = 'scrapbook:guest-identification:v1:' + secondCode
const recovery = 'Informe seu nome e confirme o aviso para este evento.'

async function confirmName(page: Page, name: string) {
  await page.getByRole('textbox', { name: 'Nome ou apelido' }).fill(name)
  await page.getByRole('checkbox', { name: 'Li o aviso de acesso.' }).check()
  await page.getByRole('button', { name: 'Continuar' }).click()
  await expect(page.getByRole('status')).toContainText('Identificação confirmada.')
}

function blockStorage() {
  Object.defineProperty(window, 'localStorage', {
    get() { throw new DOMException('Armazenamento indisponível na fixture', 'SecurityError') },
  })
}

async function measureColdRuns(browser: Browser, baseURL: string | undefined, name: string, prepare?: (page: Page) => Promise<void>) {
  const samples: number[] = []
  for (let attempt = 0; attempt < 5; attempt++) {
    const context = await browser.newContext({ baseURL, viewport: { width: 393, height: 851 }, isMobile: true, hasTouch: true })
    try {
      const page = await context.newPage()
      await prepare?.(page)
      const network = await context.newCDPSession(page)
      await network.send('Network.enable')
      await network.send('Network.setCacheDisabled', { cacheDisabled: true })
      await network.send('Network.emulateNetworkConditions', {
        offline: false, latency: 150, downloadThroughput: 200_000, uploadThroughput: 93_750, connectionType: 'cellular4g',
      })
      await page.goto(first, { waitUntil: 'domcontentloaded' })
      if (name === 'retorno') {
        await expect(page.getByText(guestName, { exact: true })).toBeVisible()
        await expect(page.getByRole('button', { name: 'Editar nome' })).toBeEnabled()
      } else {
        await expect(page.getByRole('textbox', { name: 'Nome ou apelido' })).toBeEditable()
        await expect(page.getByRole('checkbox', { name: 'Li o aviso de acesso.' })).toBeVisible()
      }
      await expect(page.getByRole('button', { name: 'Continuar' })).toBeEnabled()
      samples.push(Math.round(await page.evaluate(() => performance.now())))
    } finally {
      await context.close()
    }
  }
  console.log(`S17 em 4G, ${name}, amostras em ms: ${samples.join(', ')}`)
  expect(Math.max(...samples)).toBeLessThanOrEqual(3_000)
}

if (process.env.SCRAPBOOK_TEST_IDENTIFICATION_4G === 'true') {
  test('mede cinco aberturas a frio da S17 em 4G', async ({ browser, baseURL, browserName }) => {
    expect(browserName).toBe('chromium')
    await measureColdRuns(browser, baseURL, 'primeira identificação')
  })

  test('mede cinco retornos à S17 em 4G', async ({ browser, page, baseURL }) => {
    await page.goto(first)
    await confirmName(page, guestName)
    const record = await page.evaluate(key => localStorage.getItem(key), storageKey)
    if (record === null) throw new Error('A confirmação da fixture deve persistir o registro.')
    await measureColdRuns(browser, baseURL, 'retorno', async measured => {
      await measured.addInitScript(({ key, value }) => localStorage.setItem(key, value), { key: storageKey, value: record })
    })
  })

  test('mede cinco aberturas da S17 com armazenamento indisponível em 4G', async ({ browser, baseURL }) => {
    await measureColdRuns(browser, baseURL, 'armazenamento indisponível', async page => {
      await page.addInitScript(blockStorage)
    })
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

  test('F06-STORY-002 CA1 e CA2: reutiliza e atualiza o nome nas duas categorias do evento', async ({ page }) => {
    await page.goto(first + '/depoimento')
    await expect(page.getByText('Prévia de depoimento')).toBeVisible()
    await confirmName(page, guestName)

    await page.goto(first + '/momento')
    await expect(page.getByText('Prévia de momento')).toBeVisible()
    await expect(page.getByText(guestName, { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Editar nome' }).click()
    const name = page.getByRole('textbox', { name: 'Nome ou apelido' })
    const consent = page.getByRole('checkbox')
    await expect(name).toBeFocused()
    await expect(name).toHaveValue(guestName)
    await expect(consent).not.toBeChecked()
    await name.fill(updatedName)
    await page.getByRole('button', { name: 'Continuar' }).click()
    await expect(consent).toBeFocused()
    await expect(consent).toHaveAccessibleDescription(/Confirme que você leu o aviso para continuar\./)
    await confirmName(page, updatedName)

    await page.goto(first + '/depoimento')
    await expect(page.getByText(updatedName, { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Continuar' }).click()
    await expect(page.getByRole('status')).toHaveText('Identificação confirmada.')
    await page.goto(second + '/depoimento')
    await expect(name).toHaveValue('')
    await expect(consent).not.toBeChecked()
  })

  test('F06-STORY-002 CA3: recupera identificação perdida com o rascunho intacto', async ({ page }) => {
    await page.goto(first + '/depoimento')
    await confirmName(page, guestName)
    await page.reload()
    await expect(page.getByText(guestName, { exact: true })).toBeVisible()
    const draft = page.getByRole('textbox', { name: 'Rascunho de teste' })
    await draft.fill('Rascunho sintético da F06')
    await page.evaluate(key => localStorage.removeItem(key), storageKey)
    await page.getByRole('button', { name: 'Continuar' }).click()
    await expect(page.getByRole('status')).toHaveText(recovery)
    await expect(page.getByRole('checkbox')).not.toBeChecked()
    await expect(draft).toHaveValue('Rascunho sintético da F06')
    await confirmName(page, updatedName)
    await expect(draft).toHaveValue('Rascunho sintético da F06')
  })

  test('F06-STORY-002 CA3: conclui na página quando o armazenamento está bloqueado', async ({ page }) => {
    await page.addInitScript(blockStorage)
    await page.goto(first + '/momento')
    await expect(page.getByRole('status')).toHaveText(recovery)
    const draft = page.getByRole('textbox', { name: 'Rascunho de teste' })
    await draft.fill('Rascunho sintético da F06')
    await confirmName(page, guestName)
    await expect(page.getByRole('status')).toHaveText('Identificação confirmada. Nome e concordância estão disponíveis apenas nesta página.')
    await expect(draft).toHaveValue('Rascunho sintético da F06')
    await page.reload()
    await expect(page.getByRole('status')).toHaveText(recovery)
    await expect(page.getByRole('textbox', { name: 'Nome ou apelido' })).toHaveValue('')
  })

  test('F06-STORY-002 CA4: recusa registros incompletos e vinculados a outro evento', async ({ page }) => {
    await page.goto(first)
    await confirmName(page, guestName)
    await page.evaluate(({ firstKey, secondKey }) => {
      const record = localStorage.getItem(firstKey)
      if (!record) throw new Error('A fixture deve ter uma identificação confirmada.')
      localStorage.setItem(secondKey, record)
      localStorage.setItem(firstKey, JSON.stringify({ ...JSON.parse(record), acceptedAt: null }))
    }, { firstKey: storageKey, secondKey: otherKey })
    for (const path of [second, first]) {
      await page.goto(path + '/momento')
      await expect(page.getByRole('status')).toHaveText(recovery)
      await expect(page.getByRole('textbox', { name: 'Nome ou apelido' })).toHaveValue('')
      await expect(page.getByRole('checkbox')).not.toBeChecked()
      await expect(page.getByText(guestName, { exact: true })).toHaveCount(0)
    }
  })
}

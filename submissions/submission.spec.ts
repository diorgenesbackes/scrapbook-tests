import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { open } from 'node:fs/promises'

const first = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const second = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const clock = process.env.SCRAPBOOK_TEST_CLOCK_FILE
const keyPattern = /^[A-Za-z0-9_-]{43}$/
if (!first || !second || first === second || !keyPattern.test(first) || !keyPattern.test(second) || !clock) {
  throw new Error('Informe o relógio e os eventos da stack local descartável.')
}

const path = '/e/' + first + '/depoimento'
const api = '/api/v1/public-events/' + first + '/submissions'
const opening = '2032-02-29T02:00:00.000Z'
const closing = '2032-03-01T15:00:00.000Z'
const text = 'Texto sintético do navegador F08'
const name = 'Nome sintético do navegador F08'

async function setTime(value: string) {
  const handle = await open(clock!, 'r+')
  try {
    await handle.write(value, 0, 'ascii')
    await handle.sync()
  } finally {
    await handle.close()
  }
}

function button(page: Page, name: string) {
  return page.getByRole('button', { name, exact: true })
}

async function prepare(page: Page) {
  await page.goto(path, { waitUntil: 'domcontentloaded' })
  await button(page, 'Escrever depoimento').click()
  await page.getByRole('textbox', { name: 'Seu depoimento' }).fill(text)
  await button(page, 'Revisar').press('Enter')
  await button(page, 'Confirmar identificação').press('Enter')
  await expect(page.getByText('Aviso sintético de acesso a contribuições.')).toBeVisible()
  await page.getByRole('textbox', { name: 'Nome ou apelido' }).fill(name)
  await page.getByRole('checkbox', { name: 'Li o aviso de acesso.' }).check()
  await button(page, 'Continuar').press('Enter')
  await expect(button(page, 'Enviar')).toBeEnabled()
}

test.beforeEach(async () => setTime(opening))

if (process.env.SCRAPBOOK_TEST_SUBMISSION_4G === 'true') {
  test('mede prontidão e recibo de texto em 4G', async ({ browser, baseURL }) => {
    test.setTimeout(90_000)
    const opens: number[] = []
    const confirmations: number[] = []
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
        await page.goto(path, { waitUntil: 'domcontentloaded' })
        await expect(button(page, 'Escrever depoimento')).toBeEnabled()
        opens.push(await page.evaluate(() => Math.round(performance.now())))
        await prepare(page)
        const start = await page.evaluate(() => performance.now())
        await button(page, 'Enviar').click()
        await expect(page.getByRole('heading', { name: 'Recebimento confirmado' })).toBeVisible()
        confirmations.push(await page.evaluate(start => Math.round(performance.now() - start), start))
      } finally {
        await context.close()
      }
    }
    console.log('Envio 4G', JSON.stringify({ opens, confirmations }))
    expect(Math.max(...opens)).toBeLessThanOrEqual(3000)
  })
} else {
  test('confirma o texto com ação explícita e entrega recibo sem conteúdo', async ({ page }) => {
    const writes: string[] = []
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === api) writes.push(request.method())
    })
    await prepare(page)
    expect(writes).toEqual([])
    await button(page, 'Enviar').press('Enter')
    const receipt = page.getByRole('heading', { name: 'Recebimento confirmado' })
    await expect(receipt).toBeFocused()
    await expect(page.getByText(text)).toHaveCount(0)
    await expect(page.getByText(name)).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Registrar um momento' })).toHaveAttribute('href', '/e/' + first + '/momento')
    expect(writes).toEqual(['POST'])
    const stored = await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]))
    expect(stored).not.toContain(text)
    expect(stored).not.toContain('consentProof')
    expect((await new AxeBuilder({ page }).include('.testimonial').analyze()).violations).toEqual([])
    await button(page, 'Deixar outro depoimento').click()
    await button(page, 'Escrever depoimento').click()
    await expect(page.getByRole('textbox', { name: 'Seu depoimento' })).toBeEmpty()
  })

  test('recupera recibo após perder a resposta e fechar a janela', async ({ page }) => {
    await prepare(page)
    let key: string | undefined
    let bearer: string | undefined
    let writes = 0
    let statusVerified = false
    await page.route(url => url.pathname === api || url.pathname.startsWith(api + '/'), async route => {
      const request = route.request()
      if (request.method() === 'POST') {
        writes += 1
        key = request.headers()['idempotency-key']
        bearer = request.headers().authorization
        const response = await route.fetch()
        expect(response.status()).toBe(201)
        await setTime(closing)
        await route.abort()
      } else {
        statusVerified = request.headers().authorization === bearer && new URL(request.url()).pathname === api + '/' + key
        await route.continue()
      }
    })
    await button(page, 'Enviar').click()
    await expect(page.getByRole('heading', { name: 'Recebimento confirmado' })).toBeVisible()
    await expect(button(page, 'Deixar outro depoimento')).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Registrar um momento' })).toHaveCount(0)
    expect(statusVerified).toBe(true)
    expect(writes).toBe(1)
  })

  test('renova a concordância vencida e protege o rascunho ao sair', async ({ page }) => {
    await setTime('2032-02-29T02:02:00.000Z')
    await prepare(page)
    await setTime('2032-02-29T02:33:00.000Z')
    await button(page, 'Enviar').click()
    await expect(page.getByRole('alert')).toContainText('Confirme o aviso para continuar.')
    await expect(page.getByRole('region', { name: 'Prévia do depoimento' })).toHaveText(text)
    await button(page, 'Confirmar identificação').click()
    await expect(page.getByText('Nome ou apelido registrado')).toBeVisible()
    await button(page, 'Continuar').click()
    await expect(button(page, 'Enviar')).toBeEnabled()
    const back = page.getByRole('link', { name: 'Voltar ao evento' })
    await back.press('Enter')
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('button', { name: 'Permanecer' })).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('region', { name: 'Prévia do depoimento' })).toHaveText(text)
    await button(page, 'Descartar').click()
    await dialog.getByRole('button', { name: 'Descartar rascunho' }).click()
    await button(page, 'Escrever depoimento').click()
    await expect(page.getByRole('textbox', { name: 'Seu depoimento' })).toBeEmpty()
    await page.goto('/e/' + second + '/depoimento')
    await button(page, 'Escrever depoimento').click()
    await expect(page.getByRole('textbox', { name: 'Seu depoimento' })).toBeEmpty()
  })
}

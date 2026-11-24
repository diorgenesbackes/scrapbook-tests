import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { open } from 'node:fs/promises'

const code = process.env.SCRAPBOOK_TEST_PUBLIC_CODE
const other = process.env.SCRAPBOOK_TEST_SECOND_PUBLIC_CODE
const clockFile = process.env.SCRAPBOOK_TEST_CLOCK_FILE
const pattern = /^[A-Za-z0-9_-]{43}$/
if (!code || !other || code === other || !pattern.test(code) || !pattern.test(other) || !clockFile) {
  throw new Error('Informe dois códigos e o relógio da stack local descartável.')
}

const event = '/e/' + code
const path = event + '/depoimento'
const opening = '2032-02-29T04:00:00Z'
const draft = 'Depoimento sintético da F07 <b>sem marcação</b>.'
const guestName = 'Nome sintético da F07'

async function setTime(value: string) {
  const handle = await open(clockFile!, 'r+')
  try {
    await handle.write(new Date(value).toISOString(), 0, 'ascii')
    await handle.sync()
  } finally {
    await handle.close()
  }
}

async function enterEditor(page: Page) {
  await page.getByRole('button', { name: 'Escrever depoimento' }).click()
  return page.getByRole('textbox', { name: 'Seu depoimento' })
}

test.beforeEach(async () => setTime(opening))

if (process.env.SCRAPBOOK_TEST_TESTIMONIAL_4G === 'true') {
  test('mede editor e revisão em cinco navegações com cache frio em 4G', async ({ browser, baseURL, browserName }) => {
    expect(browserName).toBe('chromium')
    const editor: number[] = []
    const review: number[] = []
    for (let sample = 0; sample < 5; sample++) {
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
        const field = await enterEditor(page)
        await expect(field).toBeEditable()
        editor.push(Math.round(await page.evaluate(() => performance.now())))
        await field.fill('😀'.repeat(5000))
        const start = await page.evaluate(() => performance.now())
        await page.getByRole('button', { name: 'Revisar', exact: true }).click()
        await expect(page.getByRole('region', { name: 'Prévia do depoimento' })).toBeVisible()
        review.push(Math.round(await page.evaluate(() => performance.now()) - start))
      } finally {
        await context.close()
      }
    }
    console.log('Editor em 4G, amostras em ms: ' + editor.join(', '))
    console.log('Revisão, amostras em ms: ' + review.join(', '))
    expect(Math.max(...editor)).toBeLessThanOrEqual(3_000)
  })
} else {
  test('CA1: usa o código da página e recusa novo rascunho fora da janela', async ({ page }) => {
    await setTime('2032-02-29T01:59:58Z')
    await page.goto(path)
    await expect(page.getByText('Novos envios ainda não estão disponíveis.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Escrever depoimento' })).toHaveCount(0)
    await setTime('2032-03-01T15:00:00Z')
    await page.reload()
    await expect(page.getByText('O prazo para novos envios terminou.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Escrever depoimento' })).toHaveCount(0)

    await setTime(opening)
    await page.goto(event)
    const link = page.getByRole('link', { name: 'Deixe um depoimento' })
    await expect(link).toHaveAttribute('href', path)
    const eventRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/public-events/' + code)
    await link.click()
    expect((await eventRead).status()).toBe(200)
    await expect(await enterEditor(page)).toBeEditable()
    await page.goto('/e/' + 'Q'.repeat(43) + '/depoimento')
    await expect(page.getByRole('heading', { name: 'Evento indisponível.' })).toBeVisible()
  })

  test('CA2 e CA4: aceita 5.000 escalares, preserva a edição e descarta mediante confirmação', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 740 })
    await page.goto(path)
    const field = await enterEditor(page)
    const review = page.getByRole('button', { name: 'Revisar', exact: true })
    await review.click()
    await expect(field).toBeFocused()
    await expect(page.getByRole('alert')).toHaveText('Escreva um depoimento de até 5.000 caracteres.')
    await field.fill('a'.repeat(4499))
    await expect(page.getByText(/caracteres restantes/)).toHaveCount(0)
    await field.fill('a'.repeat(4500))
    await expect(page.getByText('500 caracteres restantes.')).toBeVisible()

    const base = 'a'.repeat(4999)
    await field.fill(base + 'Z')
    await field.evaluate((element: HTMLTextAreaElement) => {
      element.setSelectionRange(4998, 4999)
      const data = new DataTransfer()
      data.setData('text/plain', '😀texto excedente')
      const paste = new ClipboardEvent('paste', { bubbles: true, cancelable: true })
      Object.defineProperty(paste, 'clipboardData', { value: data })
      element.dispatchEvent(paste)
    })
    const expected = 'a'.repeat(4998) + '😀Z'
    expect(await field.evaluate((element: HTMLTextAreaElement) => element.value.slice(-4))).toBe('a😀Z')
    await expect(field).toHaveValue(expected)
    await expect(page.getByText('0 caracteres restantes.')).toBeVisible()

    await review.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('heading', { name: 'Revisar depoimento' })).toBeFocused()
    await expect(page.getByRole('region', { name: 'Prévia do depoimento' })).toHaveText(expected)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()).violations.map(({ id }) => id)).toEqual([])
    await page.getByRole('button', { name: 'Editar texto' }).click()
    await expect(field).toHaveValue(expected)
    await field.fill('Texto corrigido')

    const back = page.getByRole('link', { name: 'Voltar ao evento' })
    await back.focus()
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Você pode perder o trabalho em andamento.' })
    await expect(dialog.getByRole('button', { name: 'Permanecer' })).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(back).toBeFocused()
    await expect(field).toHaveValue('Texto corrigido')
    await page.getByRole('button', { name: 'Descartar', exact: true }).click()
    await dialog.getByRole('button', { name: 'Descartar rascunho' }).click()
    await expect(await enterEditor(page)).toHaveValue('')
  })

  test('CA3 e CA5: mantém o texto durante identificação e aplica isolamento e privacidade', async ({ page }) => {
    const mutations: string[] = []
    const browserLogs: string[] = []
    page.on('request', request => { if (request.method() !== 'GET') mutations.push(request.method()) })
    page.on('console', message => browserLogs.push(message.text()))
    await page.goto(path)
    await (await enterEditor(page)).fill(draft)
    await page.getByRole('button', { name: 'Revisar', exact: true }).click()
    const preview = page.getByRole('region', { name: 'Prévia do depoimento' })
    await expect(preview).toHaveText(draft)
    await expect(preview.locator('b')).toHaveCount(0)
    await page.getByRole('button', { name: 'Confirmar identificação' }).click()
    await page.getByRole('textbox', { name: 'Nome ou apelido' }).fill(guestName)
    await page.getByRole('checkbox', { name: 'Li o aviso de acesso.' }).check()
    await page.getByRole('button', { name: 'Continuar', exact: true }).click()
    await expect(page.getByText('Identificação confirmada.')).toBeVisible()
    await expect(preview).toHaveText(draft)
    await expect(page.getByRole('button', { name: 'Enviar', exact: true })).toBeDisabled()
    expect(new URL(page.url()).pathname).toBe(path)
    expect(new URL(page.url()).search).toBe('')
    const remembered = await page.evaluate(() => ({ local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage) }))
    expect(remembered.local).toContain(guestName)
    expect(remembered.local).not.toContain('Depoimento sintético da F07')
    expect(remembered.session).not.toContain('Depoimento sintético da F07')
    expect(mutations).toEqual([])
    expect(browserLogs.join('\n')).not.toContain(guestName)
    expect(browserLogs.join('\n')).not.toContain(draft)

    await page.getByRole('link', { name: 'Voltar ao evento' }).click()
    await page.getByRole('button', { name: 'Descartar rascunho' }).click()
    await expect(page).toHaveURL(new RegExp(event + '$'))
    await page.goto('/e/' + other + '/depoimento')
    const newDraft = await enterEditor(page)
    await expect(newDraft).toHaveValue('')
    await newDraft.fill('Outro depoimento')
    await page.getByRole('button', { name: 'Revisar', exact: true }).click()
    await page.getByRole('button', { name: 'Confirmar identificação' }).click()
    await expect(page.getByRole('textbox', { name: 'Nome ou apelido' })).toHaveValue('')
    await expect(page.getByRole('checkbox', { name: 'Li o aviso de acesso.' })).not.toBeChecked()
  })

  test('CA5: preserva o texto quando a janela fecha e bloqueia a próxima etapa', async ({ page }) => {
    await setTime('2032-03-01T14:59:58Z')
    await page.clock.install({ time: new Date('2041-03-01T12:00:00Z') })
    await page.clock.pauseAt(new Date('2041-03-01T12:00:02Z'))
    await page.goto(path)
    const field = await enterEditor(page)
    await field.fill(draft)
    await setTime('2032-03-01T15:00:00Z')
    await page.clock.runFor(2_100)
    await expect(page.getByText('O prazo para novos envios terminou.')).toBeVisible()
    await expect(field).toHaveValue(draft)
    await page.getByRole('button', { name: 'Revisar', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Prévia do depoimento' })).toHaveText(draft)
    await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Enviar', exact: true })).toBeDisabled()
  })
}

import { expect, test } from '@playwright/test'
import { readFileSync, mkdirSync } from 'node:fs'
import type { Page } from '@playwright/test'

type Lab = { owner: string; lockedId: string; startsAt: string; openingDate: string }
const manifest = process.env.SCRAPBOOK_EVENTS_MANIFEST
const lab: Lab | null = manifest ? JSON.parse(readFileSync(manifest, 'utf8')) : null
test.skip(!lab, 'Executar por python3 journeys.py browser; requer laboratório descartável.')
const errors = new WeakMap<Page, string[]>()
test.beforeEach(({ page }) => { const found: string[] = []; errors.set(page, found); page.on('pageerror', error => found.push(error.message)) })
test.afterEach(({ page }) => { expect(errors.get(page)).toEqual([]) })
const empty = { items: [], nextCursor: null, hasMore: false, serverTime: '2026-11-13T01:00:00Z' }

async function createFields(page: Page, name: string) {
  await page.goto('/events/new')
  await page.getByLabel('Nome do evento', { exact: true }).fill(name)
  await page.getByLabel('Data e hora do evento (Brasília)', { exact: true }).fill(lab!.startsAt)
  await expect(page.getByLabel('Data de abertura (Brasília)', { exact: true })).toHaveValue(lab!.openingDate)
  await expect(page.getByLabel('Nome do evento', { exact: true })).toHaveValue(name)
}

test('cria, personaliza, restaura padrão e salva abertura separadamente', async ({ page }, info) => {
  const name = 'Journey ' + info.project.name + ' ' + Date.now()
  await createFields(page, name)
  await expect(page.getByRole('img', { name: 'Capa padrão do evento' })).toBeVisible()
  await page.getByLabel('Usar boas-vindas padrão').uncheck()
  await page.getByLabel('Mensagem de boas-vindas', { exact: true }).fill('Custom journey welcome')
  await expect(page.getByLabel('Nome do evento', { exact: true })).toHaveValue(name)
  await page.getByRole('button', { name: 'Criar evento', exact: true }).click()
  await expect(page.getByText('Evento criado. Salvamento confirmado.', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  const id = page.url().split('/').pop()!
  const stored = await (await page.request.get(lab!.owner + '/api/v1/events/' + id)).json()
  expect(stored.startsAt).toBe(lab!.startsAt)
  expect(stored.welcomeMessage).toBe('Custom journey welcome')
  expect(typeof stored.id).toBe('string')
  const information = page.getByRole('form', { name: 'Informações do evento', exact: true })
  await information.getByLabel('Nome do evento', { exact: true }).fill(name + ' edited')
  await information.getByLabel('Usar boas-vindas padrão').check()
  await information.getByRole('button', { name: 'Salvar informações' }).click()
  await expect(information.getByText('Salvamento confirmado.', { exact: true })).toBeVisible()
  const access = page.getByRole('form', { name: 'Configuração de abertura', exact: true })
  await access.getByLabel('Quando abrir as lembranças').selectOption('immediate')
  await access.getByRole('button', { name: 'Salvar abertura' }).click()
  await expect(access.getByText('Salvamento confirmado.', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: name + ' edited', exact: true })).toBeVisible()
  await expect(page.getByText('Seja bem-vindo! Compartilhe uma lembrança deste evento.', { exact: true }).first()).toBeVisible()
  await expect(access.getByLabel('Quando abrir as lembranças')).toHaveValue('immediate')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  mkdirSync('artifacts/screenshots', { recursive: true })
  await page.screenshot({ path: 'artifacts/screenshots/detail-' + info.project.name + '.png', fullPage: true })
  await page.getByRole('link', { name: '← Meus eventos' }).click()
  await expect(page.getByRole('heading', { name: 'Meus eventos', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Atualizar lista' }).click()
  await expect(page.getByRole('link').filter({ hasText: name + ' edited' })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('heading', { name: name + ' edited', exact: true })).toBeVisible()
  console.log(JSON.stringify({ browser: page.context().browser()?.version(), project: info.project.name, viewport: page.viewportSize(), timezone: await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone) }))
})

test('preserva rascunho e exige reconciliação explícita no conflito', async ({ page }, info) => {
  await createFields(page, 'Conflict ' + info.project.name + Date.now())
  await page.getByRole('button', { name: 'Criar evento', exact: true }).click()
  await expect(page.getByText('Evento criado. Salvamento confirmado.')).toBeVisible()
  const id = page.url().split('/').pop()!
  const path = '/api/v1/events/' + id
  const current = await (await page.request.get(lab!.owner + path)).json()
  await page.request.put(lab!.owner + path, { data: { name: 'Remote changed', startsAt: current.startsAt, welcomeMessage: null, expectedVersion: current.version } })
  const form = page.getByRole('form', { name: 'Informações do evento', exact: true })
  await form.getByLabel('Nome do evento', { exact: true }).fill('Preserved local draft')
  await form.getByRole('button', { name: 'Salvar informações' }).click()
  await expect(form.getByText(/O evento foi alterado/)).toBeVisible()
  await expect(form.getByRole('button', { name: 'Salvar informações' })).toBeDisabled()
  await form.getByRole('button', { name: 'Recarregar estado e comparar' }).click()
  await expect(page.getByRole('heading', { name: 'Remote changed' })).toBeVisible()
  await expect(form.getByLabel('Nome do evento', { exact: true })).toHaveValue('Preserved local draft')
  await expect(form.getByRole('button', { name: 'Salvar informações' })).toBeEnabled()
  await form.getByRole('button', { name: 'Salvar informações' }).click()
  await expect(page.getByRole('heading', { name: 'Preserved local draft' })).toBeVisible()
})

test('POST e PUT confirmados no banco com resposta perdida não são repetidos', async ({ page }, info) => {
  const name = 'Lost response ' + info.project.name + Date.now()
  await createFields(page, name)
  let posts = 0
  await page.route('**/api/v1/events', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    posts++
    const response = await route.fetch()
    expect(response.status()).toBe(201)
    await route.abort('connectionreset')
  })
  await page.getByRole('button', { name: 'Criar evento', exact: true }).click()
  await expect(page.getByText(/Ele pode ter sido concluído/)).toBeVisible()
  await expect(page.getByLabel('Nome do evento', { exact: true })).toHaveValue(name)
  await expect(page.getByRole('button', { name: 'Criar evento', exact: true })).toBeDisabled()
  expect(posts).toBe(1)
  const list = await (await page.request.get(lab!.owner + '/api/v1/events')).json()
  const created = list.items.filter((event: { name: string }) => event.name === name)
  // Controlled test fixture: this assertion is not a recovery heuristic in the product.
  expect(created).toHaveLength(1)
  await page.unroute('**/api/v1/events')
  await page.goto('/events/' + created[0].id)
  const form = page.getByRole('form', { name: 'Informações do evento', exact: true })
  await form.getByLabel('Nome do evento', { exact: true }).fill(name + ' saved')
  let puts = 0
  await page.route('**/api/v1/events/' + created[0].id, async route => {
    if (route.request().method() !== 'PUT') return route.continue()
    puts++
    expect((await route.fetch()).status()).toBe(200)
    await route.abort('connectionreset')
  })
  await form.getByRole('button', { name: 'Salvar informações' }).click()
  await expect(form.getByText(/Ele pode ter sido concluído/)).toBeVisible()
  await form.getByRole('button', { name: 'Recarregar estado e comparar' }).click()
  await expect(page.getByRole('heading', { name: name + ' saved', exact: true })).toBeVisible()
  await expect(form.getByLabel('Nome do evento', { exact: true })).toHaveValue(name + ' saved')
  expect(puts).toBe(1)
})

test('estrutura travada mantém abertura independente e não revela recebimentos', async ({ page }) => {
  await page.goto('/events/' + lab!.lockedId)
  await expect(page.getByRole('button', { name: 'Salvar informações' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Salvar abertura' })).toBeEnabled()
  await expect(page.getByText(/primeiro registro|convidado|quantidade de lembranças/i)).toHaveCount(0)
  const response = await (await page.request.get(lab!.owner + '/api/v1/events/' + lab!.lockedId)).json()
  expect(Object.keys(response)).not.toContain('firstRecordId')
  expect(Object.keys(response)).not.toContain('lockedAt')
  const rejected = await page.request.put(lab!.owner + '/api/v1/events/' + lab!.lockedId, {
    data: { name: 'Denied structural change', startsAt: response.startsAt, welcomeMessage: null, expectedVersion: response.version },
  })
  expect(rejected.status()).toBe(409)
  expect((await rejected.json()).code).toBe('EVENT_LOCKED')
  const access = page.getByRole('form', { name: 'Configuração de abertura', exact: true })
  const mode = access.getByLabel('Quando abrir as lembranças')
  if (await mode.inputValue() === 'immediate') {
    await mode.selectOption('scheduled')
    await access.getByLabel('Data de abertura (Brasília)', { exact: true }).fill(lab!.openingDate)
  } else {
    await mode.selectOption('immediate')
  }
  await access.getByRole('button', { name: 'Salvar abertura' }).click()
  await expect(access.getByText('Salvamento confirmado.', { exact: true })).toBeVisible()
})

test('distingue vazio, identidade ausente e indisponibilidade com um único retry GET', async ({ page }) => {
  await page.route('**/api/v1/events', route => route.fulfill({ json: empty }))
  await page.goto('/events')
  await expect(page.getByText('Você ainda não tem eventos vinculados. Crie o primeiro para começar.')).toBeVisible()
  await page.unroute('**/api/v1/events')
  let calls = 0
  await page.route('**/api/v1/events', route => { calls++; return route.fulfill({ status: 401, json: { code: 'IDENTITY_AUTHENTICATION_REQUIRED' } }) })
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('Identidade local indisponível')
  expect(calls).toBe(1)
  await expect(page.getByText('Você ainda não tem eventos vinculados. Crie o primeiro para começar.')).toHaveCount(0)
  await page.unroute('**/api/v1/events')
  calls = 0
  await page.route('**/api/v1/events', route => { calls++; return route.fulfill({ status: 503, body: '<html>unavailable</html>', contentType: 'text/html' }) })
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('Não foi possível consultar o servidor')
  expect(calls).toBe(2)
})

test('validação acessível, paginação e layout estreito com texto longo', async ({ page }, info) => {
  await page.goto('/events/new')
  await page.getByRole('button', { name: 'Criar evento', exact: true }).click()
  const name = page.getByLabel('Nome do evento', { exact: true })
  await expect(name).toHaveAttribute('aria-invalid', 'true')
  await expect(name).toBeFocused()
  await page.keyboard.type('😀'.repeat(120))
  await page.getByLabel('Usar boas-vindas padrão').uncheck()
  await page.getByLabel('Mensagem de boas-vindas', { exact: true }).fill('L'.repeat(2000))
  await page.setViewportSize({ width: 320, height: 740 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'artifacts/screenshots/narrow-' + info.project.name + '.png', fullPage: true })
  await page.goto('/events')
  await expect(page.getByRole('button', { name: 'Carregar mais eventos' })).toBeVisible()
  const before = await page.locator('.event-card').count()
  await page.getByRole('button', { name: 'Carregar mais eventos' }).click()
  await expect.poll(() => page.locator('.event-card').count()).toBeGreaterThan(before)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

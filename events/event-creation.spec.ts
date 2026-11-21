import { expect, test } from '@playwright/test'

test('cria um evento pelo teclado e recupera a resposta perdida sem duplicar', async ({ page }, info) => {
  const authorization = process.env.SCRAPBOOK_TEST_AUTHORIZATION
  if (!process.env.PLAYWRIGHT_BASE_URL || !authorization || !/^Development [a-f0-9]{64}$/.test(authorization)) {
    throw new Error('Execute na stack de eventos.')
  }
  await page.setExtraHTTPHeaders({ Authorization: authorization })
  await page.goto('/meus-eventos')
  await expect(page.getByRole('button', { name: 'Próxima página' })).toBeVisible()
  await page.getByRole('button', { name: 'Próxima página' }).click()
  await expect(page.getByRole('link', { name: 'Celebração sintética', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Criar evento', exact: true }).focus()
  await page.keyboard.press('Enter')
  const name = 'Celebração ' + info.project.name
  await page.getByLabel('Nome do evento').fill(name)
  await page.getByLabel('Data e horário do evento (Brasília)').fill('2032-02-29T01:30')
  await expect(page.getByLabel('Data de abertura das lembranças')).toHaveValue('2033-02-28')
  await page.getByLabel('Data de abertura das lembranças').fill('2033-03-02')

  const keys: string[] = []
  await page.route('**/api/v1/events', async route => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    keys.push(route.request().headers()['idempotency-key'] ?? '')
    if (keys.length === 1) {
      const response = await route.fetch()
      expect(response.status()).toBe(201)
      await route.abort()
      return
    }
    await route.continue()
  })

  await page.getByRole('button', { name: 'Criar evento' }).click()
  await expect(page.getByText('A confirmação ainda está pendente.')).toBeVisible()
  const replay = page.waitForResponse(response =>
    response.url().endsWith('/api/v1/events') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Recuperar tentativa' }).click()
  expect((await replay).status()).toBe(200)
  expect(keys[0]).toMatch(/^[a-f0-9-]{36}$/)
  expect(keys[1]).toBe(keys[0])
  await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible()
  await expect(page.getByText('Seja bem-vindo! Compartilhe uma lembrança deste evento.')).toBeVisible()
  await expect(page.getByText('02/03/2033', { exact: true })).toBeVisible()
})

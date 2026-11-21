import { expect, test } from '@playwright/test'
import { randomUUID } from 'node:crypto'

test.beforeEach(async ({ page }) => {
  const authorization = process.env.SCRAPBOOK_TEST_AUTHORIZATION
  if (!process.env.PLAYWRIGHT_BASE_URL || !authorization || !/^Development [a-f0-9]{64}$/.test(authorization)) {
    throw new Error('Execute na stack de eventos.')
  }
  await page.setExtraHTTPHeaders({ Authorization: authorization })
})

test('cria em imediato e exige confirmação para a mudança definitiva após o prazo', async ({ page }, info) => {
  const name = 'Abertura definitiva ' + info.project.name
  await page.goto('/meus-eventos/novo')
  await page.getByLabel('Nome do evento').fill(name)
  await page.getByLabel('Data e horário do evento (Brasília)').fill('2020-02-29T01:30')
  await page.getByLabel('Modo de abertura').selectOption('immediate')
  await expect(page.getByLabel('Data de abertura das lembranças')).toHaveCount(0)
  await page.getByRole('button', { name: 'Criar evento' }).click()
  await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible()
  await expect(page.getByText('Você pode fazer uma última mudança', { exact: false })).toBeVisible()

  await page.getByLabel('Modo de abertura').selectOption('scheduled')
  await page.getByLabel('Data de abertura das lembranças').fill('2033-02-28')
  await page.getByRole('button', { name: 'Salvar abertura' }).click()
  const dialog = page.getByRole('dialog', { name: 'Confirmar mudança de abertura' })
  await expect(dialog.getByText('Após salvar, a data será definitiva', { exact: false })).toBeVisible()
  await dialog.getByRole('button', { name: 'Voltar' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByText('Política atual: Imediata')).toBeVisible()
  await page.getByRole('button', { name: 'Salvar abertura' }).click()
  await dialog.getByRole('button', { name: 'Confirmar mudança' }).click()
  await expect(page.getByText('A data de abertura deste evento está travada.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Salvar abertura' })).toBeDisabled()
})

test('consulta a política atual no conflito e mantém o rascunho até outro envio', async ({ page }, info) => {
  const headers = {
    Authorization: process.env.SCRAPBOOK_TEST_AUTHORIZATION!,
    Origin: process.env.PLAYWRIGHT_BASE_URL!,
  }
  const response = await page.request.post('/api/v1/events', {
    headers: { ...headers, 'Idempotency-Key': randomUUID() },
    data: { name: 'Conflito ' + info.project.name, startsAt: '2032-02-29T01:30:00',
      openingPolicy: { mode: 'immediate', opensOn: null } },
  })
  expect(response.status()).toBe(201)
  const event = await response.json()
  await page.goto('/meus-eventos/' + event.id)
  await expect(page.getByText('Política atual: Imediata')).toBeVisible()
  await page.getByLabel('Modo de abertura').selectOption('scheduled')
  await page.getByLabel('Data de abertura das lembranças').fill('2033-03-02')
  const otherSession = await page.request.put('/api/v1/events/' + event.id + '/opening-policy', {
    headers, data: { mode: 'scheduled', opensOn: '2033-02-28', expectedVersion: event.version },
  })
  expect(otherSession.status()).toBe(200)

  await page.getByRole('button', { name: 'Salvar abertura' }).click()
  await page.getByRole('button', { name: 'Confirmar mudança' }).click()
  await expect(page.getByRole('alert')).toContainText('O evento mudou em outra sessão.')
  await expect(page.getByText('Política atual: 28/02/2033')).toBeVisible()
  await expect(page.getByLabel('Data de abertura das lembranças')).toHaveValue('2033-03-02')
  await page.getByRole('button', { name: 'Salvar abertura' }).click()
  await expect(page.getByText('Configuração de abertura salva.')).toBeVisible()
  await expect(page.getByText('Política atual: 02/03/2033')).toBeVisible()
})

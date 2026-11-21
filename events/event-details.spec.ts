import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  const authorization = process.env.SCRAPBOOK_TEST_AUTHORIZATION
  if (!process.env.PLAYWRIGHT_BASE_URL || !authorization || !/^Development [a-f0-9]{64}$/.test(authorization)) {
    throw new Error('Execute na stack local de eventos.')
  }
  await page.setExtraHTTPHeaders({ Authorization: authorization })
})

test('cria com boas-vindas próprias e mantém o rascunho após conflito em outra sessão', async ({ page }, info) => {
  await page.goto('/meus-eventos/novo')
  const originalName = 'Informações ' + info.project.name
  await page.getByLabel('Nome do evento').fill(originalName)
  await page.getByLabel('Data e horário do evento (Brasília)').fill('2032-02-29T01:30')
  await page.getByLabel('Mensagem de boas-vindas', { exact: true }).selectOption('custom')
  await page.getByLabel('Sua mensagem de boas-vindas').fill('Bem-vindos à nossa festa!')
  await page.getByRole('button', { name: 'Criar evento' }).click()
  await expect(page.getByRole('heading', { name: originalName, level: 1 })).toBeVisible()
  await expect(page.getByRole('paragraph').filter({ hasText: 'Bem-vindos à nossa festa!' })).toBeVisible()
  await expect(page.getByLabel('Sua mensagem de boas-vindas')).toHaveValue('Bem-vindos à nossa festa!')

  const editable = page.getByRole('group', { name: 'Editar informações' })
  const name = editable.getByLabel('Nome do evento')
  await name.fill('Meu rascunho ' + info.project.name)
  await editable.getByLabel('Data e horário do evento (Brasília)').fill('2032-03-04T01:30')
  await editable.getByLabel('Mensagem de boas-vindas', { exact: true }).selectOption('default')
  const id = new URL(page.url()).pathname.split('/').at(-1)
  const otherSession = await page.request.put('/api/v1/events/' + id + '/details', {
    headers: { Authorization: process.env.SCRAPBOOK_TEST_AUTHORIZATION!, Origin: process.env.PLAYWRIGHT_BASE_URL! },
    data: { name: 'Alterado em outra sessão', startsAt: '2032-02-29T01:30:00', welcomeMessage: 'Outra mensagem',
      expectedVersion: '0' },
  })
  expect(otherSession.status()).toBe(200)

  const save = page.getByRole('button', { name: 'Salvar informações' })
  await save.click()
  await expect(page.getByRole('alert')).toContainText('O evento mudou em outra sessão.')
  await expect(page.getByRole('heading', { name: 'Alterado em outra sessão', level: 1 })).toBeVisible()
  await expect(name).toHaveValue('Meu rascunho ' + info.project.name)
  await expect(editable.getByLabel('Data e horário do evento (Brasília)')).toHaveValue('2032-03-04T01:30')
  await expect(editable.getByLabel('Mensagem de boas-vindas', { exact: true })).toHaveValue('default')
  await save.click()
  await expect(page.getByText('Informações salvas.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Meu rascunho ' + info.project.name, level: 1 })).toBeVisible()
  await expect(page.getByText('Seja bem-vindo! Compartilhe uma lembrança deste evento.')).toBeVisible()
  await expect(page.getByText(/03\/03\/2032.*23:30/)).toBeVisible()
  await expect(page.getByText(/05\/03\/2032.*12:00/)).toBeVisible()
  await expect(page.getByText(/05\/03\/2032.*13:00/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Salvar abertura' })).toBeEnabled()

  await editable.getByLabel('Mensagem de boas-vindas', { exact: true }).selectOption('custom')
  await editable.getByLabel('Sua mensagem de boas-vindas').fill('Mensagem atualizada')
  await save.click()
  await expect(page.getByRole('paragraph').filter({ hasText: 'Mensagem atualizada' })).toBeVisible()
})

test('mostra a trava estrutural sem esconder a configuração de abertura', async ({ page }) => {
  const id = process.env.SCRAPBOOK_TEST_LOCKED_EVENT_ID ?? ''
  expect(id).toMatch(/^[1-9][0-9]*$/)
  await page.goto('/meus-eventos/' + id)
  await expect(page.getByText('As informações deste evento estão travadas.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Salvar informações' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Salvar abertura' })).toBeEnabled()
})

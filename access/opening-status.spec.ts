import { expect, test } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { open } from 'node:fs/promises'

test('usa a data do servidor, libera na borda e volta a fechar após alteração', async ({ page }, info) => {
  const origin = process.env.PLAYWRIGHT_BASE_URL
  const authorization = process.env.SCRAPBOOK_TEST_AUTHORIZATION
  const file = process.env.SCRAPBOOK_TEST_CLOCK_FILE
  if (!origin || !authorization || !/^Development [a-f0-9]{64}$/.test(authorization) || !file) {
    throw new Error('Execute com o runner de abertura em loopback.')
  }

  async function setServerTime(value: string) {
    const handle = await open(file!, 'r+')
    try {
      await handle.write(new Date(value).toISOString(), 0, 'ascii')
      await handle.sync()
    } finally {
      await handle.close()
    }
  }

  const headers = { Authorization: authorization, Origin: origin }
  await setServerTime('2028-01-02T02:59:58Z')
  await page.setExtraHTTPHeaders({ Authorization: authorization })
  await page.clock.install({ time: new Date('2041-03-01T12:00:00Z') })
  await page.clock.pauseAt(new Date('2041-03-01T12:01:00Z'))

  const scheduledName = 'Abertura sintética ' + info.project.name
  const scheduled = await page.request.post('/api/v1/events', {
    headers: { ...headers, 'Idempotency-Key': randomUUID() },
    data: { name: scheduledName, startsAt: '2027-12-28T15:00:00',
      openingPolicy: { mode: 'scheduled', opensOn: '2028-01-02' } },
  })
  expect(scheduled.status()).toBe(201)
  const { id } = await scheduled.json()
  await page.goto('/meus-eventos/' + id)
  await expect(page.getByRole('heading', { name: scheduledName, level: 1 })).toBeVisible()
  await page.getByRole('link', { name: 'Ver lembranças' }).focus()
  await page.keyboard.press('Enter')
  const timer = page.getByRole('timer', { name: 'Tempo até a abertura' })
  await expect(timer).toHaveText('0 dias, 0 horas, 1 minuto')
  await expect(page.locator('time')).toHaveText('02/01/2028')
  await expect(page.getByText(scheduledName)).toHaveCount(0)
  await expect(page.getByRole('img')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await setServerTime('2028-01-02T03:00:00Z')
  await page.clock.runFor(2_010)
  await expect(page.getByText('As lembranças estão abertas.')).toBeVisible()
  await expect(timer).toHaveCount(0)

  const immediate = await page.request.post('/api/v1/events', {
    headers: { ...headers, 'Idempotency-Key': randomUUID() },
    data: { name: 'Evento imediato', startsAt: '2032-02-29T15:00:00',
      openingPolicy: { mode: 'immediate', opensOn: null } },
  })
  expect(immediate.status()).toBe(201)
  const event = await immediate.json()
  await page.goto('/meus-eventos/' + event.id + '/lembrancas')
  await expect(page.getByText('As lembranças estão abertas.')).toBeVisible()

  const changed = await page.request.put('/api/v1/events/' + event.id + '/opening-policy', {
    headers, data: { mode: 'scheduled', opensOn: '2033-03-02', expectedVersion: event.version },
  })
  expect(changed.status()).toBe(200)
  await page.clock.runFor(15_010)
  await expect(page.getByRole('heading', { name: 'Lembranças guardadas' })).toBeVisible()
  await expect(page.locator('time')).toHaveText('02/03/2033')
  await expect(timer).toBeVisible()
  await expect(page.getByText('As lembranças estão abertas.')).toHaveCount(0)
})

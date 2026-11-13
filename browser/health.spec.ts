import { expect, test } from '@playwright/test'

test('consulta a saúde do backend pelo frontend em loopback', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Estado do backend' })).toBeVisible()
  await page.getByRole('button', { name: 'Verificar backend' }).click()
  await expect(page.getByText('Backend disponível.', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test('oferece estrutura semântica, reflow e foco por teclado', async ({ page, browser, browserName }) => {
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('lang', 'pt-BR')
  await expect(page.getByRole('main')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Bem-vindo ao Scrap Book')
  const widths = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    inner: window.innerWidth,
  }))
  expect(widths.scroll).toBeLessThanOrEqual(widths.inner)

  await page.keyboard.press(browserName === 'webkit' ? 'Alt+Tab' : 'Tab')
  const skipLink = page.getByRole('link', { name: 'Ir para o conteúdo' })
  await expect(skipLink).toBeFocused()
  await skipLink.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()

  const axe = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(axe.violations).toEqual([])
  console.info(`${test.info().project.name} · navegador ${browser.version()}`)

  await page.goto('/rota-ausente')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Página indisponível')
  await expect(page.getByRole('link', { name: 'Voltar ao início' })).toHaveAttribute('href', '/')
})

test('informa indisponibilidade e recupera ao repetir por teclado', async ({ page }) => {
  await page.route('**/health/ready', route => route.fulfill({ status: 503, body: 'proxy indisponível' }), { times: 1 })
  await page.goto('/')
  const button = page.getByRole('button', { name: 'Verificar disponibilidade' })
  await button.focus()
  await button.press('Enter')
  await expect(page.getByRole('status')).toContainText('temporariamente indisponível')
  const retry = page.getByRole('button', { name: 'Tentar novamente' })
  await expect(retry).toBeFocused()
  await retry.press('Enter')
  await expect(page.getByRole('status')).toHaveText('Ambiente disponível.')
})

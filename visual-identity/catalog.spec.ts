import { expect, test } from '@playwright/test'
import { readFileSync, mkdirSync } from 'node:fs'
import type { Page } from '@playwright/test'

type Screen = { id: string; name: string; requirements: string; states: string[] }
const screens: Screen[] = JSON.parse(readFileSync('../scrapbook-docs/visual-identity/screens.json', 'utf8'))
const failures = new WeakMap<Page, string[]>()
test.beforeEach(({ page }) => {
  const found: string[] = []
  failures.set(page, found)
  page.on('pageerror', error => found.push(error.message))
  page.on('response', response => { if (response.status() >= 400) found.push(String(response.status()) + ' ' + new URL(response.url()).pathname) })
})
test.afterEach(({ page }) => expect(failures.get(page)).toEqual([]))

test('inventário cobre todos os requisitos e especificações individuais', async ({ request }) => {
  expect(screens).toHaveLength(38)
  expect(new Set(screens.map(screen => screen.id)).size).toBe(screens.length)
  for (let index = 1; index <= 22; index++) expect(screens.some(screen => screen.requirements.includes('FR-' + String(index).padStart(3, '0')))).toBe(true)
  for (const screen of screens) expect((await request.get('/screens/' + screen.id + '.md')).status()).toBe(200)
  expect((await request.get('/tokens.css')).status()).toBe(200)
})

for (const width of [320, 1440]) {
  test(`todas as telas e estados renderizam sem overflow em ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: /O que foi vivido/ })).toBeVisible()
    let states = 0
    for (const screen of screens) {
      for (const state of screen.states) {
        await page.goto('/#' + screen.id + '?state=' + state)
        await expect(page.locator('.screen-tools h1')).toHaveText(screen.name)
        await expect(page.getByLabel('Estado demonstrado')).toHaveValue(state)
        await expect(page.locator('#screen-preview')).toBeVisible()
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        if (await page.locator('#screen-preview img').count()) await page.locator('#screen-preview img').last().scrollIntoViewIfNeeded()
        await expect.poll(() => page.locator('#screen-preview img').evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true)
        states++
      }
    }
    expect(states).toBe(178)
    console.log(JSON.stringify({ browser: info.project.name, width, screens: screens.length, states }))
  })
}

test('foco, abas, diálogo e skip link funcionam por teclado', async ({ page }) => {
  await page.goto('/#components')
  const first = page.getByRole('tab', { name: 'Tudo', exact: true })
  await first.focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Depoimentos', exact: true })).toBeFocused()
  await expect(page.getByRole('tab', { name: 'Depoimentos', exact: true })).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('End')
  await expect(page.getByRole('tab', { name: 'Momentos', exact: true })).toBeFocused()
  const trigger = page.getByRole('button', { name: 'Abrir diálogo de descarte' })
  await trigger.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Permanecer', exact: true })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('button', { name: 'Sair e descartar', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(trigger).toBeFocused()
  await trigger.focus()
  expect(await trigger.evaluate(element => getComputedStyle(element).outlineStyle)).toBe('solid')
  await page.goto('/#S16')
  await page.locator('.skip').focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('#main')).toBeFocused()
  expect(new URL(page.url()).hash).toBe('#S16')
})

test('bloqueio e recibo não montam mídia ou conteúdo privado', async ({ page }) => {
  for (const route of ['S28', 'S29?state=blocked', 'S30?state=blocked', 'S26?state=success', 'S27']) {
    await page.goto('/#' + route)
    const preview = page.locator('#screen-preview')
    await expect(preview).toBeVisible()
    await expect(preview.locator('img:not(.brand img), video, audio, [autoplay]')).toHaveCount(0)
    await expect(preview).not.toContainText('Rafa')
    await expect(preview).not.toContainText('registros recebidos')
    await expect(preview.getByRole('button', { name: /Excluir|Download/ })).toHaveCount(0)
  }
  await page.goto('/#S09?state=expired')
  await expect(page.locator('#screen-preview')).not.toContainText('Festa no jardim')
})

test('concordância e contadores preservam regras do convidado', async ({ page }) => {
  await page.goto('/#S17?state=returning')
  const agreement = page.getByRole('checkbox', { name: /Concordo com o aviso/ })
  await expect(agreement).toBeChecked()
  await page.getByLabel('Nome ou apelido', { exact: true }).fill('Outro nome')
  await expect(agreement).not.toBeChecked()
  for (const [id, limit, warning] of [['S23', 5000, 500], ['S24', 500, 100]] as const) {
    await page.goto('/#' + id + '?state=near-limit')
    const input = page.locator('#demo-message')
    await expect(page.locator('#character-count')).toHaveText(warning + ' caracteres restantes')
    await input.fill('😀'.repeat(limit + 2))
    expect(Array.from(await input.inputValue()).length).toBe(limit)
    await expect(page.locator('#character-count')).toHaveText('0 caracteres restantes')
    await input.fill('Um texto curto')
    await expect(page.locator('#character-count')).not.toBeVisible()
  }
})

test('fundamentos, componentes e telas representativas em quatro larguras', async ({ page }, info) => {
  mkdirSync('artifacts/visual-identity', { recursive: true })
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    for (const route of ['overview', 'foundations', 'components', 'S09', 'S10', 'S11', 'S16', 'S26?state=progress', 'S28', 'S29', 'decisions']) {
      await page.goto('/#' + route)
      await expect(page.locator('#main h1')).toBeVisible()
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      if (route === 'overview') {
        const ratio = await page.locator('.hero-photo').evaluate(image => image.getBoundingClientRect().width / image.getBoundingClientRect().height)
        expect(ratio).toBeCloseTo(width < 768 ? 4 / 3 : 2, 1)
      }
      if (route === 'S29') {
        const ratio = await page.locator('#screen-preview .sb-photo').first().evaluate(image => image.getBoundingClientRect().width / image.getBoundingClientRect().height)
        expect(ratio).toBeCloseTo(4 / 3, 1)
      }
    }
    if ([390, 1440].includes(width)) {
      for (const route of ['overview', 'S16', 'S29']) {
        await page.goto('/#' + route)
        await page.locator('img').evaluateAll(images => images.forEach(image => { (image as HTMLImageElement).loading = 'eager' }))
        await expect.poll(() => page.locator('img').evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true)
        await page.screenshot({ path: `artifacts/visual-identity/${info.project.name}-${route}-${width}.png`, fullPage: true })
      }
    }
  }
  await page.goto('/#components')
  await page.setViewportSize({ width: 640, height: 900 })
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe('auto')
})

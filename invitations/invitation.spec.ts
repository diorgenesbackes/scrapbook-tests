import { expect, test, type Locator } from '@playwright/test'
import jsQR from 'jsqr'

async function decodeQr(qr: Locator) {
  const pixels = await qr.evaluate(async element => {
    const svg = new XMLSerializer().serializeToString(element)
    const image = new Image()
    image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 384
    const context = canvas.getContext('2d')!
    context.imageSmoothingEnabled = false
    context.drawImage(image, 0, 0, 384, 384)
    return Array.from(context.getImageData(0, 0, 384, 384).data)
  })
  return jsQR(new Uint8ClampedArray(pixels), 384, 384)?.data
}

test('emite e substitui por teclado e abre o QR sem consumir o convite', async ({ page, browser }, info) => {
  const authorization = process.env.SCRAPBOOK_TEST_AUTHORIZATION
  const events = JSON.parse(process.env.SCRAPBOOK_TEST_INVITATION_EVENTS ?? '{}')
  const eventId = events[info.project.name]
  if (!authorization || !eventId) throw new Error('Use o runner da stack local de convites.')

  await page.route('**/api/v1/events/**', route => route.continue({
    headers: { ...route.request().headers(), authorization },
  }))
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: {
      writeText: async () => { throw new Error('Clipboard indisponível na fixture') },
    } })
  })
  await page.goto('/meus-eventos/' + eventId)
  await page.getByRole('link', { name: 'Convidar responsável' }).press('Enter')
  await page.getByRole('button', { name: 'Gerar convite' }).press('Enter')
  const field = page.getByRole('textbox', { name: 'Link do convite' })
  const qr = page.getByRole('img', { name: 'QR code do convite de responsável' })
  await expect(qr).toBeVisible()
  const first = await field.inputValue()
  const address = new URL(first)
  expect(address.origin).toBe(new URL(page.url()).origin)
  expect(address.pathname).toBe('/convites/aceitar')
  expect(address.search).toBe('')
  expect(address.hash).toMatch(/^#v1\.fixture-hash\.[A-Za-z0-9_-]{43}$/)
  expect(await decodeQr(qr)).toBe(first)
  await page.getByRole('button', { name: 'Copiar link' }).press('Enter')
  await expect(field).toBeFocused()
  expect(await field.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([0, first.length])

  const guest = await browser.newContext()
  try {
    const landing = await guest.newPage()
    const response = await landing.goto(first)
    expect(response?.headers()['referrer-policy']).toBe('no-referrer')
    expect(response?.headers()['x-robots-tag']).toBe('noindex, nofollow')
    await expect(landing.getByRole('heading', { name: 'Convite para administrar um evento' })).toBeVisible()
    await expect(landing).toHaveURL(new URL('/convites/aceitar', first).href)
  } finally {
    await guest.close()
  }

  await page.reload()
  await expect(field).toHaveValue(first)
  const replace = page.getByRole('button', { name: 'Substituir convite' })
  await replace.press('Enter')
  const confirm = page.getByRole('button', { name: 'Confirmar substituição' })
  await expect(confirm).toBeFocused()
  await page.getByRole('button', { name: 'Cancelar' }).press('Enter')
  await expect(replace).toBeFocused()
  await expect(field).toHaveValue(first)
  await replace.press('Enter')
  await confirm.press('Enter')
  await expect(field).not.toHaveValue(first)
  await expect(qr).toBeVisible()
  expect(await decodeQr(qr)).toBe(await field.inputValue())
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

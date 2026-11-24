import { expect, test } from '@playwright/test'
import { decodeQr } from './decodeQr'

test('aceita o QR em outra conta, recupera o aceite perdido e encontra o evento', async ({ page, browser }, info) => {
  const owner = process.env.SCRAPBOOK_TEST_AUTHORIZATION
  const invitee = process.env.SCRAPBOOK_TEST_INVITEE_AUTHORIZATION
  const inviteeOrigin = process.env.SCRAPBOOK_TEST_INVITEE_ORIGIN
  const events = JSON.parse(process.env.SCRAPBOOK_TEST_INVITATION_EVENTS ?? '{}')
  const eventId = events[info.project.name]
  if (!owner || !invitee || !inviteeOrigin || !eventId) throw new Error('Use a stack de aceite de convites.')

  const eventName = 'Convite sintético ' + info.project.name
  await page.route('**/api/v1/events/**', route => route.continue({
    headers: { ...route.request().headers(), authorization: owner },
  }))
  await page.goto('/meus-eventos/' + eventId + '/convidar')
  await page.getByRole('button', { name: 'Gerar convite' }).press('Enter')
  const qr = page.getByRole('img', { name: 'QR code do convite de responsável' })
  await expect(qr).toBeVisible()
  const invitation = await decodeQr(qr)
  if (!invitation) throw new Error('QR sintético não pôde ser lido.')

  const recipient = await browser.newContext({ viewport: page.viewportSize() })
  try {
    const entry = await recipient.newPage()
    let ignoredResponse = false
    await entry.route('**/api/v1/**', async route => {
      const request = route.request()
      const browserUrl = new URL(request.url())
      const target = new URL(browserUrl.pathname + browserUrl.search, inviteeOrigin)
      const headers: Record<string, string> = { accept: 'application/json', authorization: invitee, origin: inviteeOrigin }
      const body = request.postDataBuffer()
      if (body) headers['content-type'] = 'application/json'
      const response = await recipient.request.fetch(target.href, {
        method: request.method(), headers, ...(body ? { data: body } : {}),
      })
      if (target.pathname === '/api/v1/invitations/preview') expect(response.status()).toBe(200)
      if (!ignoredResponse && target.pathname === '/api/v1/invitations/accept') {
        expect(response.status()).toBe(200)
        ignoredResponse = true
        await route.fulfill({ status: 503, body: '' })
      } else {
        await route.fulfill({ status: response.status(), contentType: response.headers()['content-type'], body: await response.body() })
      }
    })

    await entry.goto(invitation)
    await expect(entry).toHaveURL(new URL('/convites/aceitar', invitation).href)
    await expect(entry.getByRole('heading', { name: eventName })).toBeVisible()
    expect(await entry.evaluate(async id => (await fetch('/api/v1/events/' + id)).status, eventId)).toBe(404)
    expect(await entry.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await entry.getByRole('button', { name: 'Aceitar convite' }).press('Enter')
    await entry.getByRole('button', { name: 'Recuperar aceite' }).press('Enter')

    const details = new URL('/meus-eventos/' + eventId, invitation).href
    await expect(entry).toHaveURL(details)
    await expect(entry.getByText(eventName, { exact: true })).toBeVisible()
    await entry.getByRole('link', { name: 'Meus eventos', exact: true }).press('Enter')
    const listed = entry.getByRole('link', { name: eventName })
    await expect(listed).toBeVisible()
    await listed.press('Enter')
    await expect(entry).toHaveURL(details)
  } finally {
    await recipient.close()
  }
})

import { expect, test, type Browser, type Page } from '@playwright/test'
import { decodeQr } from '../invitations/decodeQr'

type LockEvent = { id: string; code: string }

const detailsLocked = 'As informações deste evento estão travadas.'
const invitationsLocked = 'Os convites deste evento estão travados.'
const guestText = 'Depoimento sintético enviado para o travamento F09.'
const guestName = 'Convidada sintética da F09'
const invitationApi = '/api/v1/invitations'

let events: LockEvent[]
let siteOrigin: string
let ownerAuthorization: string
let otherAuthorization: string
let otherOrigin: string

test.beforeAll(() => {
  expect(process.env.PLAYWRIGHT_BASE_URL, 'Informe a URL da stack local da F09.').toBeTruthy()
  siteOrigin = new URL(process.env.PLAYWRIGHT_BASE_URL!).origin
  ownerAuthorization = process.env.SCRAPBOOK_TEST_AUTHORIZATION ?? ''
  otherAuthorization = process.env.SCRAPBOOK_TEST_OTHER_AUTHORIZATION ?? ''
  const developmentAuthorization = /^Development [a-f0-9]{64}$/
  expect(developmentAuthorization.test(ownerAuthorization), 'Informe a identidade local do responsável.').toBe(true)
  expect(developmentAuthorization.test(otherAuthorization), 'Informe a identidade local do destinatário.').toBe(true)
  expect(otherAuthorization === ownerAuthorization, 'Use identidades locais distintas.').toBe(false)
  const other = new URL(process.env.SCRAPBOOK_TEST_OTHER_ORIGIN ?? '')
  expect(other.protocol).toMatch(/^https?:$/)
  expect(other.hostname).toBe('127.0.0.1')
  otherOrigin = other.origin

  events = JSON.parse(process.env.SCRAPBOOK_TEST_LOCK_EVENTS ?? 'null')
  const eventShape = { id: expect.stringMatching(/^[1-9][0-9]*$/), code: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/) }
  expect(events, 'Forneça dois eventos descartáveis da F09.').toMatchObject([eventShape, eventShape])
  expect(events[0].id).not.toBe(events[1].id)
  expect(events[0].code).not.toBe(events[1].code)
})

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/events/**', route => route.continue({
    headers: { ...route.request().headers(), authorization: ownerAuthorization },
  }))
})

async function confirmGuestText(browser: Browser, code: string) {
  const guest = await browser.newPage({ baseURL: siteOrigin, viewport: { width: 320, height: 740 } })
  try {
    await guest.goto('/e/' + code)
    await guest.getByRole('link', { name: 'Deixe um depoimento' }).click()
    await guest.getByRole('button', { name: 'Escrever depoimento' }).click()
    await guest.getByRole('textbox', { name: 'Seu depoimento' }).fill(guestText)
    await guest.getByRole('button', { name: 'Revisar', exact: true }).press('Enter')
    await guest.getByRole('button', { name: 'Confirmar identificação' }).press('Enter')
    await guest.getByRole('textbox', { name: 'Nome ou apelido' }).fill(guestName)
    await guest.getByRole('checkbox', { name: 'Li o aviso de acesso.' }).check()
    await guest.getByRole('button', { name: 'Continuar', exact: true }).press('Enter')
    await guest.getByRole('button', { name: 'Enviar', exact: true }).press('Enter')
    await expect(guest.getByRole('heading', { name: 'Recebimento confirmado' })).toBeFocused()
  } finally {
    await guest.close()
  }
}

async function waitForLock(page: Page, eventId: string) {
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/events/' + eventId, {
      headers: { authorization: ownerAuthorization },
      timeout: 2_000,
    })
    expect(response.status()).toBe(200)
    const event = await response.json()
    return event.canEditDetails
  }, { timeout: 12_000, message: 'A primeira confirmação deve travar os detalhes do evento.' }).toBe(false)
}

test('FR-016 S11: salva antes da confirmação e atualiza a configuração após conflito com a trava', async ({ page, browser }) => {
  const { id, code } = events[0]
  const path = '/meus-eventos/' + id
  const apiPath = '/api/v1/events/' + id + '/details'
  const savedName = 'Nome salvo antes do primeiro registro'
  const savedWelcome = 'Boas-vindas salvas antes do primeiro registro.'
  const saveDetails = page.getByRole('button', { name: 'Salvar informações' })
  const details = page.getByRole('group', { name: 'Editar informações' })

  await page.goto(path)
  await details.getByLabel('Nome do evento').fill(savedName)
  await expect(details.getByLabel('Data e horário do evento (Brasília)')).toBeEditable()
  await details.getByLabel('Mensagem de boas-vindas', { exact: true }).selectOption('custom')
  await details.getByLabel('Sua mensagem de boas-vindas').fill(savedWelcome)
  await saveDetails.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: 'Informações salvas.' })).toBeVisible()
  await expect(page.getByRole('heading', { name: savedName, level: 1 })).toBeVisible()
  await expect(page.getByText(savedWelcome, { exact: true })).toBeVisible()

  const opening = page.getByRole('group', { name: 'Política de abertura' })
  await opening.getByLabel('Modo de abertura').selectOption('scheduled')
  const dateField = opening.getByLabel('Data de abertura das lembranças')
  const openingDate = await dateField.inputValue()
  expect(openingDate).toMatch(/^\d{4}-\d{2}-\d{2}$/)

  let releaseChange!: () => void
  const waitForConfirmation = new Promise<void>(resolve => { releaseChange = resolve })
  await page.route('**' + apiPath, async route => {
    await waitForConfirmation
    await route.fallback()
  }, { times: 1 })
  await details.getByLabel('Nome do evento').fill('Rascunho pendente quando o evento trava')
  const writeStarted = page.waitForRequest(request => new URL(request.url()).pathname === apiPath && request.method() === 'PUT')
  try {
    await Promise.all([writeStarted, saveDetails.press('Enter')])
    await confirmGuestText(browser, code)
    await waitForLock(page, id)
  } finally {
    releaseChange()
  }

  const conflict = await (await writeStarted).response()
  if (!conflict) throw new Error('O envio do rascunho não retornou uma resposta.')
  expect(conflict.status()).toBe(409)
  expect(await conflict.json()).toMatchObject({ code: 'EVENT_VERSION_CONFLICT' })
  await expect(page.getByRole('status').filter({ hasText: detailsLocked })).toBeVisible()
  await expect(details).toHaveCount(0)
  await expect(saveDetails).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Convidar responsável' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: savedName, level: 1 })).toBeVisible()
  await expect(page.getByText(savedWelcome, { exact: true })).toBeVisible()
  await expect(page.getByText(guestText)).toHaveCount(0)
  await expect(page.getByText(guestName)).toHaveCount(0)

  const saveOpening = opening.getByRole('button', { name: 'Salvar abertura' })
  await expect(dateField).toHaveValue(openingDate)
  await expect(saveOpening).toBeEnabled()
  await saveOpening.press('Enter')
  const confirmation = page.getByRole('dialog', { name: 'Confirmar mudança de abertura' })
  await confirmation.getByRole('button', { name: 'Confirmar mudança' }).press('Enter')
  await expect(page.getByRole('status').filter({ hasText: 'Configuração de abertura salva.' })).toBeVisible()

  await page.reload()
  await expect(page.getByText(detailsLocked, { exact: true })).toBeVisible()
  await expect(opening.getByLabel('Modo de abertura')).toHaveValue('scheduled')
  await expect(dateField).toHaveValue(openingDate)
})

test('FR-016 S13 e S14: retira o QR e recusa a prévia e o aceite após a primeira confirmação', async ({ page, browser }) => {
  const { id, code } = events[1]
  await page.goto('/meus-eventos/' + id)
  const openInvitations = page.getByRole('link', { name: 'Convidar responsável' })
  await expect(openInvitations).toBeVisible()
  const eventName = await page.getByRole('heading', { level: 1 }).innerText()
  await openInvitations.click()

  const generate = page.getByRole('button', { name: 'Gerar convite' })
  const replace = page.getByRole('button', { name: 'Substituir convite' })
  const copy = page.getByRole('button', { name: 'Copiar link' })
  const link = page.getByRole('textbox', { name: 'Link do convite' })
  const qr = page.getByRole('img', { name: 'QR code do convite de responsável' })
  await generate.press('Enter')
  await expect(replace).toBeVisible()
  await expect(copy).toBeVisible()
  await expect(qr).toBeVisible()
  const invitation = await decodeQr(qr)
  if (!invitation) throw new Error('O QR do convite sintético não pôde ser lido.')
  await expect(link).toHaveValue(invitation)
  const invitationUrl = new URL(invitation)
  expect(invitationUrl.origin).toBe(siteOrigin)
  expect(invitationUrl.pathname).toBe('/convites/aceitar')

  const recipient = await browser.newPage({ viewport: { width: 320, height: 740 } })
  const headers = { accept: 'application/json', authorization: otherAuthorization, origin: otherOrigin }
  try {
    await recipient.route('**/api/v1/invitations/**', async route => {
      const request = route.request()
      const target = new URL(new URL(request.url()).pathname, otherOrigin)
      const response = await recipient.request.fetch(target.href, {
        method: request.method(),
        headers: { ...headers, 'content-type': 'application/json' },
        data: request.postDataBuffer() ?? undefined,
      })
      await route.fulfill({ status: response.status(), contentType: response.headers()['content-type'], body: await response.body() })
    })
    await recipient.goto(invitation)
    await expect(recipient.getByRole('heading', { name: eventName, level: 2 })).toBeVisible()
    const accept = recipient.getByRole('button', { name: 'Aceitar convite' })
    await expect(accept).toBeEnabled()

    await confirmGuestText(browser, code)
    await waitForLock(page, id)

    await page.reload()
    await expect(page.getByRole('status').filter({ hasText: invitationsLocked })).toBeVisible()
    for (const control of [generate, replace, copy, link, qr]) await expect(control).toHaveCount(0)
    await expect(page.getByText(guestText)).toHaveCount(0)
    await expect(page.getByText(guestName)).toHaveCount(0)

    const denied = await recipient.request.post(otherOrigin + invitationApi + '/accept', {
      headers,
      data: { token: invitationUrl.hash.slice(1) },
    })
    expect(denied.status()).toBe(409)
    expect(await denied.json()).toMatchObject({ code: 'INVITATION_EVENT_LOCKED' })

    await recipient.goto(siteOrigin)
    await recipient.goto(invitation)
    await expect(recipient.getByRole('alert')).toHaveText(invitationsLocked)
    await expect(accept).toHaveCount(0)
  } finally {
    await recipient.close()
  }
})

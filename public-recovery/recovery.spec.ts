import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'

const fixture = JSON.parse(readFileSync(process.env.SCRAPBOOK_RECOVERY_MANIFEST!, 'utf8')) as {
  event: { id: string; code: string; name: string; startsAt: string; publicUrl: string }
  closedStartsAt: string; project: string; envFile: string; compose: string
}
if (!fixture.project.startsWith('scrapbook-docker-test-')) throw new Error('Controle exige o projeto descartável próprio.')
const event = fixture.event
const path = `/api/v1/public/events/${event.code}`
const correlation = '11111111-2222-3333-4444-555555555555'
const qr = (page: Page) => page.getByRole('img', { name: 'QR code para abrir a página pública do evento.' })
const sql = (statement: string) => execFileSync('docker', ['compose', '--project-name', fixture.project, '--env-file', fixture.envFile, '-f', fixture.compose,
  'exec', '-T', 'postgres', 'sh', '-c', 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
{ input: statement, stdio: 'pipe', timeout: 30_000 })

test('invalid public links remain guest-only without data or a login redirect', async ({ page }) => {
  for (const suffix of ['', 'bad', 'A'.repeat(43), 'A'.repeat(1024), 'x/y', '%E0%A4%A']) {
    await page.goto('/e/'+suffix)
    await expect(page.getByText('Evento não encontrado. Confira o link recebido.')).toBeVisible()
    await expect(page.locator('.guest-message, .share-panel')).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Meus eventos' })).toHaveCount(0)
    expect(new URL(page.url()).pathname).toContain('/e/')
  }
})

test('network, non-JSON service error and rate limit recover by keyboard with safe correlation', async ({ page, browserName }) => {
  let calls = 0
  await page.route(`**${path}`, async route => {
    calls++
    if (calls===1) { await route.abort('internetdisconnected'); return }
    if (calls===2) { await route.fulfill({ status: 503, contentType: 'text/html', body: '<html>PRIVATE SQL password</html>', headers: { 'X-Correlation-Id': correlation } }); return }
    if (calls===3) { await route.fulfill({ status: 429, contentType: 'application/problem+json', body: '{"detail":"PRIVATE SQL"}', headers: { 'Retry-After': '1', 'X-Correlation-Id': 'private-event-url' } }); return }
    await route.fulfill({ response: await route.fetch() })
  })
  await page.goto(`/e/${event.code}`)
  await expect(page.getByText('Não foi possível conectar. Confira sua conexão e tente novamente.')).toBeVisible()
  await page.keyboard.press(browserName==='webkit'?'Alt+Tab':'Tab')
  const button=page.getByRole('button',{name:'Tentar novamente'})
  await expect(button).toBeFocused(); await page.keyboard.press('Enter')
  await expect(page.getByText('O serviço está temporariamente indisponível. Tente novamente.')).toBeVisible()
  await expect(page.getByLabel('Referência para atendimento')).toHaveValue(correlation)
  await expect(button).toBeFocused()
  expect(await page.locator('main').textContent()).not.toContain('PRIVATE')
  await page.screenshot({path:`artifacts/public-recovery-service-${test.info().project.name}.png`,fullPage:true})
  await page.keyboard.press('Enter')
  await expect(page.getByText('Muitas consultas em pouco tempo. Aguarde antes de tentar novamente.')).toBeVisible()
  await expect(page.getByLabel('Referência para atendimento')).toHaveCount(0)
  await page.evaluate(()=>{window.dispatchEvent(new Event('focus'));window.dispatchEvent(new Event('online'))})
  expect(calls).toBe(3)
  await expect(button).toHaveAttribute('aria-disabled','false')
  expect(calls).toBe(3)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading',{name:event.name})).toBeVisible()
  await expect(page.getByRole('button',{name:'Atualizar evento'})).toBeFocused()
  await expect(qr(page)).toBeVisible(); expect(calls).toBe(4)
})

test('a slow response times out, repeated clicks do not duplicate and a late reply is ignored', async ({page,request})=>{
  const original=await(await request.get(path)).json()
  let calls=0, lateFinished=false
  await page.route(`**${path}`,async route=>{
    calls++
    if(calls===1){
      await new Promise(resolve=>setTimeout(resolve,7000))
      try{await route.fulfill({json:{...original,name:'OLD RESPONSE MUST NOT APPEAR'}})}catch{/* Esperado após abortar o fetch. */}
      lateFinished=true;return
    }
    await route.fulfill({response:await route.fetch()})
  })
  await page.goto(`/e/${event.code}`)
  await page.locator('.read-controls button').evaluate((element)=>{(element as HTMLButtonElement).click();(element as HTMLButtonElement).click()})
  expect(calls).toBe(1)
  await expect(page.getByText('A consulta demorou mais que o esperado. Tente novamente.')).toBeVisible({timeout:10_000})
  await page.getByRole('button',{name:'Tentar novamente'}).click()
  await expect(page.getByRole('heading',{name:event.name})).toBeVisible()
  await expect.poll(()=>lateFinished).toBe(true)
  await expect(page.getByText('OLD RESPONSE MUST NOT APPEAR')).toHaveCount(0)
  expect(calls).toBe(2)
})

test('real database outage recovers without reload and applies the current closed window',async({page,request})=>{
  const detail=`/api/v1/events/${event.id}`
  await page.goto(`/e/${event.code}`)
  await expect(page.getByText('A janela de novos envios está aberta.')).toBeVisible()
  await expect(qr(page)).toBeVisible()
  try{
    sql("ALTER ROLE scrapbook_runtime NOLOGIN; SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='scrapbook_runtime' AND pid<>pg_backend_pid();")
    await page.getByRole('button',{name:'Atualizar evento'}).click()
    await expect(page.getByText('O serviço está temporariamente indisponível. Tente novamente.')).toBeVisible()
    await expect(page.getByText('A janela de novos envios está aberta.')).toHaveCount(0)
    await expect(page.getByRole('heading',{name:event.name})).toBeVisible(); await expect(qr(page)).toBeVisible()
    sql('ALTER ROLE scrapbook_runtime LOGIN;')
    const current=await(await request.get(detail)).json()
    const changed=await request.put(detail,{data:{name:current.name,startsAt:fixture.closedStartsAt,welcomeMessage:current.welcomeMessage,expectedVersion:current.version}})
    expect(changed.status()).toBe(200)
    await page.getByRole('button',{name:'Tentar novamente'}).click()
    await expect(page.getByText('Novos envios encerrados.')).toBeVisible()
    expect(new URL(page.url()).pathname).toBe(`/e/${event.code}`)
    await page.screenshot({path:`artifacts/public-recovery-restored-${test.info().project.name}.png`,fullPage:true})
  }finally{
    sql('ALTER ROLE scrapbook_runtime LOGIN;')
    const current=await(await request.get(detail)).json()
    const restored=await request.put(detail,{data:{name:current.name,startsAt:event.startsAt,welcomeMessage:current.welcomeMessage,expectedVersion:current.version}})
    expect(restored.status()).toBe(200)
  }
})

test('cover and QR generation failures keep textual alternatives and QR retries by keyboard',async({page})=>{
  await page.addInitScript(()=>{
    Object.defineProperty(window,'__pp5QrFailure',{value:true,writable:true})
    const original=HTMLCanvasElement.prototype.getContext
    Object.defineProperty(HTMLCanvasElement.prototype,'getContext',{configurable:true,value:function(this:HTMLCanvasElement,id:string,...args:unknown[]){
      if(Reflect.get(window,'__pp5QrFailure')&&id==='2d')return null
      return Reflect.apply(original,this,[id,...args])
    }})
  })
  await page.route('**/assets/event-cover-default-v1.svg',route=>route.abort())
  await page.goto(`/e/${event.code}`)
  await expect(page.getByText('Capa indisponível.')).toBeVisible()
  await expect(page.getByText('Não foi possível gerar o QR code.')).toBeVisible()
  await expect(page.getByRole('heading',{name:event.name})).toBeVisible()
  await expect(page.getByRole('link',{name:event.publicUrl})).toBeVisible()
  await expect(page.getByRole('button',{name:'Copiar link'})).toBeEnabled()
  await page.evaluate(()=>Reflect.set(window,'__pp5QrFailure',false))
  const retry=page.getByRole('button',{name:'Gerar QR novamente'});await retry.focus();await page.keyboard.press('Enter')
  await expect(qr(page)).toBeVisible()
  await expect(page.getByText('Evento não encontrado. Confira o link recebido.')).toHaveCount(0)
})

test('sharing query errors preserve context and one guarded retry loads its QR',async({page})=>{
  let calls=0
  const sharePath=`/api/v1/events/${event.id}/share`
  await page.route(`**${sharePath}`,async route=>{
    calls++
    if(calls===1){await route.fulfill({status:503,contentType:'text/html',body:'PRIVATE upstream',headers:{'X-Correlation-Id':correlation}});return}
    await new Promise(resolve=>setTimeout(resolve,200));await route.fulfill({response:await route.fetch()})
  })
  await page.goto(`/events/${event.id}/share`)
  await expect(page.getByText('O serviço está temporariamente indisponível. Tente novamente.')).toBeVisible()
  await expect(page.getByLabel('Referência para atendimento')).toHaveValue(correlation)
  await page.getByRole('button',{name:'Tentar novamente'}).evaluate(element=>{(element as HTMLButtonElement).click();(element as HTMLButtonElement).click()})
  await expect(qr(page)).toBeVisible();expect(calls).toBe(2)
  await expect(page.getByRole('link',{name:'← Voltar para o evento'})).toHaveAttribute('href',`/events/${event.id}`)
  expect(await page.locator('main').textContent()).not.toContain('PRIVATE')
})

import { expect, test } from '@playwright/test'
import { readFileSync, writeFileSync } from 'node:fs'
const data = JSON.parse(readFileSync(process.env.SCRAPBOOK_IDENTIFICATION_MANIFEST!, 'utf8'))
test('S17 ready in three seconds under the declared 4G profile', async ({ browser, baseURL }) => {
  const profile = { download_mbps: 10, upload_mbps: 2, latency_ms: 80, cpu_slowdown: 4, width: 390, height: 844, cache: 'disabled', samples: 5 }
  const samples: number[] = []
  for (let index = 0; index < profile.samples; index++) {
    const context = await browser.newContext({ viewport: { width: profile.width, height: profile.height } })
    try {
      const page = await context.newPage()
      const session = await context.newCDPSession(page)
      await session.send('Network.enable')
      await session.send('Network.setCacheDisabled', { cacheDisabled: true })
      await session.send('Network.emulateNetworkConditions', { offline: false, latency: 80, downloadThroughput: 10_000_000 / 8, uploadThroughput: 2_000_000 / 8, connectionType: 'cellular4g' })
      await session.send('Emulation.setCPUThrottlingRate', { rate: 4 })
      await page.goto(`${baseURL}/e/${data.first.code}/identification`, { waitUntil: 'domcontentloaded' })
      await expect(page.getByRole('button', { name: 'Confirmar identificação' })).toBeEnabled()
      await expect(page.getByText(data.notice.body, { exact: true })).toBeVisible()
      const elapsed = Math.round(await page.evaluate(() => performance.now()))
      samples.push(elapsed)
      expect(elapsed).toBeLessThanOrEqual(3000)
    } finally { await context.close() }
  }
  writeFileSync('artifacts/guest-identification-performance.json', JSON.stringify({ profile, ready_ms: samples, browser: browser.version(), physical_device: false }, null, 2) + '\n')
})

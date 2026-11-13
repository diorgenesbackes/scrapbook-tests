import { expect, test } from '@playwright/test'
import { readFileSync, writeFileSync } from 'node:fs'

const manifest = JSON.parse(readFileSync(process.env.SCRAPBOOK_PRESENTATION_MANIFEST!, 'utf8'))
const profile = { download_mbps: 10, upload_mbps: 2, latency_ms: 80, cpu_slowdown: 4, width: 390, height: 844, cache: 'disabled', samples: 5 }

test('page ready within three seconds under the declared 4G profile', async ({ browser, baseURL }) => {
  const samples: number[] = []
  for (let index = 0; index < profile.samples; index++) {
    const context = await browser.newContext({ viewport: { width: profile.width, height: profile.height } })
    try {
      const page = await context.newPage()
      const session = await context.newCDPSession(page)
      await session.send('Network.enable')
      await session.send('Network.setCacheDisabled', { cacheDisabled: true })
      await session.send('Network.emulateNetworkConditions', { offline: false, latency: profile.latency_ms,
        downloadThroughput: 10_000_000 / 8, uploadThroughput: 2_000_000 / 8, connectionType: 'cellular4g' })
      await session.send('Emulation.setCPUThrottlingRate', { rate: profile.cpu_slowdown })
      await page.goto(`${baseURL}/e/${manifest.normal.code}`, { waitUntil: 'domcontentloaded' })
      await expect(page.getByRole('heading', { name: manifest.normal.name })).toBeVisible()
      await expect(page.locator('.guest-message')).toHaveText(manifest.normal.effectiveWelcomeMessage)
      await expect.poll(() => page.locator('.guest-cover img').evaluate(element => (element as HTMLImageElement).complete && (element as HTMLImageElement).naturalWidth > 0)).toBe(true)
      const elapsed = await page.evaluate(() => performance.now())
      samples.push(Math.round(elapsed))
      expect(elapsed).toBeLessThanOrEqual(3000)
    } finally { await context.close() }
  }
  writeFileSync('artifacts/public-presentation-performance.json', JSON.stringify({ profile, ready_ms: samples, max_ms: Math.max(...samples), browser: browser.version(), physical_device: false }, null, 2) + '\n')
})

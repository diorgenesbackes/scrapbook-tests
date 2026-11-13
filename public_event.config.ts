import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.SCRAPBOOK_BROWSER_BASE_URL
if (!baseURL) throw new Error('Execute python3 public_event.py para preparar o laboratório.')
const target = new URL(baseURL)
if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(target.hostname)
  || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
  throw new Error('O destino dos testes públicos deve ser HTTP loopback local.')
}

export default defineConfig({
  testDir: './public-event',
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [['list'], ['json', { outputFile: 'artifacts/public-event-browser.json' }]],
  use: {
    baseURL,
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'], timezoneId: 'Asia/Tokyo' } },
    { name: 'webkit-mobile', use: { ...devices['iPhone 13'], timezoneId: 'America/Los_Angeles' } },
  ],
})

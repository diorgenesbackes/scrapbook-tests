import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.SCRAPBOOK_BROWSER_BASE_URL
if (!baseURL) throw new Error('Execute python3 public_presentation.py para preparar o laboratório.')
const target = new URL(baseURL)
if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(target.hostname)
  || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
  throw new Error('O destino da apresentação deve ser HTTP loopback local.')
}

export default defineConfig({
  testDir: './public-presentation', workers: 1, retries: 0, timeout: 45_000,
  reporter: [['list'], ['json', { outputFile: 'artifacts/public-presentation-browser.json' }]],
  use: { baseURL, screenshot: 'only-on-failure' },
  projects: [
    { name: 'chromium-desktop', testIgnore: '**/performance.spec.ts', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit-mobile', testIgnore: '**/performance.spec.ts', use: { ...devices['iPhone 13'] } },
    { name: 'chromium-4g', testMatch: '**/performance.spec.ts', use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 } } },
  ],
})

import { defineConfig, devices } from '@playwright/test'
const baseURL = process.env.SCRAPBOOK_BROWSER_BASE_URL
if (!baseURL) throw new Error('Execute python3 public_privacy.py para preparar o laboratório.')
const url = new URL(baseURL)
if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Destino exige HTTP loopback.')
export default defineConfig({
  testDir: './public-privacy', workers: 1, retries: 0, timeout: 45_000,
  reporter: [['list'], ['json', { outputFile: 'artifacts/public-privacy-browser.json' }]],
  use: { baseURL, screenshot: 'only-on-failure' },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit-mobile', use: { ...devices['iPhone 13'] } },
  ],
})

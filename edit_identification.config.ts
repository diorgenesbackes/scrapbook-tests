import { defineConfig, devices } from '@playwright/test'
const baseURL = process.env.SCRAPBOOK_BROWSER_BASE_URL
if (!baseURL) throw new Error('Execute python3 edit_identification.py para preparar o laboratório.')
const url = new URL(baseURL)
if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Destino exige HTTP loopback.')
export default defineConfig({
  testDir: '.', testMatch: ['guest-identification/*.spec.ts', 'remembered-identification/*.spec.ts', 'edit-identification/*.spec.ts'],
  workers: 1, retries: 0, timeout: 45_000,
  reporter: [['list'], ['json', { outputFile: 'artifacts/edit-identification-browser.json' }]],
  use: { baseURL, screenshot: 'only-on-failure' },
  projects: [
    { name: 'chromium-desktop', testIgnore: '**/performance.spec.ts', use: { ...devices['Desktop Chrome'], timezoneId: 'Asia/Tokyo' } },
    { name: 'webkit-mobile', testIgnore: '**/performance.spec.ts', use: { ...devices['iPhone 13'], timezoneId: 'America/Los_Angeles' } },
    { name: 'chromium-4g', testMatch: 'guest-identification/performance.spec.ts', use: { ...devices['Desktop Chrome'] } },
  ],
})

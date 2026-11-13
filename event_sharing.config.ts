import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.SCRAPBOOK_BROWSER_BASE_URL
if (!baseURL) throw new Error('Execute python3 event_sharing.py.')
const target = new URL(baseURL)
if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(target.hostname) || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
  throw new Error('O compartilhamento só pode ser testado em loopback local.')
}

export default defineConfig({
  testDir: './event-sharing', workers: 1, retries: 0, timeout: 45_000,
  reporter: [['list'], ['json', { outputFile: 'artifacts/event-sharing-browser.json' }]],
  use: { baseURL, screenshot: 'only-on-failure' },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit-mobile', use: { ...devices['iPhone 13'] } },
  ],
})

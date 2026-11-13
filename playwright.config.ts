import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './browser',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.SCRAPBOOK_BROWSER_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'], timezoneId: 'Asia/Tokyo' } },
    { name: 'webkit-mobile', use: { ...devices['iPhone 13'], timezoneId: 'America/Los_Angeles' } },
  ],
})

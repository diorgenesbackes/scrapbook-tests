import { defineConfig, devices } from '@playwright/test'

const baseURL = new URL(process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173')
if (!['http:', 'https:'].includes(baseURL.protocol) || baseURL.hostname !== '127.0.0.1') {
  throw new Error('A suíte local exige uma URL de loopback.')
}

export default defineConfig({
  testDir: './playwright',
  workers: 3,
  use: { baseURL: baseURL.origin },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 320, height: 740 } } },
    { name: 'firefox-desktop', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit-desktop', use: { ...devices['Desktop Safari'] } },
    { name: 'android-emulado', use: { ...devices['Pixel 7'] } },
    { name: 'iphone-emulado', use: { ...devices['iPhone 13'] } },
  ],
})

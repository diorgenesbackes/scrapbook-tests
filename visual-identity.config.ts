import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './visual-identity',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  reporter: [['list'], ['json', { outputFile: 'artifacts/visual-identity-report.json' }]],
  use: { baseURL: 'http://127.0.0.1:4175', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: 'python3 -m http.server 4175 --bind 127.0.0.1 --directory ../scrapbook-docs/visual-identity',
    url: 'http://127.0.0.1:4175', reuseExistingServer: false, stdout: 'ignore', stderr: 'ignore',
  },
  projects: [
    { name: 'visual-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'visual-webkit', use: { ...devices['iPhone 13'] } },
  ],
})

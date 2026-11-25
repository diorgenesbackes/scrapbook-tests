import { defineConfig } from '@playwright/test'
import camera from './playwright.camera.config'

export default defineConfig({
  ...camera,
  testDir: './photo-submissions',
  timeout: 45_000,
  forbidOnly: true,
  reporter: [['list'], ['junit', { outputFile: 'test-results/photo-submissions.xml' }]],
  use: { ...camera.use, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
})

import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({
  ...foundation,
  testDir: './event-structural-lock',
  projects: foundation.projects?.filter(project => project.name === 'chromium-desktop'),
  workers: 1,
  retries: 0,
  timeout: 45_000,
})

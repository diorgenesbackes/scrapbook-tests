import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({
  ...foundation,
  testDir: './camera',
  workers: 1,
  projects: foundation.projects?.filter(project => ['chromium-desktop', 'android-emulado'].includes(project.name ?? ''))
    .map(project => ({
      ...project,
      use: { ...project.use, launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] } },
    })),
})

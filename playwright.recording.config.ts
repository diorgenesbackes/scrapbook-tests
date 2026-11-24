import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({
  ...foundation,
  testDir: './recording',
  workers: 1,
  projects: foundation.projects?.filter(project => ['chromium-desktop', 'firefox-desktop', 'android-emulado'].includes(project.name ?? ''))
    .map(project => ({
      ...project,
      use: {
        ...project.use,
        launchOptions: project.name === 'firefox-desktop'
          ? { firefoxUserPrefs: { 'media.navigator.streams.fake': true, 'media.navigator.permission.disabled': true } }
          : { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
      },
    })),
})

import { defineConfig, devices } from '@playwright/test'

const target = process.env.SCRAPBOOK_E2E_TARGET ?? 'local'
const base = process.env.PLAYWRIGHT_BASE_URL
if (target !== 'local' && target !== 'url') {
  throw new Error('Use SCRAPBOOK_E2E_TARGET=local ou url.')
}
if (!base) throw new Error('Informe PLAYWRIGHT_BASE_URL para a suíte de vídeo.')
const url = new URL(base)
const protocols = target === 'local' ? ['http:', 'https:'] : ['https:']
if (!protocols.includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/'
  || (target === 'local' && url.hostname !== '127.0.0.1')) {
  throw new Error('Informe a origem loopback da stack local ou a origem HTTPS do ambiente de desenvolvimento.')
}

export default defineConfig({
  testDir: './video-submissions',
  workers: 1,
  timeout: 70_000,
  forbidOnly: true,
  metadata: { target },
  reporter: [['list'], ['junit', { outputFile: 'test-results/video-submissions.xml' }]],
  use: { baseURL: url.origin, screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium-desktop', use: {
    ...devices['Desktop Chrome'],
    viewport: { width: 320, height: 740 },
    launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  } }],
})

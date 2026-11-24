import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({ ...foundation, testDir: './invitations', testMatch: '**/acceptance.spec.ts', workers: 1 })

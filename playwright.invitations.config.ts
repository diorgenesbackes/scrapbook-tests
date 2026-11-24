import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({ ...foundation, testDir: './invitations', testMatch: '**/invitation.spec.ts', workers: 1 })

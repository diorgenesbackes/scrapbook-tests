import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({ ...foundation, testDir: './media', workers: 1 })

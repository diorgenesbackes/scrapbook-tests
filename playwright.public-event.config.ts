import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({ ...foundation, testDir: './public-event', workers: 1 })

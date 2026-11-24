import { defineConfig } from '@playwright/test'
import foundation from './playwright.config'

export default defineConfig({ ...foundation, testDir: './guest-identification', workers: 1 })

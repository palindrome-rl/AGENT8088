import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/audit',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['json', { outputFile: 'audit-results.json' }]],
  use: {
    baseURL: process.env.AUDIT_BASE || 'http://127.0.0.1:8280',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})

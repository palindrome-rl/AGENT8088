import { defineConfig, devices } from '@playwright/test'

/* The socket is stubbed in-page, so these specs need only the dev server --
 * no python backend and no model, unlike tests/audit. */
export default defineConfig({
  testDir: './tests',
  testMatch: 'tool-diff.spec.ts',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5180', trace: 'off' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [{
    command: 'npm run dev',
    url: 'http://127.0.0.1:5180',
    reuseExistingServer: true,
    timeout: 60000,
  }],
})

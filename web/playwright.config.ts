import { defineConfig, devices } from '@playwright/test'

const python = process.env.AGENT8088_TEST_PYTHON || 'python'

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  reporter: [['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5180',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: `"${python}" -m agent8088.cli --web --web-port 8180 --web-dev`,
      url: 'http://127.0.0.1:8180/api/status',
      env: { PYTHONPATH: '../src' },
      reuseExistingServer: true,
      timeout: 30000,
    },
    {
      command: 'npm run dev',
      url: 'http://127.0.0.1:5180',
      reuseExistingServer: true,
      timeout: 30000,
    },
  ],
})

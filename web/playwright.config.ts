import { defineConfig, devices } from '@playwright/test'

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
  webServer: {
    command: 'cd .. && PYTHONPATH=src .venv/bin/python -m agent8088.cli --web --web-port 8180 --web-dev',
    url: 'http://127.0.0.1:8180/api/status',
    reuseExistingServer: true,
    timeout: 30000,
  },
})
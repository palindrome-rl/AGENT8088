import { defineConfig, devices } from '@playwright/test'

/* The prompt-queue specs stub window.WebSocket and mock the REST calls the
 * composer makes, so they need the frontend only - no Python backend, and
 * therefore no real engine, config, or session files. */
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'prompt-queue.spec.ts',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5181',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npx vite --port 5181 --host 127.0.0.1',
    url: 'http://127.0.0.1:5181',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})

import { defineConfig, devices } from '@playwright/test'

/* The stack is brought up (docker compose) by the CI job / the developer before
 * `playwright test`; global-setup just waits for it to answer. baseURL is the
 * nginx-served SPA. Keep the suite to the ~8 critical journeys - it's slow. */
export default defineConfig({
  testDir: './tests',
  globalSetup: './global-setup.ts',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // shared backend state (shares/links); keep ordering sane
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})

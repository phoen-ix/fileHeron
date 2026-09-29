import { defineConfig, devices } from '@playwright/test'

/* The docs tour (./docs): README screenshots of a seeded demo organisation,
 * and a render check of every page it visits. Separate from the journey suite
 * (playwright.config.ts), which never picks it up:
 *
 *   npx playwright test -c playwright.docs.config.ts
 *
 * Screenshots land in ../docs/screenshots (DOCS_SCREENSHOT_DIR overrides). The
 * committed ones come from .github/workflows/docs-screenshots.yml - see
 * CONTRIBUTING.md "Docs screenshots". */
export default defineConfig({
  testDir: './docs',
  globalSetup: './global-setup.ts',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  // The seed is idempotent, but a retried render check would hide a flaky page.
  retries: 0,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
    locale: 'en-GB',
    // Native controls (the expiry's datetime-local) ignore `locale` and follow
    // the browser's own locale, which headless Chromium takes from the process
    // environment - without this they render a 12-hour clock.
    launchOptions: {
      args: ['--lang=en-GB'],
      env: { ...process.env, LANG: 'en_GB.UTF-8', LANGUAGE: 'en_GB:en', LC_ALL: 'en_GB.UTF-8' },
    },
    timezoneId: 'Europe/Vienna',
    screenshot: 'only-on-failure',
    trace: 'off',
  },
})

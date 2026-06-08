/* Wait for the already-started stack to be reachable before any spec runs.
 * The CI job (or the developer) does `docker compose up -d`; here we just poll
 * the SPA + the API health endpoint so the first spec doesn't race the boot. */
const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:8080'

async function waitFor(url: string, label: string, tries = 60): Promise<void> {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url)
      if (r.ok) {
        // eslint-disable-next-line no-console
        console.log(`[e2e] ${label} ready (${r.status})`)
        return
      }
    } catch {
      /* not up yet */
    }
    await new Promise((res) => setTimeout(res, 2000))
  }
  throw new Error(`[e2e] ${label} never became ready at ${url}`)
}

export default async function globalSetup() {
  await waitFor(`${BASE}/api/health`, 'backend')
  await waitFor(`${BASE}/`, 'frontend')
}

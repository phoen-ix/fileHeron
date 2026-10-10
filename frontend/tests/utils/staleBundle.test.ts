/* A tab from before an update must reload rather than dead-end on a chunk the
 * update replaced - and must never reload in a loop when the chunk is genuinely
 * gone. Seen on the reference instance after two updates (2026-09-26, 09-28). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  RELOAD_GUARD_KEY,
  RELOAD_GUARD_MS,
  installStaleBundleRecovery,
  isChunkLoadError,
  reloadAfterUpdate,
  reloadOnce,
  takeUpdateDoneNotice,
  type ReloadEnv,
} from '@/utils/staleBundle'

function memoryStore() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    raw: m,
  }
}

function env(over: Partial<ReloadEnv> = {}) {
  let t = 1_000_000
  const store = memoryStore()
  const e = {
    now: () => t,
    storage: store as ReloadEnv['storage'],
    navigate: vi.fn<(url: string | null) => void>(),
    ...over,
  }
  return { e, store, advance: (ms: number) => (t += ms) }
}

/* Raw source of the entry point: the recovery only works if main.ts installs
 * it, and nothing else would notice that call going missing. */
const MAIN = import.meta.glob('/src/main.ts', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

it('is installed at startup, against the app router', () => {
  expect(MAIN['/src/main.ts']).toMatch(/installStaleBundleRecovery\(router,/)
})

describe('isChunkLoadError', () => {
  it.each([
    new TypeError(
      'Failed to fetch dynamically imported module: https://x/assets/ShareList-C70gjugK.js',
    ),
    new TypeError('error loading dynamically imported module: https://x/assets/A-1.js'),
    new TypeError('Importing a module script failed.'),
    new Error('Unable to preload CSS for /assets/AdminScheduledTasks-nKzojrdI.css'),
    'Loading chunk 42 failed.',
  ])('recognises %s', (err) => {
    expect(isChunkLoadError(err)).toBe(true)
  })

  it.each([null, undefined, new TypeError('x is undefined'), { message: 42 }, 'Network Error'])(
    'ignores %s',
    (err) => {
      expect(isChunkLoadError(err)).toBe(false)
    },
  )
})

describe('reloadOnce', () => {
  it('reloads once, then refuses inside the guard window', () => {
    const { e, advance } = env()
    expect(reloadOnce('/shares', e)).toBe(true)
    expect(e.navigate).toHaveBeenCalledWith('/shares')
    advance(RELOAD_GUARD_MS - 1)
    expect(reloadOnce('/shares', e)).toBe(false)
    expect(e.navigate).toHaveBeenCalledTimes(1)
    advance(2)
    expect(reloadOnce('/shares', e)).toBe(true)
    expect(e.navigate).toHaveBeenCalledTimes(2)
  })

  it('never reloads when it could not remember having done so', () => {
    const { e } = env({ storage: null })
    expect(reloadOnce('/shares', e)).toBe(false)
    const throwing = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => undefined,
      removeItem: () => undefined,
    }
    const { e: e2 } = env({ storage: throwing })
    expect(reloadOnce('/shares', e2)).toBe(false)
    expect(e.navigate).not.toHaveBeenCalled()
    expect(e2.navigate).not.toHaveBeenCalled()
  })
})

describe('installStaleBundleRecovery', () => {
  let dispose: (() => void) | null = null
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    dispose?.()
    dispose = null
    vi.useRealTimers()
  })

  function fakeRouter() {
    let handler: ((err: unknown, to: { fullPath: string }) => void) | null = null
    return {
      onError: (h: typeof handler) => {
        handler = h
        return () => (handler = null)
      },
      fail: (err: unknown, fullPath: string) => handler?.(err, { fullPath }),
    }
  }

  it('sends a failed lazy navigation to its target as a full page load', () => {
    const { e } = env()
    const router = fakeRouter()
    const notify = vi.fn()
    dispose = installStaleBundleRecovery(router as never, notify, () => e)
    router.fail(new TypeError('Failed to fetch dynamically imported module: /a.js'), '/shares?x=1')
    expect(e.navigate).toHaveBeenCalledWith('/shares?x=1')
    expect(notify).not.toHaveBeenCalled()
  })

  it('leaves every other navigation error alone', () => {
    const { e } = env()
    const router = fakeRouter()
    dispose = installStaleBundleRecovery(router as never, vi.fn(), () => e)
    router.fail(new Error('Navigation cancelled'), '/shares')
    expect(e.navigate).not.toHaveBeenCalled()
  })

  it('tells the user instead of looping when the chunk is still missing', () => {
    const { e, store } = env()
    store.setItem(RELOAD_GUARD_KEY, String(e.now())) // reloaded a moment ago
    const router = fakeRouter()
    const notify = vi.fn()
    dispose = installStaleBundleRecovery(router as never, notify, () => e)
    const err = new TypeError('Importing a module script failed.')
    router.fail(err, '/shares')
    router.fail(err, '/shares') // the same failure reaching a second hook
    expect(e.navigate).not.toHaveBeenCalled()
    expect(notify).toHaveBeenCalledTimes(1)
  })

  it('reloads the current page for a dynamic import outside the router', () => {
    const { e } = env()
    dispose = installStaleBundleRecovery(fakeRouter() as never, vi.fn(), () => e)
    window.dispatchEvent(new Event('vite:preloadError'))
    expect(e.navigate).not.toHaveBeenCalled() // deferred to a new task
    vi.runAllTimers()
    expect(e.navigate).toHaveBeenCalledWith(null)
  })

  it('lets the router, which knows the target, win over the preload event', () => {
    const { e } = env()
    const router = fakeRouter()
    dispose = installStaleBundleRecovery(router as never, vi.fn(), () => e)
    window.dispatchEvent(new Event('vite:preloadError'))
    router.fail(new TypeError('Failed to fetch dynamically imported module: /a.js'), '/files')
    vi.runAllTimers()
    expect(e.navigate).toHaveBeenCalledTimes(1)
    expect(e.navigate).toHaveBeenCalledWith('/files')
  })
})

describe('reloadAfterUpdate', () => {
  it('waits until the SPA is served again, then reloads once per job', async () => {
    const { e } = env()
    const served = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true)
    const wait = vi.fn().mockResolvedValue(undefined)
    expect(await reloadAfterUpdate('job-1', 'v9.9.9', { env: e, served, wait })).toBe(true)
    expect(served).toHaveBeenCalledTimes(2)
    expect(e.navigate).toHaveBeenCalledWith(null)
    // The reloaded page hands out the notice once ...
    expect(takeUpdateDoneNotice(e)).toBe('v9.9.9')
    expect(takeUpdateDoneNotice(e)).toBeNull()
    // ... and a status that still names the finished job cannot reload it again.
    expect(await reloadAfterUpdate('job-1', 'v9.9.9', { env: e, served, wait })).toBe(false)
    expect(e.navigate).toHaveBeenCalledTimes(1)
  })

  it('does not reload a page the server is not answering', async () => {
    const { e } = env()
    const served = vi.fn().mockResolvedValue(false)
    const wait = vi.fn().mockResolvedValue(undefined)
    expect(await reloadAfterUpdate('job-2', 'v9.9.9', { env: e, served, wait, attempts: 3 })).toBe(
      false,
    )
    expect(served).toHaveBeenCalledTimes(3)
    expect(e.navigate).not.toHaveBeenCalled()
  })

  it('does nothing without storage to remember the job by', async () => {
    const { e } = env({ storage: null })
    const served = vi.fn().mockResolvedValue(true)
    expect(await reloadAfterUpdate('job-3', 'v9.9.9', { env: e, served })).toBe(false)
    expect(e.navigate).not.toHaveBeenCalled()
  })
})

/* A tab opened before an update keeps running the OLD bundle, and the update
 * replaced every hashed chunk under /assets/. The next navigation to a page that
 * tab has not loaded yet asks for /assets/<View>-<oldhash>.js, which no longer
 * exists: the dynamic import rejects, vue-router aborts the navigation, and the
 * click does nothing. The reference instance's own admin tab did exactly that
 * after the 2026-09-26 and 09-28 updates (AdminScheduledTasks, ShareList: a 404,
 * then the same request again a second later - the user clicking twice).
 *
 * The cure is a full page load: index.html is served no-cache
 * (docker/frontend/nginx.conf), so a fresh one names the new chunks. */

import type { Router } from 'vue-router'

// Chrome, Firefox and Safari word a failed `import()` differently, and Vite's
// preload helper reports a CSS dependency that would not load in its own words.
const CHUNK_ERROR =
  /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|unable to preload css|loading (?:css )?chunk \S+ failed|chunkloaderror/i

export function isChunkLoadError(err: unknown): boolean {
  if (err == null) return false
  if (typeof err === 'string') return CHUNK_ERROR.test(err)
  const e = err as { name?: unknown; message?: unknown }
  return (
    (typeof e.name === 'string' && CHUNK_ERROR.test(e.name)) ||
    (typeof e.message === 'string' && CHUNK_ERROR.test(e.message))
  )
}

export const RELOAD_GUARD_KEY = 'fh:stale-bundle-reload-at'
// A second reload inside this window means the fresh index.html still points at
// something missing; reloading again would loop, so the user is told instead.
export const RELOAD_GUARD_MS = 30_000

type SessionStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export interface ReloadEnv {
  now: () => number
  /** null when the browser refuses storage - then nothing reloads by itself. */
  storage: SessionStore | null
  /** Full page load of `url`; `null` reloads the current page. */
  navigate: (url: string | null) => void
}

function sessionStoreOrNull(): SessionStore | null {
  try {
    const s = window.sessionStorage
    s.getItem(RELOAD_GUARD_KEY) // throws where storage is blocked
    return s
  } catch {
    return null
  }
}

export function browserEnv(): ReloadEnv {
  return {
    now: () => Date.now(),
    storage: sessionStoreOrNull(),
    // `assign` of a URL that differs from the current one only in its hash
    // scrolls instead of loading, so "this page again" is an explicit reload.
    navigate: (url) => (url === null ? window.location.reload() : window.location.assign(url)),
  }
}

/** Load `target` (null: the current page) as a full page, at most once per
 * RELOAD_GUARD_MS. Returns false, and does nothing, when this tab already did
 * so within the window or cannot remember that it did: without the guard a
 * genuinely missing asset would reload forever. */
export function reloadOnce(target: string | null, env: ReloadEnv = browserEnv()): boolean {
  const store = env.storage
  if (!store) return false
  try {
    const last = Number(store.getItem(RELOAD_GUARD_KEY) || 0)
    if (last && env.now() - last < RELOAD_GUARD_MS) return false
    store.setItem(RELOAD_GUARD_KEY, String(env.now()))
  } catch {
    return false
  }
  env.navigate(target)
  return true
}

/** Wire both ways a chunk can fail to load: vue-router's onError for a route's
 * own component (it knows where the user was going) and Vite's
 * `vite:preloadError` for every other dynamic import. `notify` tells the user to
 * reload when the guard refuses to do it for them. */
export function installStaleBundleRecovery(
  router: Pick<Router, 'onError'>,
  notify: () => void,
  env: () => ReloadEnv = browserEnv,
): () => void {
  let navigating = false
  let lastNotice = 0
  const recover = (target: string | null) => {
    if (navigating) return
    const e = env()
    if (reloadOnce(target, e)) {
      navigating = true
      return
    }
    // One failure can reach both hooks; one notice is enough.
    if (lastNotice && e.now() - lastNotice < 2000) return
    lastNotice = e.now()
    notify()
  }
  const removeOnError = router.onError((err, to) => {
    if (isChunkLoadError(err)) recover(to.fullPath)
  })
  const onPreloadError = () => {
    // A new task, not now: for a route's component, vue-router's onError runs in
    // this failure's own promise chain and knows the TARGET, so it goes first.
    // The default is left alone - Vite rethrows, which is what reaches onError.
    setTimeout(() => recover(null), 0)
  }
  window.addEventListener('vite:preloadError', onPreloadError)
  return () => {
    removeOnError()
    window.removeEventListener('vite:preloadError', onPreloadError)
  }
}

// ---------------------------------------------------------------------------
// After an in-app update: the admin's own tab
// ---------------------------------------------------------------------------

const UPDATE_DONE_KEY = 'fh:update-done'

/** Reload the tab that ran a healthy update, once the SPA answers again - the
 * executor reports `healthy` when the BACKEND is up, and a reload that met the
 * frontend mid-restart would show the proxy's error page. The tag rides the
 * reload so the new page can say "updated". Once per job: a status that still
 * names the finished job must not reload again on the next mount. */
export async function reloadAfterUpdate(
  jobId: string,
  tag: string,
  opts: {
    env?: ReloadEnv
    served?: () => Promise<boolean>
    wait?: (ms: number) => Promise<void>
    attempts?: number
  } = {},
): Promise<boolean> {
  const env = opts.env ?? browserEnv()
  const store = env.storage
  if (!store) return false
  const served =
    opts.served ??
    (() =>
      fetch('/', { cache: 'no-store' })
        .then((r) => r.ok)
        .catch(() => false))
  const wait = opts.wait ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  try {
    const done = JSON.parse(store.getItem(UPDATE_DONE_KEY) || 'null') as { job?: string } | null
    if (done?.job === jobId) return false
  } catch {
    return false
  }
  for (let i = 0; i < (opts.attempts ?? 15); i++) {
    if (await served()) {
      try {
        store.setItem(UPDATE_DONE_KEY, JSON.stringify({ job: jobId, tag, shown: false }))
      } catch {
        return false
      }
      env.navigate(null)
      return true
    }
    await wait(2000)
  }
  return false
}

/** The tag of an update this tab just reloaded for, handed out ONCE. */
export function takeUpdateDoneNotice(env: ReloadEnv = browserEnv()): string | null {
  const store = env.storage
  if (!store) return null
  try {
    const done = JSON.parse(store.getItem(UPDATE_DONE_KEY) || 'null') as {
      job?: string
      tag?: string
      shown?: boolean
    } | null
    if (!done || done.shown || !done.tag) return null
    // Keep the job id: it is what stops the same job reloading this tab again.
    store.setItem(UPDATE_DONE_KEY, JSON.stringify({ ...done, shown: true }))
    return done.tag
  } catch {
    return null
  }
}

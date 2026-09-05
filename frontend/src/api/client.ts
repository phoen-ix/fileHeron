/* Axios client with promise-coalesced refresh-on-401.
 *
 * - Access token lives only in memory (Pinia store), set on every successful
 *   /api/auth/login or /api/auth/refresh response. Never persisted.
 * - Refresh cookie is httpOnly + Secure + scoped to /api/auth, so it travels
 *   with /api/auth/refresh automatically and nowhere else.
 * - On a 401 response from any non-refresh endpoint, we attempt one refresh.
 *   Concurrent 401s share a single refresh promise so we don't hammer the
 *   server with five refreshes if five requests fail at once. That promise is
 *   per-TAB; the cross-tab lock below covers the tabs it cannot see.
 * - A refresh failure is not one thing. Only a CREDENTIAL VERDICT (401/403)
 *   clears the auth store and pushes the router to /login (preserving the
 *   original target via `?redirect=...`); a failure that means we could not ASK
 *   - a proxy 502 during a container restart, a network drop, a timeout - leaves
 *   the session alone and simply rejects the request. See `RefreshOutcome`.
 *   A replay that 401s again does sign out - see the `_retry` branch below.
 */

import axios, { type AxiosError, type AxiosRequestConfig } from 'axios'

import type { ApiErrorEnvelope, RefreshResponse } from '@/types/api'

declare module 'axios' {
  // Per-request opt-out: when true, a 401 that cannot be refreshed away does
  // NOT trigger the global onAuthLost() redirect.
  //
  // The comment here used to say the app-bootstrap session probe passes it.
  // Nothing has ever passed it: `bootstrap()` calls `refreshSession()` FIRST and
  // only calls getMe() if that succeeded, so the probe never reaches this
  // branch and the flag was a documented escape hatch that did nothing (audit
  // 2026-07-30, fe-auth-9). It stays declared - it is the correct mechanism if
  // a caller ever does need a 401 not to navigate - but the comment now says
  // what is true, so nobody relies on protection that is not there.
  interface AxiosRequestConfig {
    _skipAuthLost?: boolean
  }
}

/** Instance default: covers uploads, downloads and long list queries. Nothing to
 *  do with auth - the refresh has its own, much shorter budget below. */
const REQUEST_TIMEOUT_MS = 30_000

const api = axios.create({
  baseURL: '/api',
  timeout: REQUEST_TIMEOUT_MS,
  withCredentials: true,
  // Serialize array query params as repeated keys (`?state=a&state=b`)
  // instead of axios's default bracket form (`?state[]=a`). FastAPI's
  // `Query(default_factory=list)` only matches the repeated form;
  // the bracket form silently disappears, leaving the filter unapplied.
  paramsSerializer: { indexes: null },
})

// Set by the auth store on login + refresh; cleared on logout.
let accessToken: string | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

api.interceptors.request.use((config) => {
  if (accessToken && !config.headers?.has?.('Authorization')) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

/* --- cross-tab refresh lock ----------------------------------------------- */

/* `pendingRefresh` below coalesces refreshes within ONE tab. It cannot see the
 * other tabs, and they all share the single httpOnly `fh_refresh` cookie - so
 * two tabs waking at the same moment (the classic trigger is a laptop resuming
 * from sleep with several tabs open, every access token already expired) both
 * POST /api/auth/refresh with the same cookie value. Server-side that is
 * `rotate_refresh`, and it has TWO outcomes, neither of them good:
 *
 *   - the loser's conditional UPDATE returns rowcount 0  -> 401 INVALID_REFRESH,
 *     handled softly, but the SPA logs that tab out of a live session;
 *   - the loser READ the row after the winner committed, so `replaced_by_id` is
 *     already set -> classed as chain replay -> TOKEN_REUSE, which revokes every
 *     session on every device and files a `refresh_token_reused` audit row that
 *     reads as token theft.
 *
 * Which branch you get is a coin flip on commit timing. The second one cannot be
 * fixed server-side: an immediate replay of the same cookie is indistinguishable
 * from this race by anything the backend can see, so any grace window wide
 * enough to help is wide enough to admit a stolen token. The fix therefore has
 * to PREVENT the concurrent request, not classify it - hence a lock.
 *
 * The lock only has to span one cookie jar (a browser profile, or one desktop-
 * client process). Separate devices hold separate refresh chains and have
 * nothing to coordinate.
 *
 * It SEQUENCES rather than suppresses: the waiter still refreshes when its turn
 * comes, against the cookie the winner just set, and succeeds. It must also fail
 * OPEN in every direction - a lock that can wedge sign-in is worse than the race
 * it fixes.
 */

const REFRESH_LOCK = 'fh:refresh-lock'

/* A refresh is a bodyless POST and one DB round-trip - sub-second when healthy.
 * If it has not answered in 8s the server is not well, and `unavailable` is the
 * right verdict, which now costs the user nothing because it no longer signs
 * anyone out. Failing fast is also what keeps the lock below short. */
const REFRESH_TIMEOUT_MS = 8_000

/* THE HOLDER MUST NEVER OUTLIVE ITS OWN LOCK. `tryAcquireStorageLock` treats a
 * record older than the TTL as abandoned and overwrites it, so a TTL shorter
 * than a refresh lets a waiter take over mid-flight - which is precisely the
 * concurrent rotation this lock exists to prevent, and it would fire in the
 * slow/restarting-backend case the whole `unavailable` change was written for.
 *
 * DERIVED, not asserted in a comment: the previous constants said "comfortably
 * longer than a refresh round-trip" and were silently falsified by giving the
 * refresh a 30s timeout. A relationship a comment claims is a relationship that
 * can drift; this one cannot.
 *
 * The 2x budgets BOTH of doRefresh's attempts. Today only one of them can ever
 * be slow - the retry fires only on 'raced', which requires the server to have
 * answered 401, while a timeout yields 'unavailable' and does not retry - but
 * that argument stops holding the moment someone retries 'unavailable' too, and
 * nothing would go red. */
const LOCK_TTL_MS = 2 * REFRESH_TIMEOUT_MS + 2_000
const LOCK_WAIT_MAX_MS = LOCK_TTL_MS + 2_000 // > TTL, so a crashed holder always frees it
const LOCK_POLL_MS = 40

/** Exported solely so the test can pin the ordering above. The relationship is
 *  the thing that broke, and it is not observable from outside otherwise: a
 *  waiter stealing a live lock looks identical to a waiter taking a dead one. */
export const __refreshTimings = Object.freeze({
  REFRESH_TIMEOUT_MS,
  LOCK_TTL_MS,
  LOCK_WAIT_MAX_MS,
})

type LockRecord = { id: string; at: number }

function readLock(): LockRecord | null {
  try {
    const raw = localStorage.getItem(REFRESH_LOCK)
    if (!raw) return null
    const rec = JSON.parse(raw) as LockRecord
    return typeof rec?.id === 'string' && typeof rec?.at === 'number' ? rec : null
  } catch {
    return null
  }
}

function tryAcquireStorageLock(id: string): boolean {
  try {
    const held = readLock()
    // `age < TTL` alone treats a FUTURE timestamp as freshly held, and nothing
    // can clear it - releaseStorageLock only removes a record whose id matches
    // its own. An NTP correction or a VM/laptop resume stepping the clock back
    // would then wedge every tab on the profile for the length of the skew,
    // each spinning the full LOCK_WAIT_MAX_MS before failing open. Resume from
    // sleep is exactly when clocks get stepped, and is the scenario this lock
    // was written for. A record from the future is nonsense: treat it as stale.
    if (held) {
      const age = Date.now() - held.at
      if (age >= 0 && age < LOCK_TTL_MS) return false
    }
    localStorage.setItem(REFRESH_LOCK, JSON.stringify({ id, at: Date.now() }))
    // localStorage has no compare-and-swap, so read back: if another tab wrote
    // between our read and our write, its record is the one in storage and we
    // are not the holder. This narrows the window to microseconds rather than
    // closing it - which is why the INVALID_REFRESH retry below still exists.
    return readLock()?.id === id
  } catch {
    return true // storage unavailable (private mode, quota) - fail open
  }
}

function releaseStorageLock(id: string): void {
  try {
    if (readLock()?.id === id) localStorage.removeItem(REFRESH_LOCK)
  } catch {
    /* nothing to do - the TTL frees it */
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function withStorageLock(fn: () => Promise<RefreshOutcome>): Promise<RefreshOutcome> {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  const deadline = Date.now() + LOCK_WAIT_MAX_MS
  while (!tryAcquireStorageLock(id)) {
    if (Date.now() >= deadline) return fn() // fail open rather than wedge auth
    await sleep(LOCK_POLL_MS + Math.random() * LOCK_POLL_MS)
  }
  try {
    return await fn()
  } finally {
    releaseStorageLock(id)
  }
}

type LockManagerLike = {
  request?: (
    name: string,
    options: { signal?: AbortSignal },
    cb: () => Promise<RefreshOutcome>,
  ) => Promise<RefreshOutcome>
}

/** Web Locks where the browser has it (a real mutex, so no residual window),
 *  the localStorage lock otherwise. */
async function withRefreshLock(fn: () => Promise<RefreshOutcome>): Promise<RefreshOutcome> {
  const locks = (navigator as Navigator & { locks?: LockManagerLike }).locks
  if (typeof locks?.request === 'function') {
    // Web Locks waits FOREVER by default. The storage fallback is bounded by
    // LOCK_WAIT_MAX_MS and fails open; without this the primary path - the one
    // every current browser takes - had no bound at all. That is not merely a
    // slow refresh: `router.beforeEach` awaits `bootstrap()`, and `main.ts`
    // gates `app.mount()` on it, so N tabs queued behind a hung backend freeze
    // navigation and blank a cold load for N x the refresh budget.
    const ctrl = new AbortController()
    const bail = setTimeout(() => ctrl.abort(), LOCK_WAIT_MAX_MS)
    let started = false
    let result: RefreshOutcome | undefined
    try {
      return await locks.request(REFRESH_LOCK, { signal: ctrl.signal }, async () => {
        started = true
        result = await fn()
        return result
      })
    } catch {
      // `fn` never rejects, so reaching here means the ACQUISITION failed or was
      // aborted. If the callback nevertheless ran, its result is the truth - do
      // not re-run it (that is the double refresh this exists to prevent) and do
      // not discard a refresh that actually succeeded, which would reject a
      // request whose replay was guaranteed to work.
      if (started) return result ?? 'unavailable'
      // Never acquired: fall back rather than guess. `unavailable` if even that
      // cannot run - a lock hiccup says nothing about the session.
      return withStorageLock(fn)
    } finally {
      clearTimeout(bail)
    }
  }
  return withStorageLock(fn)
}

/* --- promise-coalesced refresh ------------------------------------------- */

/**
 * `expired` = the server told us this session is over. Sign out.
 * `unavailable` = we could not ASK. The session is probably fine - do not sign
 * anyone out over it.
 *
 * Collapsing these two into one boolean is what made a backend restart log
 * every open tab out: a 502 from the proxy during the container swap was
 * indistinguishable from a revoked session. The in-app updater restarts the
 * backend deliberately, so that window recurs on every update, and it lasts
 * 9-25s - long enough for some tab's 15-minute token to expire inside it.
 */
export type RefreshOutcome = 'ok' | 'expired' | 'unavailable'

let pendingRefresh: Promise<RefreshOutcome> | null = null

/** `'raced'` = another holder of this same cookie rotated it first. */
type Attempt = RefreshOutcome | 'raced'

function classifyRefreshFailure(err: unknown): Attempt {
  // Status first, body second: a proxy 5xx carries Traefik's plain "Bad Gateway"
  // or nginx's HTML page, never our JSON envelope, so nothing here may depend on
  // the body being parseable. (`asEnvelope` returns null for those, which is
  // indistinguishable from a genuine 401 with an empty body - so the code alone
  // cannot classify anything.)
  if (!axios.isAxiosError(err)) return 'unavailable'
  const status = err.response?.status
  if (status === undefined) return 'unavailable' // network error, timeout, abort

  // Only INVALID_REFRESH is retryable. TOKEN_REUSE, SESSION_REVOKED,
  // ACCOUNT_DISABLED and AUTH_REQUIRED (no cookie at all) mean the session
  // really is gone - retrying one of those just delays the sign-out.
  if (status === 401 && asEnvelope(err)?.code === 'INVALID_REFRESH') return 'raced'

  // ONLY a credential verdict signs anyone out: 401 and 403 are the statuses
  // that mean "the server looked at what you presented and rejected it".
  // Everything else means we did not get an answer, and an unanswered question
  // is not grounds for destroying a live session. That deliberately covers the
  // proxy's 502/503/504 during a container restart, a 429 from any future
  // proxy-level limit, and the scan guard's 404 short-circuit
  // (middleware/scan_guard.py), which can hit this route like any other.
  return status === 401 || status === 403 ? 'expired' : 'unavailable'
}

async function attemptRefresh(): Promise<Attempt> {
  try {
    const resp = await axios.post<RefreshResponse>('/api/auth/refresh', null, {
      withCredentials: true,
      // The GLOBAL axios is used here on purpose (so this call never re-enters
      // the interceptor), which means it does not inherit the instance's
      // timeout - it had none at all. An unanswered refresh would then hang
      // indefinitely while HOLDING the cross-tab lock, blocking every other
      // tab's refresh behind it. Its OWN budget, deliberately much shorter than
      // the instance default: LOCK_TTL_MS is derived from this, so raising it
      // widens the lock window too.
      timeout: REFRESH_TIMEOUT_MS,
    })
    // A 200 is not proof of a token. An SPA-fallback misconfiguration
    // (`try_files ... /index.html`) or a captive portal answers this route with
    // 200 text/html, and taking that as success is worse than any failure: the
    // interceptor drops the Authorization header for the replay, the request
    // interceptor then injects nothing because the token is undefined, the
    // replay 401s, and the `_retry` branch signs the user out - on every
    // request, on every page. The desktop client has guarded this since C3
    // (`api/client.py`); the SPA did not.
    const token = resp.data?.access_token
    if (typeof token !== 'string' || !token) return 'unavailable'
    setAccessToken(token)
    return 'ok'
  } catch (err) {
    return classifyRefreshFailure(err)
  }
}

async function doRefresh(): Promise<RefreshOutcome> {
  let outcome = await attemptRefresh()
  if (outcome === 'raced') {
    // The lock failed open, or the microsecond window in the storage fallback
    // lost. The winner has set the new cookie by now; try exactly once more.
    // This runs while STILL HOLDING the lock, which is the whole point: a blind
    // timed retry could fire before the winner's Set-Cookie lands, replay the
    // superseded cookie, and escalate a one-tab logout into TOKEN_REUSE and a
    // full family revoke - turning the symptom into the worse failure.
    outcome = await attemptRefresh()
  }
  // Losing the race twice while holding the lock rules out a same-browser
  // racer, so the cookie genuinely is not valid.
  if (outcome === 'raced') outcome = 'expired'
  if (outcome === 'expired') setAccessToken(null)
  // `unavailable` deliberately keeps the (expired) token and the session: there
  // is nothing to retry against, and the very next request re-enters here once
  // the backend is back. Do NOT turn this into a sleep-and-retry loop - a
  // restart window is 9-25s, so a retry short enough not to freeze the UI (and
  // not to hold the cross-tab lock) cannot cover one anyway. The bell's SSE
  // loop re-tries within ~60s on its own, so the session self-heals.
  return outcome
}

/** Renamed from `refreshOnce` when it stopped returning a boolean: the old call
 *  sites were truthy checks, and every outcome string is truthy, so a silent
 *  in-place type change would have read `'unavailable'` as success. */
export async function refreshSession(): Promise<RefreshOutcome> {
  if (pendingRefresh) return pendingRefresh
  pendingRefresh = (async () => {
    try {
      return await withRefreshLock(doRefresh)
    } finally {
      // Clear only after the awaited request settles to avoid race.
      queueMicrotask(() => {
        pendingRefresh = null
      })
    }
  })()
  return pendingRefresh
}

let onAuthLost: (() => void) | null = null

export function setOnAuthLost(fn: () => void) {
  onAuthLost = fn
}

api.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as AxiosRequestConfig & { _retry?: boolean }
    const status = error.response?.status
    const url = original?.url ?? ''

    const isAuthCall =
      url.includes('/auth/refresh') ||
      url.includes('/auth/login') ||
      url.includes('/auth/logout') ||
      url.includes('/auth/register-from-invite') ||
      url.includes('/auth/forgot-password') ||
      url.includes('/auth/reset-password') ||
      url.includes('/auth/verify-email') ||
      // The SECOND-FACTOR exchange after an SSO or passkey first factor
      // (services/auth.py -> 401 INVALID_TOTP / INVALID_RECOVERY_CODE for a
      // mistyped code). `/auth/login` does not substring-match this, so it was
      // missed - the second time this list was found short. Worse here than
      // elsewhere: the replay double-spends the twofa_complete per-IP budget AND
      // bumps failed_login_count twice, so a wrong code costs 2 against the
      // lockout threshold; and the forced sign-out destroys the pending token,
      // making the user redo the whole SSO round-trip over one typo.
      url.includes('/auth/2fa/complete') ||
      // THE RULE, so the next addition is not guessed at: everything below
      // answers 401 for a WRONG SUBMITTED SECRET from an already-signed-in
      // caller - not for an expired session. Any route that does that belongs
      // here: the INVALID_CREDENTIALS / INVALID_TOTP / INVALID_RECOVERY_CODE
      // raise sites in services/{auth,totp}.py and routers/account.py that are
      // reachable through this axios instance. The anonymous ones
      // (INVALID_PUBLIC_PASSWORD, UNLOCK_REQUIRED, INVALID_MANAGE_TOKEN) are
      // not, because publicLinks.ts and notificationSubscriptions.ts build
      // their own interceptor-less clients.
      //
      // This comment used to claim the list below WAS that full set. It was not,
      // twice over - and a comment asserting completeness is worth nothing next
      // to a check. `backend/tests/test_wrong_secret_routes.py` now enumerates
      // the raise sites mechanically and fails if one is not classified here.
      //
      // Replaying one is worse than useless: the refresh always succeeds, so
      // the request goes again with the same wrong secret and every visible
      // attempt spends the per-IP budget twice. change-password and
      // change-email share one 3-per-15-min bucket, so the user's SECOND typo
      // surfaces as 429 RATE_LIMITED rather than "current password is
      // incorrect". This is the hazard step_up.py answers 403 to avoid. And
      // since the interceptor now fires onAuthLost on a replay that 401s again,
      // a missing entry no longer just wastes a round trip - it SIGNS THE USER
      // OUT for a typo. 2fa/enable was exactly that: it 401s INVALID_TOTP
      // (services/totp.py:150) for a mistyped ENROLMENT code, and it was the
      // one wrong-secret route this list had never carried.
      //
      // Listed individually, NOT as a blanket `/account/` prefix: the rest of
      // that namespace (2fa/status, oidc/links, profile PATCHes) 401s for an
      // expired session, which is precisely what should be refreshed and
      // replayed. Excluding all of it would sign people out instead.
      url.includes('/account/change-password') ||
      // POST only: DELETE /account/email (cancel a pending change) takes no
      // secret and 401s only for an expired session, which must be replayed.
      (original.method?.toUpperCase() === 'POST' && url.includes('/account/email')) ||
      url.includes('/account/2fa/enable') ||
      url.includes('/account/2fa/disable') ||
      url.includes('/account/2fa/recovery-codes/regenerate')

    if (status === 401 && !original._retry && !isAuthCall) {
      original._retry = true
      const outcome = await refreshSession()
      if (outcome === 'ok') {
        // Drop the stale Authorization header so the request interceptor
        // injects the freshly-refreshed token; otherwise the retry replays the
        // now-expired token (the interceptor skips headers that already carry
        // an Authorization) and 401s again.
        ;(original.headers as { delete?(name: string): void } | undefined)?.delete?.(
          'Authorization',
        )
        return api(original)
      }
      // `unavailable` falls through to the reject below WITHOUT signing out: we
      // never got an answer, so the session may well be fine. The caller sees a
      // normal failed request, and the next one re-enters here once the backend
      // is back. Only `expired` is a verdict.
      //
      // The bootstrap session-probe opts out: a failed probe just means
      // "anonymous", it must not force a navigation to /login.
      if (outcome === 'expired' && !original._skipAuthLost) onAuthLost?.()
    } else if (status === 401 && original._retry && !isAuthCall) {
      // The replay 401'd too. The refresh SUCCEEDED to get here, so this is not
      // an expired token - the session died between the refresh and the replay
      // (an admin revoke, a role downgrade, the account being disabled). The
      // guard above is `!original._retry`, so without this branch the promise
      // just rejects and onAuthLost never fires: the SPA sits on a page whose
      // every request 401s instead of returning the user to /login.
      //
      // Signal only - deliberately no second refreshSession(), which would loop.
      if (!original._skipAuthLost) onAuthLost?.()
    }

    return Promise.reject(error)
  },
)

/* Type-narrowing helper for components / pages handling errors. */
export function asEnvelope(e: unknown): ApiErrorEnvelope | null {
  if (axios.isAxiosError(e)) {
    const data = e.response?.data
    if (
      data &&
      typeof data === 'object' &&
      'error' in data &&
      'code' in data &&
      typeof (data as ApiErrorEnvelope).code === 'string'
    ) {
      return data as ApiErrorEnvelope
    }
  }
  return null
}

export default api

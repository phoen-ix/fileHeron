/* Interceptor coverage beyond client.test.ts: request-header injection, the
 * promise-coalesced refresh (N concurrent 401s -> one /auth/refresh), the
 * original-request retry after a successful refresh, and the isAuthCall
 * exclusion (a 401 from an auth endpoint must NOT trigger a refresh loop).
 *
 * Approach mirrors client.test.ts: override the axios adapter so we control
 * every response without a network. The `api` instance copies the adapter at
 * create time and refreshSession() uses the global axios, so we set both. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/* eslint-disable @typescript-eslint/no-explicit-any */

function ok(config: any, data: any = {}) {
  return Promise.resolve({ status: 200, data, headers: {}, statusText: 'OK', config })
}

function unauth(config: any, code?: string) {
  const err: any = new Error('Unauthorized')
  err.isAxiosError = true
  err.config = config
  err.response = {
    status: 401,
    data: code ? { code, error: code, request_id: 'test' } : {},
    headers: {},
    statusText: 'Unauthorized',
    config,
  }
  return Promise.reject(err)
}

/** A proxy 5xx: NOT our JSON envelope. Traefik answers plain "Bad Gateway",
 *  nginx an HTML page - so `code` is absent, exactly as it is on a bare 401.
 *  That is why classification keys on the status, never on the body. */
function gatewayError(config: any, status = 502) {
  const err: any = new Error(`Request failed with status code ${status}`)
  err.isAxiosError = true
  err.config = config
  err.response = {
    status,
    data: status === 502 ? 'Bad Gateway' : `<html><title>${status}</title></html>`,
    headers: {},
    statusText: 'Bad Gateway',
    config,
  }
  return Promise.reject(err)
}

/** No response at all - the connection never completed. */
function networkError(config: any, code = 'ERR_NETWORK') {
  const err: any = new Error('Network Error')
  err.isAxiosError = true
  err.config = config
  err.code = code
  err.request = {}
  return Promise.reject(err)
}

const REFRESH_LOCK = 'fh:refresh-lock'

async function loadFresh() {
  // Reset module-level state (accessToken, pendingRefresh) per test.
  vi.resetModules()
  const axios = (await import('axios')).default
  const client = await import('@/api/client')
  return { axios, client }
}

function setAdapter(axios: any, client: any, adapter: any) {
  axios.defaults.adapter = adapter // refreshSession() uses the global axios
  ;(client.default as any).defaults.adapter = adapter // the api instance
}

// localStorage survives vi.resetModules(), so a lock left behind by one test
// would make the next one wait out the whole 12s acquisition budget.
beforeEach(() => localStorage.clear())
afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('request interceptor', () => {
  it('injects Authorization: Bearer when a token is set', async () => {
    const { axios, client } = await loadFresh()
    let seen: any = null
    setAdapter(axios, client, (config: any) => {
      seen = config
      return ok(config)
    })
    client.setAccessToken('jwt-abc')
    await client.default.get('/account/me')
    const auth = seen.headers?.get?.('Authorization') ?? seen.headers?.Authorization
    expect(auth).toBe('Bearer jwt-abc')
  })

  it('sends no Authorization header when no token is set', async () => {
    const { axios, client } = await loadFresh()
    let seen: any = null
    setAdapter(axios, client, (config: any) => {
      seen = config
      return ok(config)
    })
    await client.default.get('/config-public')
    const auth = seen.headers?.get?.('Authorization') ?? seen.headers?.Authorization
    expect(auth == null).toBe(true)
  })
})

describe('refresh coalescing + retry', () => {
  it('coalesces concurrent 401s into ONE refresh, then retries each request', async () => {
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'new-tok', expires_in_seconds: 900 })
      }
      if (!config._retry) return unauth(config) // first hit: 401
      return ok(config, { retried: true }) // post-refresh retry: success
    })

    const results = await Promise.all([
      client.default.get('/a'),
      client.default.get('/b'),
      client.default.get('/c'),
      client.default.get('/d'),
      client.default.get('/e'),
    ])

    expect(refreshCalls).toBe(1) // five 401s -> a single refresh
    expect(results.every((r) => r.status === 200 && r.data.retried)).toBe(true)
    expect(client.getAccessToken()).toBe('new-tok')
  })

  it('does not retry twice (gives up after one failed refresh)', async () => {
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return unauth(config) // refresh itself fails
      }
      return unauth(config)
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(client.default.get('/account/me')).rejects.toBeDefined()
    expect(refreshCalls).toBe(1)
    expect(spy).toHaveBeenCalledTimes(1)
    expect(client.getAccessToken()).toBe(null)
  })
})

describe('isAuthCall exclusion', () => {
  it('a 401 from /auth/login does NOT trigger a refresh', async () => {
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'x' })
      }
      return unauth(config)
    })
    await expect(client.default.post('/auth/login', {})).rejects.toBeDefined()
    expect(refreshCalls).toBe(0)
  })

  it('a 401 INVALID_TOTP from /account/2fa/enable neither refreshes nor signs out', async () => {
    // A mistyped ENROLMENT code. Before this route was excluded, the refresh
    // succeeded, the replay resent the same wrong code, and the second 401 -
    // now that a replayed 401 fires onAuthLost - signed the user out mid-setup.
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'x' })
      }
      return unauth(config, 'INVALID_TOTP')
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(client.default.post('/account/2fa/enable', { code: '000000' })).rejects.toBeDefined()
    expect(refreshCalls).toBe(0)
    expect(spy).not.toHaveBeenCalled()
  })
})

describe('a replay that 401s again is terminal, not silent', () => {
  it('fires onAuthLost exactly once and does not refresh a second time', async () => {
    // The refresh SUCCEEDED, so this is not an expired token: the session died
    // between the refresh and the replay (revoke, role downgrade, disable).
    // The interceptor guards on !_retry, so without the terminal branch this
    // rejected silently and the SPA sat on a page whose every request 401s.
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'new-tok', expires_in_seconds: 900 })
      }
      return unauth(config) // 401s on the first hit AND on the replay
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)

    await expect(client.default.get('/account/me')).rejects.toBeDefined()
    expect(refreshCalls).toBe(1) // no loop
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('still honours _skipAuthLost on the terminal 401', async () => {
    const { axios, client } = await loadFresh()
    setAdapter(axios, client, (config: any) =>
      (config.url ?? '').includes('/auth/refresh')
        ? ok(config, { access_token: 'new-tok' })
        : unauth(config),
    )
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(
      client.default.get('/account/me', { _skipAuthLost: true } as any),
    ).rejects.toBeDefined()
    expect(spy).not.toHaveBeenCalled()
  })
})

describe('rotation-race handling', () => {
  it('retries a refresh that lost the rotation race (INVALID_REFRESH) exactly once', async () => {
    // Another holder of the same cookie rotated first. The winner has set the
    // new cookie, so a second attempt - sequenced behind the lock, not on a
    // blind timer - presents it and succeeds.
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return refreshCalls === 1
          ? unauth(config, 'INVALID_REFRESH')
          : ok(config, { access_token: 'after-race', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)

    const res = await client.default.get('/account/me')
    expect(refreshCalls).toBe(2)
    expect(res.data.retried).toBe(true)
    expect(client.getAccessToken()).toBe('after-race')
    expect(spy).not.toHaveBeenCalled()
  })

  it('does NOT retry TOKEN_REUSE - that session really is gone', async () => {
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return unauth(config, 'TOKEN_REUSE')
      }
      return unauth(config)
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(client.default.get('/account/me')).rejects.toBeDefined()
    expect(refreshCalls).toBe(1) // retrying would only delay the sign-out
    expect(spy).toHaveBeenCalledTimes(1)
    expect(client.getAccessToken()).toBe(null)
  })
})

describe('cross-tab refresh lock', () => {
  it('waits for another tab that holds the lock instead of racing it', async () => {
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'mine', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })

    // Another tab is mid-refresh right now.
    localStorage.setItem(REFRESH_LOCK, JSON.stringify({ id: 'other-tab', at: Date.now() }))

    const pending = client.default.get('/account/me')
    await new Promise((r) => setTimeout(r, 150))
    expect(refreshCalls).toBe(0) // still queued behind the holder

    localStorage.removeItem(REFRESH_LOCK) // the other tab finished
    const res = await pending
    expect(refreshCalls).toBe(1)
    expect(res.data.retried).toBe(true)
    // and we cleaned up after ourselves
    expect(localStorage.getItem(REFRESH_LOCK)).toBe(null)
  })

  it('ignores a stale lock rather than wedging sign-in', async () => {
    // A tab that crashed mid-refresh must not lock everyone else out. Failing
    // OPEN is the rule: a lock that can wedge auth is worse than the race.
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'mine', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })

    localStorage.setItem(
      REFRESH_LOCK,
      JSON.stringify({ id: 'dead-tab', at: Date.now() - 60_000 }),
    )

    const res = await client.default.get('/account/me')
    expect(refreshCalls).toBe(1)
    expect(res.data.retried).toBe(true)
  })

  it('refreshes anyway when localStorage throws', async () => {
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'mine', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('private mode')
    })

    const res = await client.default.get('/account/me')
    expect(refreshCalls).toBe(1)
    expect(res.data.retried).toBe(true)
  })
})
/* eslint-enable @typescript-eslint/no-explicit-any */

describe('an unreachable server must not sign anyone out', () => {
  // The whole point of the outcome union. Before it, EVERY refresh failure
  // collapsed to "sign out", so a backend container restart - which the in-app
  // updater performs deliberately, and which serves 502s for 9-25s - pushed
  // every open tab to /login with a perfectly live session.
  const transient: [string, (c: any) => Promise<never>][] = [
    ['502 from the proxy', (c) => gatewayError(c, 502)],
    ['503', (c) => gatewayError(c, 503)],
    ['504 gateway timeout', (c) => gatewayError(c, 504)],
    ['500 from the app', (c) => gatewayError(c, 500)],
    ['429', (c) => gatewayError(c, 429)],
    ['404 (the scan guard short-circuit)', (c) => gatewayError(c, 404)],
    ['a network error', (c) => networkError(c)],
    ['a timeout', (c) => networkError(c, 'ECONNABORTED')],
  ]

  for (const [label, fail] of transient) {
    it(`keeps the session when the refresh hits ${label}`, async () => {
      const { axios, client } = await loadFresh()
      let refreshCalls = 0
      setAdapter(axios, client, (config: any) => {
        if ((config.url ?? '').includes('/auth/refresh')) {
          refreshCalls++
          return fail(config)
        }
        return unauth(config)
      })
      const spy = vi.fn()
      client.setOnAuthLost(spy)
      client.setAccessToken('still-mine')

      await expect(client.default.get('/account/me')).rejects.toBeDefined()
      expect(refreshCalls).toBe(1)
      expect(spy, 'must NOT sign the user out').not.toHaveBeenCalled()
      // The token is kept: it may still be good, and clearing it only
      // manufactures the next 401.
      expect(client.getAccessToken()).toBe('still-mine')
    })
  }

  it('recovers on the next request once the backend is back', async () => {
    const { axios, client } = await loadFresh()
    let backendDown = true
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        return backendDown
          ? gatewayError(config, 502)
          : ok(config, { access_token: 'recovered', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)

    await expect(client.default.get('/account/me')).rejects.toBeDefined()
    backendDown = false
    const res = await client.default.get('/account/me')

    expect(res.data.retried).toBe(true)
    expect(client.getAccessToken()).toBe('recovered')
    expect(spy).not.toHaveBeenCalled()
  })

  it('still signs out on a credential verdict - 403 ACCOUNT_DISABLED', async () => {
    // The regression guard that keeps the distinction meaningful: if everything
    // became "unavailable", a genuinely dead session would never sign out.
    const { axios, client } = await loadFresh()
    setAdapter(axios, client, (config: any) =>
      (config.url ?? '').includes('/auth/refresh')
        ? gatewayError(config, 403)
        : unauth(config),
    )
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(client.default.get('/account/me')).rejects.toBeDefined()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(client.getAccessToken()).toBe(null)
  })

  it('still signs out on a 401 carrying no envelope at all', async () => {
    const { axios, client } = await loadFresh()
    setAdapter(axios, client, (config: any) => unauth(config))
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(client.default.get('/account/me')).rejects.toBeDefined()
    expect(spy).toHaveBeenCalledTimes(1)
  })
})

describe('refreshSession outcome, read directly', () => {
  // bootstrap() consumes this value, and the old call site was `if (await
  // refreshOnce())`. Every outcome string is truthy, so pin the values rather
  // than their truthiness - a truthy check would read 'unavailable' as success
  // and sign a cold-booting tab in as nobody.
  it('returns the three outcomes distinctly', async () => {
    const { axios, client } = await loadFresh()
    let mode: 'ok' | 'gone' | 'down' = 'ok'
    setAdapter(axios, client, (config: any) => {
      if (mode === 'ok') return ok(config, { access_token: 't', expires_in_seconds: 900 })
      if (mode === 'gone') return unauth(config, 'TOKEN_REUSE')
      return gatewayError(config, 502)
    })

    expect(await client.refreshSession()).toBe('ok')
    mode = 'gone'
    expect(await client.refreshSession()).toBe('expired')
    mode = 'down'
    expect(await client.refreshSession()).toBe('unavailable')
  })
})

describe('Web Locks failure paths', () => {
  // happy-dom implements no navigator.locks, so the branch that handles a
  // rejecting LockManager is otherwise never executed - and it is a SIGN-OUT
  // path. The fake here stands in for the browser's mutex; what is under test
  // is our own branch, not the Lock API.
  function withFakeLocks(request: any, fn: () => Promise<void>) {
    Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true })
    return fn().finally(() => {
      Reflect.deleteProperty(navigator as object, 'locks')
    })
  }

  it('a lock that rejects AFTER the refresh ran keeps the refresh it already got', async () => {
    // This used to assert only that nobody was signed out, and deliberately did
    // not look at the token - which baked in a real loss: a refresh that had
    // SUCCEEDED was thrown away and reported as `unavailable`, so the original
    // request was rejected even though its replay was guaranteed to work.
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'new-tok', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)

    let res: any
    await withFakeLocks(
      async (_name: string, _opts: unknown, cb: () => Promise<unknown>) => {
        await cb() // the refresh really happens...
        throw new Error('lock aborted') // ...then the lock layer blows up
      },
      async () => {
        res = await client.default.get('/account/me')
      },
    )

    // Ran exactly once - re-running it is the double refresh the lock exists to
    // prevent - and its result survives the lock layer failing.
    expect(refreshCalls).toBe(1)
    expect(client.getAccessToken()).toBe('new-tok')
    expect(res.data.retried).toBe(true)
    expect(spy).not.toHaveBeenCalled()
  })

  it('a lock that rejects BEFORE the callback falls back and still refreshes', async () => {
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'via-fallback', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })

    let res: any
    await withFakeLocks(
      async () => {
        throw new Error('cannot acquire')
      },
      async () => {
        res = await client.default.get('/account/me')
      },
    )

    expect(refreshCalls).toBe(1) // the localStorage lock took over
    expect(res.data.retried).toBe(true)
    expect(client.getAccessToken()).toBe('via-fallback')
  })

  it('passes an AbortSignal so a queued acquisition cannot wait forever', async () => {
    // Web Locks waits indefinitely by default. The storage fallback is bounded
    // and fails open; without a signal the PRIMARY path - what every current
    // browser takes - had no bound at all. That freezes more than a refresh:
    // router.beforeEach awaits bootstrap() and main.ts gates app.mount() on it,
    // so tabs queued behind a hung backend blank a cold load.
    const { axios, client } = await loadFresh()
    setAdapter(axios, client, (config: any) =>
      (config.url ?? '').includes('/auth/refresh')
        ? ok(config, { access_token: 't', expires_in_seconds: 900 })
        : unauth(config),
    )

    let sawSignal: AbortSignal | undefined
    await withFakeLocks(
      async (_name: string, opts: { signal?: AbortSignal }, cb: () => Promise<unknown>) => {
        sawSignal = opts?.signal
        return cb()
      },
      async () => {
        await client.refreshSession()
      },
    )

    expect(sawSignal, 'locks.request must be given a signal').toBeInstanceOf(AbortSignal)
    expect(sawSignal!.aborted).toBe(false) // not aborted on the happy path
  })
})

describe('the lock outlives the refresh it protects', () => {
  it('the refresh budget is shorter than the lock TTL, both attempts included', async () => {
    // The invariant that broke: the refresh POST was given the 30s instance
    // timeout while the lock TTL was 10s, so a slow refresh - the restarting
    // backend this whole change targets - outlived the lock protecting it.
    // tryAcquireStorageLock then treats the record as abandoned and OVERWRITES
    // it, so another tab rotates the same cookie concurrently, ending in either
    // a stray /login or a TOKEN_REUSE family revoke.
    //
    // Asserted on the real values rather than re-derived: a waiter stealing a
    // LIVE lock is indistinguishable from one taking a dead lock, so there is
    // no behavioural signal to test. The relationship IS the thing.
    const { client } = await loadFresh()
    const { REFRESH_TIMEOUT_MS, LOCK_TTL_MS, LOCK_WAIT_MAX_MS } = client.__refreshTimings

    // doRefresh can call attemptRefresh twice (the rotation-race retry).
    expect(2 * REFRESH_TIMEOUT_MS).toBeLessThan(LOCK_TTL_MS)
    // and a crashed holder must always free the lock for a waiter.
    expect(LOCK_TTL_MS).toBeLessThan(LOCK_WAIT_MAX_MS)
  })

  it('a holder keeps its lock for the whole refresh, however slow', async () => {
    // tryAcquireStorageLock treats a record older than LOCK_TTL_MS as abandoned
    // and OVERWRITES it. So if a refresh can run longer than the TTL, another
    // tab takes the lock mid-flight and rotates the same cookie concurrently -
    // the exact race the lock exists to prevent, and one that ends in either a
    // stray /login or a TOKEN_REUSE family revoke.
    //
    // It bit for real: the refresh POST was given the 30s instance timeout
    // while the TTL was 10s. The constants are derived from one another now, so
    // this test is the guard against them being pulled apart again.
    vi.useFakeTimers()
    try {
      const { axios, client } = await loadFresh()
      let release: (() => void) | null = null
      setAdapter(axios, client, (config: any) => {
        if ((config.url ?? '').includes('/auth/refresh')) {
          // Never settles until we say so - a backend that is restarting.
          return new Promise((resolve) => {
            release = () => resolve({
              status: 200,
              data: { access_token: 'eventually', expires_in_seconds: 900 },
              headers: {},
              statusText: 'OK',
              config,
            } as never)
          })
        }
        return config._retry ? ok(config, { retried: true }) : unauth(config)
      })

      const pending = client.default.get('/account/me')
      await vi.advanceTimersByTimeAsync(50) // let it acquire and start the POST

      const holder = JSON.parse(localStorage.getItem(REFRESH_LOCK) ?? 'null')
      expect(holder, 'the refresher should hold the lock').not.toBeNull()

      // Sit for the LONGEST a refresh is allowed to take (both attempts) while
      // it is still in flight. The record must still be inside its own TTL - if
      // it had lapsed, a second tab calling tryAcquireStorageLock here would
      // overwrite it and rotate the same cookie concurrently.
      await vi.advanceTimersByTimeAsync(2 * client.__refreshTimings.REFRESH_TIMEOUT_MS - 500)
      const stillHeld = JSON.parse(localStorage.getItem(REFRESH_LOCK) ?? 'null')
      expect(stillHeld?.id).toBe(holder.id)
      expect(
        Date.now() - stillHeld.at,
        'the record must still be inside its own TTL while the refresh runs',
      ).toBeLessThan(client.__refreshTimings.LOCK_TTL_MS)

      release!()
      await vi.advanceTimersByTimeAsync(50)
      await pending
      // ...and it cleans up after itself.
      expect(localStorage.getItem(REFRESH_LOCK)).toBe(null)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('regressions found reviewing this change', () => {
  it('a mistyped code at /auth/2fa/complete neither refreshes nor signs out', async () => {
    // The second-factor exchange after SSO or a passkey. `/auth/login` does not
    // substring-match it, so it was missed - the second time this list was
    // short. The replay would resend the same wrong code, double-spending the
    // per-IP budget AND failed_login_count, then sign the user out and destroy
    // the pending token, forcing the whole SSO round-trip again over one typo.
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'x' })
      }
      return unauth(config, 'INVALID_TOTP')
    })
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(
      client.default.post('/auth/2fa/complete', { pending_token: 'p', totp_code: '000000' }),
    ).rejects.toBeDefined()
    expect(refreshCalls).toBe(0)
    expect(spy).not.toHaveBeenCalled()
  })

  it('a 200 carrying no access_token is unavailable, not ok', async () => {
    // An SPA-fallback misconfiguration or a captive portal answers the refresh
    // with 200 text/html. Treating that as success dropped the Authorization
    // header for the replay, which then 401'd and signed the user out - on
    // every request, on every page.
    const { axios, client } = await loadFresh()
    setAdapter(axios, client, (config: any) =>
      (config.url ?? '').includes('/auth/refresh')
        ? ok(config, '<!doctype html><title>app</title>')
        : unauth(config),
    )
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    client.setAccessToken('still-mine')

    expect(await client.refreshSession()).toBe('unavailable')
    expect(client.getAccessToken()).toBe('still-mine')
    expect(spy).not.toHaveBeenCalled()
  })

  it('a lock record dated in the future is treated as stale, not held forever', async () => {
    // `age < TTL` alone reads a future timestamp as freshly held, and nothing
    // can clear it - releaseStorageLock only removes its own id. An NTP
    // correction or a VM resume stepping the clock back would wedge every tab
    // for the length of the skew. Resume-from-sleep is when clocks get stepped,
    // and is the scenario this lock was written for.
    const { axios, client } = await loadFresh()
    let refreshCalls = 0
    setAdapter(axios, client, (config: any) => {
      if ((config.url ?? '').includes('/auth/refresh')) {
        refreshCalls++
        return ok(config, { access_token: 'mine', expires_in_seconds: 900 })
      }
      return config._retry ? ok(config, { retried: true }) : unauth(config)
    })

    localStorage.setItem(
      REFRESH_LOCK,
      JSON.stringify({ id: 'clock-skewed-tab', at: Date.now() + 5 * 60_000 }),
    )

    const res = await client.default.get('/account/me')
    expect(refreshCalls).toBe(1)
    expect(res.data.retried).toBe(true)
  })
})

/* Interceptor coverage beyond client.test.ts: request-header injection, the
 * promise-coalesced refresh (N concurrent 401s -> one /auth/refresh), the
 * original-request retry after a successful refresh, and the isAuthCall
 * exclusion (a 401 from an auth endpoint must NOT trigger a refresh loop).
 *
 * Approach mirrors client.test.ts: override the axios adapter so we control
 * every response without a network. The `api` instance copies the adapter at
 * create time and refreshOnce() uses the global axios, so we set both. */

import { afterEach, describe, expect, it, vi } from 'vitest'

/* eslint-disable @typescript-eslint/no-explicit-any */

function ok(config: any, data: any = {}) {
  return Promise.resolve({ status: 200, data, headers: {}, statusText: 'OK', config })
}

function unauth(config: any) {
  const err: any = new Error('Unauthorized')
  err.isAxiosError = true
  err.config = config
  err.response = { status: 401, data: {}, headers: {}, statusText: 'Unauthorized', config }
  return Promise.reject(err)
}

async function loadFresh() {
  // Reset module-level state (accessToken, pendingRefresh) per test.
  vi.resetModules()
  const axios = (await import('axios')).default
  const client = await import('@/api/client')
  return { axios, client }
}

function setAdapter(axios: any, client: any, adapter: any) {
  axios.defaults.adapter = adapter // refreshOnce() uses the global axios
  ;(client.default as any).defaults.adapter = adapter // the api instance
}

afterEach(() => vi.restoreAllMocks())

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
})
/* eslint-enable @typescript-eslint/no-explicit-any */

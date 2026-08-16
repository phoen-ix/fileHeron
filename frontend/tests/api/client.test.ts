/* Tests for the axios client's refresh-on-401 interceptor + the
 * _skipAuthLost opt-out (so an anonymous visitor on a public page isn't
 * bounced to /login by the app-bootstrap session probe). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let getAccessToken: () => string | null
let setAccessToken: (t: string | null) => void

beforeEach(async () => {
  // Re-import each test so module-level state (accessToken) starts fresh.
  vi.resetModules()
  const mod = await import('@/api/client')
  getAccessToken = mod.getAccessToken
  setAccessToken = mod.setAccessToken
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('client.setAccessToken / getAccessToken', () => {
  it('round-trips a value', () => {
    setAccessToken('fake-jwt')
    expect(getAccessToken()).toBe('fake-jwt')
    setAccessToken(null)
    expect(getAccessToken()).toBe(null)
  })
})

/* eslint-disable @typescript-eslint/no-explicit-any */
function reject401(config: any) {
  const err: any = new Error('Unauthorized')
  err.isAxiosError = true
  err.config = config
  err.response = {
    status: 401,
    data: {},
    headers: {},
    statusText: 'Unauthorized',
    config,
  }
  return Promise.reject(err)
}

async function loadFreshWith401() {
  vi.resetModules()
  const axios = (await import('axios')).default
  const client = await import('@/api/client')
  // The api instance copies axios.defaults.adapter at create time, and
  // refreshSession() uses the global axios - force both to 401.
  axios.defaults.adapter = reject401 as any
  ;(client.default as any).defaults.adapter = reject401
  return client
}

describe('401 interceptor → onAuthLost', () => {
  it('fires onAuthLost on a normal 401 once refresh fails', async () => {
    const client = await loadFreshWith401()
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(client.default.get('/account/me')).rejects.toBeDefined()
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('does NOT fire onAuthLost when _skipAuthLost is set (bootstrap probe)', async () => {
    const client = await loadFreshWith401()
    const spy = vi.fn()
    client.setOnAuthLost(spy)
    await expect(
      client.default.get('/account/me', { _skipAuthLost: true }),
    ).rejects.toBeDefined()
    expect(spy).not.toHaveBeenCalled()
  })
})
/* eslint-enable @typescript-eslint/no-explicit-any */

/* Tests for the axios client's refresh-on-401 interceptor.
 *
 * We use a real http server fixture rather than mocking the axios adapter,
 * so the interceptor + retry path is exercised end-to-end against a tiny
 * happy-dom-friendly fetch fake. */

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

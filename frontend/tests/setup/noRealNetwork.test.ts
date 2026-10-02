/* The guard in tests/setup/noRealNetwork.ts. Without it, an unmocked API call in
 * a test is a real request to localhost:3000 that the code under test swallows.
 * This file never imports the guard - it must already be installed by
 * vitest.config.ts `setupFiles`, as it is for every other test file. */
import { describe, expect, it } from 'vitest'

import api from '@/api/client'

import type { NoRealNetworkGuard } from './noRealNetwork'

const guard = (globalThis as { __noRealNetwork?: NoRealNetworkGuard }).__noRealNetwork

describe('the no-real-network guard', () => {
  it('is installed for every test file by the vitest config', () => {
    expect(guard).toBeDefined()
  })

  it('fails an axios request through the real API client as a network error', async () => {
    // As an unreachable server would: ERR_NETWORK, config intact, so the
    // client's interceptors run as they do for real.
    const err = await api.post('/uploads/direct', {}).catch((e: unknown) => e)
    expect(err).toMatchObject({
      code: 'ERR_NETWORK',
      message: expect.stringMatching(/real network request refused/),
      config: expect.objectContaining({ url: '/uploads/direct' }),
    })
    expect(guard?.take()).toEqual(['POST /api/uploads/direct'])
  })

  it('refuses fetch, and records the method', async () => {
    await expect(fetch('/api/config-public')).rejects.toThrow(/real network request refused/)
    await expect(fetch('/api/x', { method: 'post' })).rejects.toThrow()
    expect(guard?.take()).toEqual(['GET /api/config-public', 'POST /api/x'])
  })

  it('leaves nothing behind once the attempts are taken', () => {
    expect(guard?.take()).toEqual([])
  })
})

/* files-added was called once per batch, so a file that finished later - a
 * Retry after a failure - was never registered: never announced to the
 * recipients, missing from the audit row, and missing from the share page. */
import { describe, expect, it, vi } from 'vitest'

import { FILES_ADDED_MAX, createSettledRegistrar } from '@/composables/settledRegistrar'
import type { UploadItem } from '@/composables/useUpload'

function item(fileId: string, state: UploadItem['state']): UploadItem {
  return { uid: fileId, fileId, state } as unknown as UploadItem
}

describe('createSettledRegistrar', () => {
  it('registers each settled file once, including one that finished on retry', async () => {
    const items = [item('a', 'done'), item('b', 'finalizing'), item('c', 'error')]
    const register = vi.fn(async () => {})
    const r = createSettledRegistrar({ shareId: () => 's1', items: () => items, register })

    expect(await r.flush()).toEqual(['a', 'b'])
    items[2] = item('c', 'done') // the Retry succeeded
    expect(await r.flush()).toEqual(['c'])
    expect(await r.flush()).toEqual([])
    expect(register.mock.calls).toEqual([
      ['s1', ['a', 'b'], true],
      ['s1', ['c'], true],
    ])
    expect(r.count).toBe(3)
  })

  it('keeps ids the server refused for the next flush', async () => {
    const register = vi.fn().mockRejectedValueOnce(new Error('503')).mockResolvedValue(undefined)
    const r = createSettledRegistrar({
      shareId: () => 's1',
      items: () => [item('a', 'done')],
      register,
    })
    await expect(r.flush()).rejects.toThrow('503')
    expect(await r.flush()).toEqual(['a'])
  })

  it('does not send the same ids twice when flushes overlap', async () => {
    let release!: () => void
    const register = vi.fn(() => new Promise<void>((res) => (release = res)))
    const r = createSettledRegistrar({
      shareId: () => 's1',
      items: () => [item('a', 'done')],
      register,
    })
    const first = r.flush()
    const second = r.flush()
    await vi.waitFor(() => expect(register).toHaveBeenCalled())
    release()
    await first
    await second
    expect(register).toHaveBeenCalledTimes(1)
  })

  /* A dropped folder easily settles more files than the server takes in one
   * call; 1,001 got a 422 for the whole batch, so none were registered. */
  it('sends a large batch in chunks, with `last` only on the final one', async () => {
    const n = FILES_ADDED_MAX * 2 + 5
    const items = Array.from({ length: n }, (_, i) => item(`f${i}`, 'done'))
    const register = vi.fn(async () => {})
    const r = createSettledRegistrar({ shareId: () => 's1', items: () => items, register })
    expect(await r.flush()).toHaveLength(n)
    const calls = register.mock.calls as unknown as [string, string[], boolean][]
    expect(calls.map(([, ids]) => ids.length)).toEqual([FILES_ADDED_MAX, FILES_ADDED_MAX, 5])
    expect(calls.map(([, , last]) => last)).toEqual([false, false, true])
    expect(new Set(calls.flatMap(([, ids]) => ids)).size).toBe(n)
  })

  it('a failed chunk leaves only the ids still unsent for the next flush', async () => {
    const items = Array.from({ length: FILES_ADDED_MAX + 3 }, (_, i) => item(`f${i}`, 'done'))
    const register = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('503'))
      .mockResolvedValue(undefined)
    const r = createSettledRegistrar({ shareId: () => 's1', items: () => items, register })
    await expect(r.flush()).rejects.toThrow('503')
    expect(r.count).toBe(FILES_ADDED_MAX)
    expect(await r.flush()).toHaveLength(3)
  })

  it('does nothing without a share', async () => {
    const register = vi.fn()
    const r = createSettledRegistrar({
      shareId: () => null,
      items: () => [item('a', 'done')],
      register,
    })
    expect(await r.flush()).toEqual([])
    expect(register).not.toHaveBeenCalled()
  })
})

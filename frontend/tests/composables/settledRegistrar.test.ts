/* files-added was called once per batch, so a file that finished later - a
 * Retry after a failure - was never registered: never announced to the
 * recipients, missing from the audit row, and missing from the share page. */
import { describe, expect, it, vi } from 'vitest'

import { createSettledRegistrar } from '@/composables/settledRegistrar'
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
      ['s1', ['a', 'b']],
      ['s1', ['c']],
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

  it('does nothing without a share', async () => {
    const register = vi.fn()
    const r = createSettledRegistrar({ shareId: () => null, items: () => [item('a', 'done')], register })
    expect(await r.flush()).toEqual([])
    expect(register).not.toHaveBeenCalled()
  })
})

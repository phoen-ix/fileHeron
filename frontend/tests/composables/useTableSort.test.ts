import { describe, expect, it } from 'vitest'

import { useTableSort } from '@/composables/useTableSort'

describe('useTableSort', () => {
  it('starts at the configured default', () => {
    const s = useTableSort({ defaultBy: 'created_at', defaultDir: 'desc' })
    expect(s.sortBy.value).toBe('created_at')
    expect(s.sortDir.value).toBe('desc')
    expect(s.indicator('created_at')).toBe('↓')
    expect(s.indicator('subject')).toBe('')
    expect(s.ariaSort('created_at')).toBe('descending')
    expect(s.ariaSort('subject')).toBe('none')
  })

  it('cycles asc → desc → off (reset to default)', () => {
    const s = useTableSort({ defaultBy: 'created_at', defaultDir: 'desc' })
    s.toggle('subject') // first click on a different column → asc
    expect(s.sortBy.value).toBe('subject')
    expect(s.sortDir.value).toBe('asc')

    s.toggle('subject') // asc → desc
    expect(s.sortDir.value).toBe('desc')

    s.toggle('subject') // desc → off → back to default
    expect(s.sortBy.value).toBe('created_at')
    expect(s.sortDir.value).toBe('desc')
  })

  it('switching columns starts asc', () => {
    const s = useTableSort({ defaultBy: 'created_at' })
    s.toggle('subject')
    expect(s.sortDir.value).toBe('asc')
    s.toggle('size') // change column → asc again
    expect(s.sortBy.value).toBe('size')
    expect(s.sortDir.value).toBe('asc')
  })

  it('reset() goes back to default', () => {
    const s = useTableSort({ defaultBy: 'created_at', defaultDir: 'desc' })
    s.toggle('subject')
    s.toggle('subject') // now subject desc
    s.reset()
    expect(s.sortBy.value).toBe('created_at')
    expect(s.sortDir.value).toBe('desc')
  })

  it('default direction defaults to desc', () => {
    const s = useTableSort({ defaultBy: 'created_at' })
    expect(s.sortDir.value).toBe('desc')
  })
})

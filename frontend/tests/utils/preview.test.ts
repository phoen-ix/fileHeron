import { describe, expect, it } from 'vitest'

import { previewKind, TEXT_PREVIEW_MAX_BYTES } from '@/utils/preview'

describe('previewKind', () => {
  it('maps raster images to image', () => {
    for (const m of ['image/png', 'image/jpeg', 'image/gif', 'image/webp']) {
      expect(previewKind(m)).toBe('image')
    }
    expect(previewKind('IMAGE/PNG')).toBe('image')
  })

  it('maps pdf and text', () => {
    expect(previewKind('application/pdf')).toBe('pdf')
    expect(previewKind('text/plain; charset=utf-8')).toBe('text')
    expect(previewKind('text/markdown')).toBe('text')
    // html is text-kind (rendered as source by the backend), never executed.
    expect(previewKind('text/html')).toBe('text')
  })

  it('refuses svg and arbitrary binaries (mirrors the backend allowlist)', () => {
    expect(previewKind('image/svg+xml')).toBeNull()
    expect(previewKind('application/zip')).toBeNull()
    expect(previewKind('application/octet-stream')).toBeNull()
    expect(previewKind(null)).toBeNull()
    expect(previewKind(undefined)).toBeNull()
    expect(previewKind('')).toBeNull()
  })

  it('exposes a positive text size cap', () => {
    expect(TEXT_PREVIEW_MAX_BYTES).toBeGreaterThan(0)
  })
})

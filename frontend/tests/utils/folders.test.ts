/* Folder uploads (v2.26.0): the folder a picked or dropped file came from, and
 * a listing grouped by folder without being reordered. */
import { describe, expect, it } from 'vitest'

import { folderOf, groupByFolder } from '@/utils/folders'

function fileAt(path: string, prop: 'webkitRelativePath' | 'relativePath'): File {
  const f = new File(['x'], path.split('/').pop() ?? 'x')
  Object.defineProperty(f, prop, { value: path })
  return f
}

describe('folderOf', () => {
  it('reads a folder picker path and an Uppy walker path the same way', () => {
    expect(folderOf(fileAt('Photos/2026/a.jpg', 'webkitRelativePath'))).toBe('Photos/2026')
    expect(folderOf(fileAt('Photos/2026/a.jpg', 'relativePath'))).toBe('Photos/2026')
    expect(folderOf(fileAt('/Photos/a.jpg', 'relativePath'))).toBe('Photos')
  })

  it('is null for a file picked or dropped on its own', () => {
    expect(folderOf(new File(['x'], 'a.jpg'))).toBeNull()
    expect(folderOf(fileAt('a.jpg', 'webkitRelativePath'))).toBeNull()
  })
})

describe('groupByFolder', () => {
  it('keeps the arrival order and puts each folder where it first appears', () => {
    const rows = [
      { id: '1', relative_dir: null },
      { id: '2', relative_dir: 'Docs' },
      { id: '3' },
      { id: '4', relative_dir: 'Docs' },
      { id: '5', relative_dir: 'Docs/Inner' },
    ]
    expect(groupByFolder(rows).map((g) => [g.dir, g.files.map((f) => f.id)])).toEqual([
      [null, ['1', '3']],
      ['Docs', ['2', '4']],
      ['Docs/Inner', ['5']],
    ])
  })
})

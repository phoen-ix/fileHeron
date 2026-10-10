/* Folder uploads (v2.26.0).
 *
 * A file that came from a dropped or picked folder carries the folder it was in
 * (`relative_dir`, e.g. 'Photos/2026'); null or absent means the top level. The
 * server stores only what utils/paths.py::safe_relative_dir leaves, and lists a
 * share's files top level first, then by folder and name. */

/** The folder part of a picked or dropped file's path, or null.
 *
 *  Uppy's folder walker (`getDroppedFiles`) sets `relativePath`, a
 *  `webkitdirectory` input sets `webkitRelativePath` - both like
 *  'docs/Prague/ticket.pdf', ending in the file's own name, which is dropped
 *  here. A file picked or dropped on its own has neither. The server cleans
 *  whatever arrives; this only finds it. */
export function folderOf(file: File): string | null {
  const path =
    (file as File & { relativePath?: string | null }).relativePath || file.webkitRelativePath || ''
  const parts = path.split(/[\\/]/).filter((p) => p !== '')
  parts.pop()
  return parts.length > 0 ? parts.join('/') : null
}

export interface FolderGroup<T> {
  dir: string | null
  files: T[]
}

/** Group a listing by folder, keeping the order it arrived in: the first time
 *  a folder appears decides where its group sits. */
export function groupByFolder<T extends { relative_dir?: string | null }>(
  files: readonly T[],
): FolderGroup<T>[] {
  const groups = new Map<string, FolderGroup<T>>()
  for (const f of files) {
    const dir = f.relative_dir || null
    const key = dir ?? ''
    let group = groups.get(key)
    if (!group) {
      group = { dir, files: [] }
      groups.set(key, group)
    }
    group.files.push(f)
  }
  return [...groups.values()]
}

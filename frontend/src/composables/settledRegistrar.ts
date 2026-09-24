import { settledFileIds, type UploadItem } from '@/composables/useUpload'

/**
 * Register each settled upload with the server exactly once.
 *
 * `POST /shares/{id}/files-added` is the batch-complete signal: the first call
 * announces the share, a later one sends recipients a "files added" follow-up.
 * Both views called it once per batch, so a file that finished LATER - a Retry
 * after a failure - was never registered: never announced, missing from the
 * share-level audit row, and (on the share page) missing from the list until a
 * reload. `flush()` sends only the ids not yet sent, and marks them only once
 * the server has accepted them, so a failed call leaves them for the next one.
 */
export function createSettledRegistrar(opts: {
  shareId: () => string | null
  items: () => readonly UploadItem[]
  register: (shareId: string, fileIds: string[]) => Promise<unknown>
}) {
  const sent = new Set<string>()
  let inFlight: Promise<string[]> | null = null

  async function send(): Promise<string[]> {
    const shareId = opts.shareId()
    if (!shareId) return []
    const fresh = settledFileIds(opts.items()).filter((id) => !sent.has(id))
    if (fresh.length === 0) return []
    await opts.register(shareId, fresh)
    fresh.forEach((id) => sent.add(id))
    return fresh
  }

  return {
    /** Registers what has settled since the last flush; rejects if the server does. */
    flush(): Promise<string[]> {
      // Serialise: a retry finishing while the first batch registers must not
      // send the same ids twice.
      const next = (inFlight ?? Promise.resolve([] as string[]))
        .catch(() => [] as string[])
        .then(send)
      inFlight = next
      return next
    },
    clear() {
      sent.clear()
    },
    get count() {
      return sent.size
    },
  }
}

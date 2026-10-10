import { settledFileIds, type UploadItem } from '@/composables/useUpload'

/** The most ids `POST /shares/{id}/files-added` takes in one call
 *  (schemas/share.py FilesAddedRequest). */
export const FILES_ADDED_MAX = 1000

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
  /** `last` is true for the final chunk of a flush: a caller that
   *  re-notifies recipients does it there, once, not per chunk. */
  register: (shareId: string, fileIds: string[], last: boolean) => Promise<unknown>
}) {
  const sent = new Set<string>()
  let inFlight: Promise<string[]> | null = null

  async function send(): Promise<string[]> {
    const shareId = opts.shareId()
    if (!shareId) return []
    const fresh = settledFileIds(opts.items()).filter((id) => !sent.has(id))
    if (fresh.length === 0) return []
    // The server takes FILES_ADDED_MAX ids per call, and a dropped folder
    // easily holds more: 1,001 settled files got a 422 for the whole batch,
    // so none were registered. Chunks go in order; each is marked only once
    // the server accepted it, so a failure leaves the rest for the next flush.
    for (let i = 0; i < fresh.length; i += FILES_ADDED_MAX) {
      const chunk = fresh.slice(i, i + FILES_ADDED_MAX)
      await opts.register(shareId, chunk, i + FILES_ADDED_MAX >= fresh.length)
      chunk.forEach((id) => sent.add(id))
    }
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

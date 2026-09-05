/* Upload orchestration for a single share.
 *
 * Two paths, one queue:
 *
 * 1. Files smaller than DIRECT_UPLOAD_THRESHOLD go through
 *    POST /api/uploads/direct (multipart, single round-trip, no resume).
 * 2. Larger files take the resumable path:
 *      POST /api/uploads/init → server returns a TUS endpoint + an
 *      HMAC-signed Upload-Metadata envelope. Uppy's Tus plugin opens
 *      the upload at that endpoint with that header; tusd verifies
 *      the envelope on pre-create.
 *
 * The composable exposes a flat list of items the UI iterates over -
 * the small/large split is invisible to the consumer.
 *
 * What this composable deliberately does NOT do:
 *   - It doesn't poll the server for finalization. tusd's post-finish
 *     hook runs after returning to the client, so upload-success is
 *     racy with file.state - but the ShareDetail page re-fetches the
 *     authoritative share+files on navigation, and that's where users
 *     read final state. Adding polling here would couple two layers. */
import Uppy, { type Body, type Meta, type UppyFile } from '@uppy/core'
import Tus from '@uppy/tus'
import { computed, onBeforeUnmount, ref, type Ref } from 'vue'

import { asEnvelope } from '@/api/client'
import { directUpload, initUpload } from '@/api/uploads'
import { useSiteStore } from '@/stores/site'

// Mirror backend's MAX_DIRECT_UPLOAD_BYTES default. Cheap-enough for the
// smallest VPS - files above this take the chunked path with resume. Build-time
// override (VITE_DIRECT_UPLOAD_THRESHOLD) so a deploy that lowered the backend
// limit can track it, and so e2e can force the resumable path with a tiny file.
// Empty/unset -> NaN/0 -> the 100 MB default.
const DIRECT_UPLOAD_THRESHOLD =
  Number(import.meta.env.VITE_DIRECT_UPLOAD_THRESHOLD) || 100 * 1024 * 1024
const TUS_CHUNK_BYTES = 8 * 1024 * 1024 // 8 MB chunks → balanced for big files + slow links
const TUS_RETRY_DELAYS = [0, 1000, 3000, 5000, 10000] // ms

export type UploadState =
  | 'queued'
  | 'preparing'
  | 'uploading'
  | 'finalizing'
  | 'done'
  | 'error'

export interface UploadItem {
  uid: string
  file: File
  state: UploadState
  progress: number
  fileId: string | null
  error: string | null
  /** Backend error code (e.g. QUOTA_EXCEEDED) when the failure carried one, so
   *  the view can render a localized string instead of the server's English
   *  text. The composable stays i18n-free; only the key travels. */
  errorCode: string | null
  bytesUploaded: number
}

// One timestamped line in the per-file activity log surfaced on the
// upload-progress screen. We store an i18n key + params (not a rendered
// string) so the log stays reactive to a mid-session locale switch and the
// composable stays free of any i18n dependency.
export interface LogEntry {
  id: string
  ts: number // client wall-clock epoch ms (Date.now())
  uid: string
  fileName: string
  kind: 'queued' | 'started' | 'finalizing' | 'done' | 'error'
  messageKey: string
  params?: Record<string, unknown>
}

// Local meta we attach so the Tus plugin can pull per-file headers off
// the Uppy file object. Index signature is required by Uppy v4's `Meta`
// constraint (Record<string, unknown>).
interface FileMeta {
  uid: string
  uploadMetadataHeader: string
  fileId: string
  [key: string]: unknown
}

let uidCounter = 0
const nextUid = () => `u${++uidCounter}_${Date.now().toString(36)}`

/** The ids of the items whose bytes have landed, for the batch-complete call.
 *
 *  `finalizing` counts: the file is on tusd's disk and only the 800 ms
 *  cosmetic timer above separates it from `done`. ShareCreate filtered on
 *  `done` alone while its "all uploads done" check (and ShareDetail's add-files
 *  panel) counted `finalizing`, so a share whose files all went through tus
 *  reported the batch with `file_ids: []` - an audit row saying zero files were
 *  added to a share that had just received all of them. */
export function settledFileIds(items: readonly UploadItem[]): string[] {
  return items
    .filter((i) => i.fileId && (i.state === 'done' || i.state === 'finalizing'))
    .map((i) => i.fileId as string)
}

export function useUpload(shareId: Ref<string | null>) {
  const site = useSiteStore()
  const items = ref<UploadItem[]>([])
  const log = ref<LogEntry[]>([])
  let logCounter = 0

  function pushLog(
    item: UploadItem,
    kind: LogEntry['kind'],
    params?: Record<string, unknown>,
  ) {
    log.value.push({
      id: `l${++logCounter}`,
      ts: Date.now(),
      uid: item.uid,
      fileName: item.file.name,
      kind,
      messageKey: `share_create.progress.log.${kind}`,
      params,
    })
  }

  const uppy = new Uppy<FileMeta, Record<string, never>>({
    autoProceed: false,
    allowMultipleUploadBatches: true,
  }).use(Tus, {
    endpoint: '/uploads/', // Vite/Traefik proxies to tusd
    chunkSize: TUS_CHUNK_BYTES,
    retryDelays: TUS_RETRY_DELAYS,
    removeFingerprintOnSuccess: true,
    // Per-file Upload-Metadata: the HMAC envelope tusd's pre-create hook
    // verifies.
    headers: (file: UppyFile<Meta, Body>): Record<string, string> => {
      const meta = file.meta as unknown as FileMeta | undefined
      return meta?.uploadMetadataHeader
        ? { 'Upload-Metadata': meta.uploadMetadataHeader }
        : {}
    },
  })

  function findItemByUppyId(uppyFileId: string): UploadItem | undefined {
    const file = uppy.getFile(uppyFileId)
    const uid = (file?.meta as FileMeta | undefined)?.uid
    return uid ? items.value.find((i) => i.uid === uid) : undefined
  }

  uppy.on('upload-progress', (uppyFile, progress) => {
    if (!uppyFile) return
    const item = findItemByUppyId(uppyFile.id)
    if (!item) return
    item.bytesUploaded = progress.bytesUploaded ?? 0
    if (progress.bytesTotal) {
      item.progress = Math.min(
        99,
        Math.round((progress.bytesUploaded / progress.bytesTotal) * 100),
      )
    }
    if (item.state === 'preparing') item.state = 'uploading'
  })

  uppy.on('upload-success', (uppyFile) => {
    if (!uppyFile) return
    const item = findItemByUppyId(uppyFile.id)
    if (!item) return
    item.progress = 100
    item.bytesUploaded = item.file.size
    // tusd post-finish runs server-side after returning here, so we
    // briefly show 'finalizing' before the next view re-fetches the
    // share and shows authoritative state.
    item.state = 'finalizing'
    pushLog(item, 'finalizing')
    setTimeout(() => {
      if (item.state === 'finalizing') {
        item.state = 'done'
        pushLog(item, 'done')
      }
    }, 800)
  })

  uppy.on('upload-error', (uppyFile, err) => {
    if (!uppyFile) return
    const item = findItemByUppyId(uppyFile.id)
    if (!item) return
    item.state = 'error'
    item.error = err?.message ?? null
    item.errorCode = 'UPLOAD_FAILED'
    pushLog(item, 'error', { error: item.error ?? 'UPLOAD_FAILED' })
  })

  function add(files: File[]) {
    for (const f of files) {
      const item: UploadItem = {
        uid: nextUid(),
        file: f,
        state: 'queued',
        progress: 0,
        fileId: null,
        error: null,
        errorCode: null,
        bytesUploaded: 0,
      }
      items.value.push(item)
      pushLog(item, 'queued')
    }
  }

  async function startItem(item: UploadItem) {
    if (!shareId.value) {
      // Was the literal developer string "no share id", rendered verbatim into
      // the user's file list (audit 2026-07-30, fe-i18n-a11y-5).
      item.state = 'error'
      item.error = null
      item.errorCode = 'UPLOAD_NOT_READY'
      return
    }
    if (item.state !== 'queued') return
    item.state = 'preparing'

    try {
      // The LIVE server ceiling when the config endpoint has supplied one -
      // the build-time constant is only the fallback. An admin lowering
      // `uploads.max_direct_bytes` used to make every file between the new cap
      // and 100 MB stream in full and then fail, repeatably (audit #2).
      const directLimit = site.maxDirectUploadBytes || DIRECT_UPLOAD_THRESHOLD
      if (item.file.size < directLimit) {
        // Direct path: one POST, server-managed progress estimation.
        pushLog(item, 'started')
        const { data } = await directUpload(
          shareId.value,
          item.file,
          (n) => {
            item.progress = Math.min(99, n)
            item.bytesUploaded = Math.round((item.file.size * n) / 100)
            if (item.state === 'preparing') item.state = 'uploading'
          },
        )
        item.fileId = data.file_id
        item.progress = 100
        item.bytesUploaded = item.file.size
        item.state = 'done'
        pushLog(item, 'done')
        return
      }

      // Resumable path.
      const { data } = await initUpload({
        share_id: shareId.value,
        filename: item.file.name,
        size_bytes: item.file.size,
        mime_type: item.file.type || 'application/octet-stream',
      })
      item.fileId = data.file_id

      const uppyId = uppy.addFile({
        name: item.file.name,
        type: item.file.type || 'application/octet-stream',
        data: item.file,
        meta: {
          uid: item.uid,
          uploadMetadataHeader: data.upload_metadata_header,
          fileId: data.file_id,
        },
      })

      item.state = 'uploading'
      pushLog(item, 'started')
      // Uppy upload returns when this batch completes; per-file
      // success/error already routed through event handlers.
      await uppy.upload()
      // Drop the file from Uppy's internal tracking so a retry can
      // re-add cleanly.
      try {
        uppy.removeFile(uppyId)
      } catch {
        /* ignore - already removed on success */
      }
    } catch (err) {
      // Prefer the API error envelope (e.g. QUOTA_EXCEEDED / MAINTENANCE_MODE /
      // DIRECT_UPLOAD_TOO_LARGE) over axios's generic "Request failed with
      // status code NNN"; keep this composable i18n-free (views localize).
      const env = asEnvelope(err)
      const msg =
        env?.error ??
        (err instanceof Error ? err.message : typeof err === 'string' ? err : null)
      item.state = 'error'
      item.error = msg
      item.errorCode = env?.code ?? 'UPLOAD_FAILED'
      pushLog(item, 'error', { error: msg ?? item.errorCode })
    }
  }

  async function start() {
    // Run sequentially to keep tusd hook pressure predictable on small
    // VPSes. If a particular deploy needs throughput, switching to
    // parallel is a one-line change.
    for (const item of items.value) {
      if (item.state === 'queued') await startItem(item)
    }
  }

  function remove(uid: string) {
    const idx = items.value.findIndex((i) => i.uid === uid)
    if (idx === -1) return
    // If the file is mid-flight in Uppy, remove it there too.
    const uppyFile = uppy
      .getFiles()
      .find((f: UppyFile<FileMeta, Record<string, never>>) => {
        const meta = f.meta as FileMeta | undefined
        return meta?.uid === uid
      })
    if (uppyFile) {
      try {
        uppy.removeFile(uppyFile.id)
      } catch {
        /* ignore */
      }
    }
    items.value.splice(idx, 1)
  }

  async function retry(uid: string) {
    const item = items.value.find((i) => i.uid === uid)
    if (!item) return
    item.state = 'queued'
    item.progress = 0
    item.bytesUploaded = 0
    item.error = null
    item.errorCode = null
    pushLog(item, 'queued')
    await startItem(item)
  }

  // Clear everything for a fresh "create another" round. The Uppy instance
  // is kept alive (cancelAll, not close) since the composable persists.
  function reset() {
    try {
      uppy.cancelAll()
    } catch {
      /* ignore */
    }
    items.value = []
    log.value = []
  }

  const isActive = computed(() =>
    items.value.some((i) =>
      ['preparing', 'uploading', 'finalizing'].includes(i.state),
    ),
  )

  const allDone = computed(
    () => items.value.length > 0 && items.value.every((i) => i.state === 'done'),
  )

  const totalBytes = computed(() =>
    items.value.reduce((acc, i) => acc + i.file.size, 0),
  )
  const uploadedBytes = computed(() =>
    items.value.reduce((acc, i) => acc + i.bytesUploaded, 0),
  )

  onBeforeUnmount(() => {
    try {
      uppy.cancelAll()
    } catch {
      /* ignore */
    }
  })

  return {
    items,
    log,
    add,
    start,
    remove,
    retry,
    reset,
    isActive,
    allDone,
    totalBytes,
    uploadedBytes,
  }
}

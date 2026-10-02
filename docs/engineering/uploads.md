# Uploads

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/routers/{uploads,tus_hooks}.py`, `backend/app/services/{tus_hooks,tus_signing,upload_liveness,quota,file}.py`, `backend/app/workers/{cleanup_stale_uploads,cleanup_abandoned_uploads,quota_reconcile}.py`, `frontend/src/composables/useUpload.ts`, `client/src/fileheron_client/tus.py`, `client/src/fileheron_client/ui/upload_worker.py`, the `tusd` service in `docker-compose.yml`

## Uploads

→ README §Upload pipeline for the diagram. Flow: `POST /api/uploads/init` (HMAC
envelope, `files` row `state=uploading`) → tusd `POST /uploads/` with
`Upload-Metadata: fh_payload + fh_sig` → pre-create hook → post-finish hook
finalises into `./data/files/yyyy/mm/<uuid>.bin`, `state=ready_unscanned`,
enqueue `av_scan_file`.

- **HMAC envelope** signed under `TUS_HOOK_SECRET` - tusd can't mint it; backend re-HMACs every hook. `/api/internal/*` is also Traefik-denied + optional `TUS_HOOK_ALLOWED_IPS`. `tus_upload_id` regex `^[A-Za-z0-9_-]{1,64}$` with `fullmatch` (`tus_hooks.py::_check_tus_upload_id`; `files.tus_upload_id` is String(64), and `.match` let a trailing `\n` through).
- **Finalize uses `shutil.move`** (rename fast path, else copy2+unlink). **Don't switch back to `os.rename`** - bind mounts appear cross-device in containers and it raises `EXDEV`. The copy fallback is also why finalize must not run on the event loop.
- **Direct upload** `POST /api/uploads/direct` (≤ `MAX_DIRECT_UPLOAD_BYTES`, default 100 MB) - single multipart, skips tusd. Browser (`composables/useUpload.ts`): <100 MB direct, ≥100 MB init + Uppy/`@uppy/tus`.
- **Quota:** per-user `users.quota_bytes` (NULL = unlimited), reserved at pre-create via Redis Lua, released on revoke/quarantine/delete. Redis counter = fast **enforcement** (reconciled hourly, floors at 0); for **display** use `quota.storage_used_bytes[_bulk]` (DB SUM), never the counter. Quota is a fairness control, not a hard cap - on a Redis outage the upload is allowed through.
- **`_initialize_from_db` takes `exclude_file_id`.** `uploading` is in `STORED_STATES` and the tus row is committed before pre-create reserves against it, so without the exclusion the seed and the INCRBY each charge the same file. `reserve_bytes_once` clears its marker when the reservation raises; leaving it set let the next pre-create skip the charge entirely (unmetered upload).
- **Deferred-length tus uploads are refused** (`DEFERRED_LENGTH_REFUSED`) and pre-create requires `announced_size == max_size`, not `<=`. `Upload-Defer-Length: 1` announced Size=0, sailed through, then PATCHed an arbitrary length against ONE authorised row - no hook fires on a PATCH, and tusd carried no `-max-size` (now set, 1 TiB, mirroring `MAX_DECLARED_UPLOAD_BYTES`). `refuse_if_critical_low` cannot throttle that: it reads a kv flag written by the HOURLY `disk_check` cron, not a live stat.

API tokens and token scopes: CLAUDE.md §Roles, permissions & security patterns. Recipient search: `docs/engineering/shares.md`.

### Upload liveness

- **`files.created_at` is stamped at `/api/uploads/init`, BEFORE the first byte, and never refreshed** - any predicate built on it measures "time since the upload started", never "is it still going". `cleanup_stale_uploads` did exactly that and reaped every transfer slower than `UPLOAD_STALE_AFTER_HOURS` (3) mid-flight, flipping the parent share to `failed` with reason `upload_abandoned` - at ~23 Mbit/s sustained for the 30 GB this product advertises. The three shares killed that way on the reference instance were deleted on 2026-09-13 (dumped first to `backups/failed-shares-removed_2026-09-13_191057.sql`; their `share_failed` audit rows were kept), so an empty `state='failed'` query there is not evidence it never happened. **`ShareState.failed` is terminal** - written in one place, no un-fail path.
- **The tusd `.info` sidecar's mtime is NOT a liveness signal** (`tusproject/tusd:v2.9.2`, measured): it is written at creation and finish only, so it tracks `created_at`, while the bare data file's mtime advances on every PATCH. Reading it "like `cleanup_abandoned_uploads` does" reproduces the bug with a different clock.
- **tusd does NOT supply `Event.Upload.ID` on pre-create** - measured, it is `""`. A comment in `tus_hooks.py` asserted the opposite, so a fix written on that premise left `tus_upload_id` NULL for the whole transfer and made `cleanup_abandoned_uploads`' live-upload guard (`tus_upload_id == <id> AND state == uploading`) unmatchable by any live upload. **post-receive is the first hook that carries a real id**, and stamping it there is what makes that guard reachable.
- **Both sweepers, the drain counter and `expire_files` must share one definition** - `services/upload_liveness.py`. `expire_files` skips a share with a LIVE upload (v2.23.0): it used to expire it mid-transfer, the `uploading` row went `deleted`, tusd kept taking bytes (no hook per PATCH) and pre-finish refused a 20 GB upload after its last byte. A stale upload does not hold the share, so an abandoned one cannot keep it alive. They previously disagreed twice over: one read the admin-tunable window via `settings_registry.effective` and the other `config.settings` directly (raising the knob moved one and not the other), and both keyed on `created_at` (a long upload was "abandoned" to the reaper and invisible to the drain, which then let a maintenance restart land mid-transfer).
- **Readers COALESCE to `created_at`** so direct uploads (no tus id, no progress tick) and rows written before the column existed keep their old behaviour instead of becoming immortal.
- **post-receive must never raise and must stay cheap** - it fires per `-progress-hooks-interval` for the whole transfer, so an exception is a per-tick error storm and the default 1s would be one UPDATE per second per upload (hence 30s).
- **`cleanup_stale_uploads` must keep NO size filter** - it is the only automated rescan, and excluding a class of file makes `ready_unscanned` permanent for that class (every download 425s forever).
- **`config.py::UPLOAD_STALE_AFTER_HOURS` measures INACTIVITY since v2.12.0**, not upload duration - the older reading is what killed the three live transfers.

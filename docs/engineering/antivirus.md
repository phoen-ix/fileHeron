# Antivirus and quarantine

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/services/{av_scan,av_release,quarantine,quarantine_admin}.py`, `backend/app/workers/{av_scan,purge_old_quarantine}.py`, `backend/app/routers/admin/quarantine.py`, `docker/clamav/`

## Antivirus

→ README §Quarantine for the admin view.

- ClamAV = separate compose service: read-only `./data/files`, read-write `./data/quarantine`; signature DB in `clamav-defs` volume. `enqueue("av_scan_file", file_id)` from post-finish + direct upload; ARQ `scan_path` over TCP to clamd (shared mount → zero copy).
- **State machine:** `uploading → ready_unscanned → clean | infected → deleted`. Download codes: `425 SCAN_IN_PROGRESS`, `410 FILE_INFECTED`, `410 FILE_DELETED`.
- **Coverage stops at ~2 GiB and that is not configurable.** clamd clamps its own `MaxFileSize` to ~2 GiB (INT_MAX), so `clamd.conf`'s 30 G limits do not apply; larger files are served flagged `av_unscanned` rather than `clean`. **`AV_MAX_SCAN_BYTES` is clamped to `config.CLAMD_MAX_FILE_SIZE`** by a field_validator - `.env.example` shipped 30 GiB for four releases and `install.sh` copies it, so every fresh self-host recorded 2-30 GB files as `clean` with `av_unscanned=False`. Never "raise" this to match a clamd.conf value; clamd ignores its own.
- **The skip is keyed to `CLAMD_MAX_FILE_SIZE`, never to `AV_MAX_SCAN_BYTES`.** The tunable is a TRUST threshold applied *after* the verdict; keying the skip off it turns a documented knob into a silent AV off-switch, releasing an infected file above the value as `clean` instead of quarantining it. `av_scan_file` decides oversize **before scanning** and calls `_release_unscanned` (clean + `av_unscanned` + a `file_served_unscanned` audit row) - terminal on BOTH backends, because INSTREAM answers `error` for an oversize stream and `error` is not a state flip. Only safe because `size_bytes` cannot be claimed (tus pre-finish forces final == authorised size; direct upload records what it received) - don't relax either check.
- **`WorkerSettings.job_timeout` must stay above `av_scan.SOCKET_TIMEOUT_SEC`.** arq's 300s default cancelled slow scans before the socket ceiling could fire, and arq retries a CancelledError, so the file looped through the sweep forever. The `av_scan_file` retry backoff has to outlast a clamav COLD START (180s healthcheck budget), not a blip.
- **Quarantine** (`services/quarantine.py`): move to `${QUARANTINE_DIR}/{share_id}/{filename}`, set `infected`, revoke parent share, release quota, audit + notify (kv `quarantine.notify_admins` fans out to all admins). **Reversible** - infected bytes stay on disk indefinitely so admins can release/inspect/purge via `services/quarantine_admin.py`.
- **`file.hard_delete` refuses an `infected` row with `409 FILE_QUARANTINED` unless `allow_quarantined=True`.** The row's `storage_path` IS the quarantine copy (quarantine rewrites it), and every interactive delete - the share page, `/admin/file-history`, the user-detail file list - reached the helper and unlinked that copy under a plain `file_deleted` row, bypassing `quarantine_admin.purge` and its `file_quarantine_purged` receipt. Only right-to-erasure and the config-import identity purge opt in; the added-files reject loop skips infected rows.
- **`AV_SKIP=true`** marks every upload clean (CI/dev). Boot fail-fast refuses `production AND AV_SKIP=true`.

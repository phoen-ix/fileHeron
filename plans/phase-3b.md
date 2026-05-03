# Phase 3b — Upload UI (Uppy + share creation)

> Master plan: `REDACTED/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 3a being complete.

## Goal

Build the user-facing upload experience using Uppy (`@uppy/core` + `@uppy/tus` + `@uppy/vue`). Land the share-creation flow (single recipient for now, multi-recipient + groups in Phase 4). Add the API-tokens panel inside Account settings. Verify resumable upload end-to-end with a real >1 GB file in Chrome.

## Pre-phase decisions (resolve at session start)

1. **Uppy UI choice** — `@uppy/dashboard` (full pre-built UI, ~150 KB gzipped, complete with progress + file list + retry buttons) vs headless + custom Element Plus. *Default recommendation: headless + custom Element Plus for visual consistency, but Dashboard is faster.*
2. **Share-create UX** — single page with all options (recipient, expiry, message, files), or wizard (recipient → files → review)? *Default: single page; faster for power users.*
3. **Inbox/outbox visual difference** — same component with a filter, or two distinct views? *Default: shared component with a `box={inbox|outbox}` prop.*

## Acceptance criteria

- `/share/new` page lets an authenticated user pick: recipient (single user dropdown for now), expiry date, optional subject/message, drop one or more files.
- Drag-and-drop a 1 GB+ file → sees per-file progress bar → kill the network mid-upload → reconnect → upload resumes (Uppy + TUS handles this; we just have to not break it).
- After all files finish, share is `state=active` and visible at `/outbox`.
- `/inbox` lists shares the current user is on the recipient list of, with file list + download buttons.
- `/share/:id` shows full share detail: recipients (anonymized to display name), files, expiry countdown, sender, audit timeline of file accesses.
- API tokens panel in `/account` lets the user create + name + copy + revoke tokens; the plaintext is only shown once.
- `vitest run` green for `useUpload` composable + `RecipientPicker` (stub for now, real in P4).

## Files to create / modify

### Frontend — composables
- `frontend/src/composables/useUpload.ts` — Uppy wrapper. Configures `@uppy/tus` with `endpoint` + `headers: { 'Upload-Metadata': signed_metadata }`; handles `upload-success` / `upload-error` / `complete` events; emits a Vue ref of progress state.

### Frontend — API
- `frontend/src/api/uploads.ts` — `initUpload(...)`, `directUpload(file)`.
- `frontend/src/api/shares.ts` — `createShare(...)`, `listShares(box)`, `getShare(id)`, `deleteShare(id)`.
- `frontend/src/api/files.ts` — `downloadFile(id)` (returns blob URL or triggers browser download), `deleteFile(id)`.
- `frontend/src/api/apiTokens.ts` — list / create / revoke.

### Frontend — views
- `frontend/src/views/ShareCreate.vue` — recipient picker stub (single user dropdown using `/api/users` *which doesn't exist yet*; for now use an autocomplete that hits `/api/admin/users` if you're admin or a hardcoded list — better, defer until P4 user-search exists; for P3b accept a free-form email field that resolves to user by `email_hash`). Drop-zone (Element Plus `el-upload`-shaped or custom + Uppy headless), expiry datepicker, subject/message fields. Submit calls `createShare` then `initUpload` per file then triggers Uppy.
- `frontend/src/views/Inbox.vue` — list of shares where I'm a recipient.
- `frontend/src/views/Outbox.vue` — list of shares I created.
- `frontend/src/views/ShareDetail.vue` — share info, file list with size + download, audit timeline, delete (sender or admin).
- `frontend/src/views/Account.vue` — extend with API tokens panel.

### Frontend — components
- `frontend/src/components/FileUploadArea.vue` — drag-drop + file picker + per-file progress list; bound to `useUpload`.
- `frontend/src/components/FileRow.vue` — single file row with name, size (formatted), state badge, download/delete buttons.
- `frontend/src/components/RecipientPickerStub.vue` — single email input that resolves to a user via API (`POST /api/users/lookup`?). Replaced in P4 by real picker.
- `frontend/src/components/ApiTokenPanel.vue` — list, create-with-name dialog, one-time-display modal, revoke confirm.
- `frontend/src/components/ExpiryPicker.vue` — wraps Element Plus `el-date-picker` with sane min (now + 1h) and configurable max default (e.g., 30d).

### Frontend — i18n
- Extend `frontend/src/i18n/locales/{en,de}.json` with all upload + share + token strings.

### Backend — small additions to support frontend
- `backend/app/routers/users.py` (new) — `POST /api/users/lookup` (auth required) — body `{email}` → returns `{user_id, display_name}` if connected to current user (in P3b: only allow lookup of users the current user has shared a group with — but groups don't exist yet in P3b! Workaround: temporarily allow any-user lookup for the sender's role only, and tighten in P4 once `client_employee_connections` exists). Document this temp relaxation.

### Frontend — tests
- `frontend/tests/composables/useUpload.test.ts` — mock Uppy events, assert state transitions.
- `frontend/tests/views/ShareCreate.test.ts` — submit happy path with mocked APIs.

## DB migrations

None this phase.

## API endpoints (added this phase)

- `POST /api/users/lookup` (temporary; tightened in P4)

## Dependencies added

**npm:**
- runtime: `@uppy/core`, `@uppy/tus`, `@uppy/vue`, `@uppy/progress-bar`, `@uppy/file-input` (or `@uppy/dashboard` if we go that route), `@uppy/locales`
- dev: none new

## Risks / pitfalls

1. **Uppy + Vue 3 reactivity** — `@uppy/vue` works but Uppy emits events outside the reactive system. Wrap state in `ref()` and update on Uppy events; don't try to make Uppy itself reactive.
2. **TUS `Upload-Metadata` encoding** — values must be base64. The signed metadata blob from `/api/uploads/init` is already base64; ensure `useUpload` passes it through unmodified.
3. **Multi-file share** — call `/api/uploads/init` once per file (each file gets its own `file_id` + signed metadata), but link them to a single `share_id`. Sequencing matters; if any init fails, abort the share atomically (delete the draft `shares` row).
4. **Upload concurrency** — limit concurrent uploads (e.g., `Uppy({ limit: 2 })`) to avoid hammering tusd / browser network stack with 10 simultaneous 1 GB transfers.
5. **Resume across browser sessions** — Uppy uses localStorage for resume state by default. Make sure that's enabled (`tus({ removeFingerprintOnSuccess: false })` for cross-tab resume, or accept same-tab-only).
6. **Download memory blow-up** — when triggering download via `<a href>` direct to `/api/files/{id}/download`, the browser handles streaming; do NOT try to fetch as a blob into memory for big files.

## Verification

Manual e2e in Chrome:

```
1. Login as test user
2. Visit /share/new
3. Pick recipient (existing test user via lookup), set expiry +7d, drop a 2 GB file
4. Watch progress bar; at 30% disable wifi for 10s; reenable; watch resume
5. After complete, log out
6. Log in as recipient; visit /inbox; click share; click download; verify SHA-256 matches source
```

Automated:
```bash
cd frontend && npm run test
```

## Out of scope

- Multi-recipient + group recipients → **Phase 4**
- Public link UI → **Phase 5**
- Notifications for share-created → **Phase 6a**
- AV state badges in UI → **Phase 5**

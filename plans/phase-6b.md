# Phase 6b — In-app Notifications + Admin UIs

> Master plan: `/home/mk/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 6a being complete.

## Goal

In-app notifications: bell icon in header with unread count, dropdown list, mark-read/mark-all-read, real-time push via SSE. Notification preferences UI inside Account. Admin user-management UI (list/invite/edit/disable/promote/right-to-erasure). Admin audit-log viewer with filters + CSV export. Forced 2FA enforcement gate (`REQUIRE_2FA={admins,all}` env interrupts login flow until user enables).

## Pre-phase decisions

1. **SSE vs WebSocket vs polling** — SSE locked from master plan. Confirm: 60s connection, frontend reconnects on close with `Last-Event-Id`. *Yes.*
2. **Right-to-erasure scope** — when user X is erased, files X uploaded are hard-deleted; shares *sent to* X retain the file (sender's data) with recipient marked `[deleted]`. *Confirm.*
3. **REQUIRE_2FA on existing users at first deploy** — when env flips to `admins`, existing admins without TOTP must be flagged and forced through setup on next login. *Default: yes, set `users.requires_2fa_setup=true` on backend boot, and gate any non-2fa-setup endpoint until done.*
4. **Audit-log retention cleanup** — add a separate ARQ scheduled job for retention enforcement, or defer to Phase 8? *Default: defer to Phase 8.*

## Acceptance criteria

- Bell icon appears in `AppHeader` with unread count badge. Click → dropdown shows recent 10 notifications. Mark-as-read on click; mark-all-read button.
- New notifications pushed via SSE land in the bell within ~1 second without page reload.
- `/account/notifications` shows per-category preferences (email / in-app / both / off) with save button.
- Admin section visible only to `role=admin`:
  - `/admin/users` — list (search by hint + display name), filter by role, paginated. Bulk actions: invite (modal), force-password-reset, disable.
  - `/admin/users/:id` — detail with edit (display name, role, quota, disabled), force-password-reset button, right-to-erasure (with two-step confirm + file count summary).
  - `/admin/audit-log` — filterable (event_type, actor, target, date range), paginated. CSV export.
- `REQUIRE_2FA=admins` gate: an admin without TOTP enabled tries any privileged action → redirected to `/account/2fa/forced` page; once enabled, redirected to original target.
- pytest covers: notification CRUD, SSE event delivery, admin user management actions, right-to-erasure (file deletion + recipient anonymization), REQUIRE_2FA gate.
- vitest covers: bell store, notification dropdown, admin user list filters.

## Files to create / modify

### Backend — new services
- `backend/app/services/user_management.py` — `list_users(filter, page)`, `update_user(id, **fields)`, `force_password_reset(id)`, `disable_user(id)`, `enable_user(id)`.
- `backend/app/services/erasure.py` — `erase_user(user_id)`:
  1. Anonymize `users` row: `email_hash → 'erased:<random>'`, `email_hint → '[erased]'`, `display_name → '[erased]'`, `password_hash → ''`, `is_disabled → true`.
  2. Hard-delete every file uploaded by this user (`file.uploaded_by == user_id`): unlink from disk, `state=deleted`, audit.
  3. Anonymize recipient references: `share_recipients.recipient_user_id` left as-is (FK), but the `users` row is now `[erased]`.
  4. Audit row: `user_erased`.
- `backend/app/services/sse.py` — Redis pubsub backend; per-user channel `fh:sse:{user_id}`. Frontend subscribes via long-lived HTTP connection.

### Backend — extended services
- `backend/app/services/notification.py` — extend `send_notification` to publish SSE event to the user's Redis channel (in addition to inserting DB row + enqueueing email).

### Backend — extended middleware / dependencies
- `backend/app/dependencies.py` — `require_2fa_if_enforced(user)` raises if `REQUIRE_2FA=admins and user.role==admin and user.totp_disabled` OR `REQUIRE_2FA=all and user.totp_disabled`. Mounted on protected routes.

### Backend — new routers
- `backend/app/routers/notifications.py` — extend with:
  - `GET /api/notifications?unread=true&page=...&page_size=...`
  - `POST /api/notifications/{id}/read`
  - `POST /api/notifications/read-all`
  - `GET /api/notifications/stream` (SSE; closes after 60s, frontend reconnects)
- `backend/app/routers/admin.py` (new):
  - `GET /api/admin/users?q=...&role=...&page=...`
  - `POST /api/admin/users/invite` (was on `/api/account/invite`; admin variant accepts more fields)
  - `PATCH /api/admin/users/{id}` (role, quota, disabled, display_name)
  - `POST /api/admin/users/{id}/force-password-reset`
  - `POST /api/admin/users/{id}/erase` (right-to-erasure)
  - `GET /api/admin/audit-log?event_type=...&actor=...&target=...&from=...&to=...&page=...`
  - `GET /api/admin/audit-log/export.csv?...` (streams CSV)

### Backend — boot-time enforcement
- On startup, if `REQUIRE_2FA in {admins,all}`, set `users.requires_2fa_setup=true` for matching users without TOTP (idempotent).

### Backend — new tests
- `backend/tests/test_notifications_api.py`
- `backend/tests/test_sse.py` — pubsub round trip, reconnection behavior.
- `backend/tests/test_admin_users.py` — full CRUD + erasure.
- `backend/tests/test_2fa_enforcement.py` — admin without TOTP blocked, completes setup, regains access.
- `backend/tests/test_audit_export.py` — CSV correctness + filter combinations.

### Frontend — new
- `frontend/src/api/notifications.ts` — list, mark, stream (EventSource).
- `frontend/src/api/admin.ts` — users, audit log.
- `frontend/src/composables/useSSE.ts` — wraps EventSource with reconnect + Last-Event-Id.
- `frontend/src/stores/notifications.ts` — Pinia store: `notifications: []`, `unreadCount`, `connected: bool`.
- `frontend/src/components/NotificationBell.vue` — header dropdown.
- `frontend/src/components/NotificationItem.vue`.
- `frontend/src/components/NotificationPreferences.vue` — used in Account.
- `frontend/src/views/AdminUsers.vue` — list with filters + bulk actions.
- `frontend/src/views/AdminUserDetail.vue` — detail page with all admin actions.
- `frontend/src/views/AdminAuditLog.vue` — filter UI + paginated table + CSV export button.
- `frontend/src/views/TwoFactorForced.vue` — friendly "your account requires 2FA, set it up to continue" page.

### Frontend — extended
- `Layout.vue` — mount `NotificationBell` in header (only when authed). Subscribe to SSE on mount; unsubscribe on logout.
- `router/index.ts` — add `/admin/*` routes guarded by `requireAdmin: true`.

## DB migrations

1. `users_add_requires_2fa_setup`

## API endpoints (added this phase)

- `GET    /api/notifications?unread=true&page=...`
- `POST   /api/notifications/{id}/read`
- `POST   /api/notifications/read-all`
- `GET    /api/notifications/stream`
- `GET    /api/admin/users?q=&role=&page=`
- `POST   /api/admin/users/invite`
- `PATCH  /api/admin/users/{id}`
- `POST   /api/admin/users/{id}/force-password-reset`
- `POST   /api/admin/users/{id}/erase`
- `GET    /api/admin/audit-log?event_type=&actor=&from=&to=&page=`
- `GET    /api/admin/audit-log/export.csv`

## Frontend routes (added this phase)

- `/admin/users`
- `/admin/users/:id`
- `/admin/audit-log`
- `/account/2fa/forced`

## Dependencies added

**pip:** none (Redis pubsub is already in the redis lib).
**npm:** `@vueuse/integrations` (provides `useEventSource`) — or write 30 lines manually.

## Risks / pitfalls

1. **SSE behind Traefik** — must not buffer. Set response headers `Cache-Control: no-cache, X-Accel-Buffering: no`. Document in CLAUDE.md that Traefik labels should not include any buffering middleware.
2. **SSE backpressure** — close connection after 60s; client reconnects with `Last-Event-Id` to resume. Avoid keeping thousands of long-lived connections.
3. **Notification self-loop** — a "login from new device" email fires on login → audit log row → if we're not careful, *that audit row* could trigger another notification → loop. Whitelist the categories that trigger notifications; don't generic-trigger off `audit_log INSERT`.
4. **Right-to-erasure breadth** — clarify scope in confirm UI: total file count + total bytes about to be deleted. Allow admin to back out.
5. **REQUIRE_2FA migration** — at first deploy with `REQUIRE_2FA=admins`, existing admins must be flagged. Add idempotent startup task.
6. **CSV export of audit log** — large export should stream (`StreamingResponse` + generator) not buffer in memory.
7. **Admin actions need their own audit rows** — every admin action (invite, edit, erase) audits the *admin* as actor. Test this.

## Verification

```bash
# Real-time bell — open two browser tabs as the same user
# In tab A, trigger a share-created (so the user becomes recipient)
# In tab B, the bell badge increments without reload

# Right-to-erasure test (using an erasable test user)
curl -X POST .../api/admin/users/$ID/erase -H "Authorization: Bearer $ADMIN"
# verify in DB: user row anonymized; their files gone from disk; audit row present

# Force 2FA on admin who hasn't set up
docker compose exec backend bash -c "REQUIRE_2FA=admins python -m app.scripts.flip_require_2fa"
# admin tries to do anything → redirected to /account/2fa/forced

docker compose exec backend pytest -q
cd frontend && npm run test
```

## Out of scope

- OIDC employee SSO → **Phase 7**
- New-device login alert email → **Phase 7**
- HIBP password breach check → **Phase 7**
- WebAuthn → **Phase 8**

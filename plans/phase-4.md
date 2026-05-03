# Phase 4 — Groups, Recipients, Share Lifecycle

> Master plan: `REDACTED/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 3a/3b being complete.

## Goal

Land the full multi-recipient sharing model: groups (with optional `is_company_inbox` flag), group memberships, client↔employee connections derived from invites + shared-group membership. Replace the temporary single-recipient/lookup UI with a real recipient picker (multi-user + multi-group). Add the ARQ scheduled `expire_files` job (hourly cron, idempotent). Real groups CRUD in admin UI.

## Pre-phase decisions

1. **Inbound dual-targeting** — can a client target *both* a specific employee AND an inbox group in one share? *Default: yes-both.*
2. **Snapshot vs dynamic group membership** — locked as **dynamic** (revoking group membership immediately revokes access to past shares). Confirm UI warns the admin about this.
3. **Connection-source semantics** — `source` on `client_employee_connections` distinguishes `invite` vs `shared_group`. Default: never auto-delete `invite` connections; only `shared_group` connections disappear when no shared group remains.
4. **Group naming uniqueness** — case-insensitive unique? *Default: yes (lowercase index column).*

## Acceptance criteria

- Admin can create / edit / delete groups in `/admin/groups`. Each group has `name`, `description`, `is_company_inbox` flag.
- Admin can add/remove members via `/admin/groups/:id`.
- Real `RecipientPicker` component supports: typeahead user search (limited by connection scope), multi-select, inline group chips. Used by `ShareCreate` for both outbound (employee→client) and inbound (client→company).
- `client_employee_connections` rows auto-populate on: (a) invite consumption (sets `source=invite` for inviter↔invitee), (b) any new common-group membership (sets `source=shared_group`).
- Removing a user from a group also removes the `source=shared_group` row IF no other common group remains (raw SQL trigger or service-level enforcement).
- Group-targeted shares appear in inbox of every current member at the moment of inbox query (dynamic resolution, not a snapshot).
- Files-list filtering / search works in `/outbox` and `/inbox` (date range, recipient, state).
- ARQ scheduled job `expire_files` runs hourly: shares with `expires_at < now()` transition to `expired`, files hard-deleted from disk + `audit_log(file_expired)`. Idempotent on re-run.
- `pytest -q` green; new tests cover dynamic-membership share access, connection auto-population, expiry job behavior.

## Files to create / modify

### Backend — new models
- `backend/app/models/group.py` — `id`, `name` (unique, lowercase index), `description`, `is_company_inbox` bool, `created_at`, `created_by` FK→users.
- `backend/app/models/group_member.py` — `group_id` FK, `user_id` FK, `joined_at`, PK(group_id, user_id).
- `backend/app/models/client_employee_connection.py` — `client_user_id` FK, `employee_user_id` FK, `source` enum (`invite`, `shared_group`), `created_at`, PK(client_user_id, employee_user_id).

### Backend — new services
- `backend/app/services/group.py` — CRUD + member management. Trigger `_recompute_connections(user_id, group_id)` on add/remove.
- `backend/app/services/connection.py` — `record_invite_connection`, `recompute_shared_group_connections(user_a, user_b)`, `list_allowed_recipients(user)` (returns the union of connected users + inbox groups for clients, or all users + groups for employees).
- `backend/app/services/share.py` — extend: `create_outbound_share` accepts list of `recipient_user_ids` + `recipient_group_ids`; `create_inbound_share` enforces recipient is a connected employee or an inbox group.
- `backend/app/services/file.py` — extend: `delete_file_for_expiry(file_id)` (hard-delete from disk + audit + DB row marked `state=deleted`).

### Backend — workers
- `backend/app/workers/__init__.py` — ARQ setup.
- `backend/app/workers/worker.py` — `WorkerSettings(redis_settings, functions=[...], cron_jobs=[cron(hour={'*'}, minute={0}, name='expire_files')])`.
- `backend/app/workers/expire_files.py` — finds expired shares, calls `delete_file_for_expiry` for each file, transitions share to `expired`, audits.
- Compose adds a `worker` service: same image as backend, command `arq app.workers.worker.WorkerSettings`.

### Backend — extended routers
- `backend/app/routers/groups.py` — CRUD endpoints (admin only).
- `backend/app/routers/users.py` — replace temp `/lookup` with `/api/users/search?q=...` scoped to `list_allowed_recipients(current_user)`. Tighten the P3b temporary relaxation.
- `backend/app/routers/shares.py` — `POST /api/shares` now accepts `recipients: { user_ids: [...], group_ids: [...] }`.
- `backend/app/routers/account.py` — `GET /api/me/connections`, `GET /api/me/inbox-groups`.

### Backend — new tests
- `backend/tests/test_groups.py` — CRUD, uniqueness, member add/remove triggers connection updates.
- `backend/tests/test_connections.py` — invite-source persistent, shared-group-source dynamic, no double-counting.
- `backend/tests/test_share_lifecycle.py` — group share dynamically resolves recipient list; revoking membership immediately blocks future downloads.
- `backend/tests/test_expire_worker.py` — manually trigger worker, assert files gone + share state + audit.

### Frontend — new
- `frontend/src/api/groups.ts`, `frontend/src/api/users.ts`, `frontend/src/api/connections.ts`.
- `frontend/src/components/RecipientPicker.vue` — typeahead with mixed user + group results, distinguished visually.
- `frontend/src/components/GroupForm.vue` — create/edit form.
- `frontend/src/components/GroupMemberList.vue` — list with add/remove.
- `frontend/src/views/AdminGroups.vue` — list + create.
- `frontend/src/views/AdminGroupDetail.vue` — detail + member management.
- Replace `RecipientPickerStub` usages in `ShareCreate.vue` with the real component.
- Extend `Inbox.vue` / `Outbox.vue` with filter UI.

## DB migrations

1. `groups`
2. `group_members`
3. `client_employee_connections`
4. `share_recipients_recipient_group_id_fk` — make `share_recipients.recipient_group_id` a real FK to `groups`.
5. (optional) `users_lower_email_index` if desired for lookup.

## API endpoints (added this phase)

- `GET/POST/PATCH/DELETE /api/groups` (admin)
- `POST /api/groups/{id}/members` — body `{user_ids: []}`
- `DELETE /api/groups/{id}/members/{user_id}`
- `GET /api/users/search?q=...` (replaces temporary `/lookup`)
- `GET /api/me/connections` — returns allowed recipient targets
- `GET /api/me/inbox-groups` — returns inbox-flagged groups visible to current user

## Frontend routes

- `/admin/groups`
- `/admin/groups/:id`

## Dependencies added

None new (ARQ + Redis already in P1).

## Risks / pitfalls

1. **Connection auto-population correctness** — when removing the last shared group with a client, only the `source=shared_group` row should disappear; `source=invite` is sticky.
2. **Dynamic membership communication** — surface the implication clearly in admin UI: "Removing a user from this group will revoke their access to all past shares targeting it." Confirm dialog.
3. **ARQ vs APScheduler confusion** — ARQ's cron is configured via `cron_jobs` in `WorkerSettings`, not via a separate `arq.scheduler` package.
4. **Expiry job idempotency** — must be safe to run twice in the same hour. Check `share.state == 'active'` before transitioning; check file exists on disk before unlink (no exception if already gone).
5. **Group rename and history** — renaming a group should not orphan past shares. `share_recipients` references group by FK ID, not name; renames are safe. Document that group deletion is irreversible (cascades / blocks based on policy decision below).
6. **Group deletion semantics** — block deletion if there are active shares targeting it, or auto-revoke those shares? *Default: block (return 409); admin must revoke shares explicitly first.*

## Verification

```bash
# Create a group
curl -X POST .../api/groups -d '{"name":"customer-acme","description":"ACME Inc","is_company_inbox":false}'
# Add members
curl -X POST .../api/groups/1/members -d '{"user_ids":[2,3,4]}'

# Create a group share
curl -X POST .../api/shares -d '{"kind":"outbound","expires_at":"...","recipients":{"group_ids":[1]}}'

# Each member can see in /api/shares?box=inbox

# Remove user 2 from the group
curl -X DELETE .../api/groups/1/members/2
# User 2 can no longer see the share

# Trigger expiry job manually
docker compose exec worker arq app.workers.worker.WorkerSettings --queue-name=default --burst

docker compose exec backend pytest -q
```

## Out of scope

- ClamAV scan on upload → **Phase 5**
- Public links → **Phase 5**
- Notifications for share-created / share-expiring → **Phase 6a**
- Admin user-management UI (beyond groups) → **Phase 6b**

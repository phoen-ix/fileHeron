# Phase 6a — Email Worker + Templates + Preferences

> Master plan: `REDACTED/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 5 being complete.

## Goal

Production-grade email delivery: ARQ `send_email` worker with retry + exponential backoff + logs-fallback. HTML + text templates for every notification category, in DE + EN. Per-user notification preferences (email / in-app / both / off, per category). Hourly `share_expiring_24h_warning` cron job, idempotent via a column on `shares`.

## Pre-phase decisions

1. **HTML email engine** — hand-written tables (most robust across clients) vs MJML compiled at build time? *Default: hand-written tables for v1; small set of templates, simple visual.*
2. **Email sender domain & DKIM/SPF/DMARC** — out of scope code-wise; document in README the DNS records the operator needs.
3. **Bounce handling** — none in v1 (just log SMTP errors). Document as a known limitation.
4. **Localization fallback** — if user's locale has no template for a category, fall back to EN. *Default: yes.*

## Acceptance criteria

- Every notification source enqueues via `send_notification(user, category, payload)`:
  - share-created → recipient(s)
  - share-expiring-soon (24h before) → recipient + sender
  - public-link-downloaded → uploader (if `notify_on_download=true`)
  - account-created (invite consumed) → admin who invited
  - password-reset → user
  - 2FA-required (after `REQUIRE_2FA` enforcement) → user
  - login-from-new-device → user (deferred to Phase 7 actual sending; recording lands here)
- ARQ `send_email` task: aiosmtplib delivery, 3 retries with exponential backoff (1s, 5s, 30s); on hard failure logs and continues (not crash the worker).
- If `SMTP_HOST` is empty, log the rendered email body (subject + headers + body) to backend stdout in a clearly-marked block. Used for dev.
- Per-user preferences stored in `user_notification_preferences` table; UI not built yet (Phase 6b builds the UI). API endpoints for `GET / PUT preferences`.
- Cron job `share_expiring_24h` runs hourly. Finds shares with `expires_at` between now+24h and now+25h AND `expiring_notified_at IS NULL`. For each: enqueue notifications, then `UPDATE shares SET expiring_notified_at = now()` (idempotent — won't re-fire next hour).
- Templates render with the recipient's locale, falling back to EN. dayjs equivalent for backend date formatting (use `babel.dates.format_datetime` with locale).
- pytest covers retry behavior, logs-fallback, locale resolution, idempotency of expiry-warning job.

## Files to create / modify

### Backend — new models
- `backend/app/models/notification.py` — `id BIGINT`, `user_id` FK, `category` enum, `payload_json`, `created_at`, `read_at` (nullable), `link_url` (nullable). (Inserted but not surfaced in UI yet — Phase 6b builds the bell.)
- `backend/app/models/user_notification_preference.py` — `user_id` FK, `category` enum, `channel` enum (`email`, `in_app`, `both`, `off`), PK(user_id, category). Default channel = `both`.
- Migration: add `shares.expiring_notified_at TIMESTAMP NULL`.

### Backend — new services
- `backend/app/services/notification.py` — `send_notification(user, category, payload, request=None)`:
  1. Insert `notifications` row (always, regardless of pref).
  2. Look up user's preference for this category; if `email` or `both` and channel allowed, enqueue ARQ `send_email`.
  3. (Phase 6b will also push SSE event here.)
- `backend/app/services/email.py` — extend: `render_email(template_name, locale, payload)` returning `(subject, text, html)`; `enqueue_email_send(to, subject, text, html)`.
- `backend/app/services/audit.py` — no change.

### Backend — workers
- `backend/app/workers/send_email.py` — ARQ task with retry config: `Retry(defer=...)` exponential. Catches transient SMTP errors, logs + reraises for retry; permanent (550 etc.) → log + skip.
- `backend/app/workers/share_expiring.py` — ARQ task `share_expiring_24h_warning()` that runs the query + enqueues notifications + updates `expiring_notified_at`.
- `backend/app/workers/worker.py` — register both new tasks. Add cron job for `share_expiring_24h_warning`: hourly.

### Backend — extended routers
- `backend/app/routers/notifications.py` (new):
  - `GET /api/notifications/preferences`
  - `PUT /api/notifications/preferences` — body `{<category>: <channel>}`

### Backend — email templates
For each category × {de, en} × {html, txt}:
- `backend/app/templates/email/{en,de}/share_created.{html,txt}.j2`
- `backend/app/templates/email/{en,de}/share_expiring.{html,txt}.j2`
- `backend/app/templates/email/{en,de}/public_link_downloaded.{html,txt}.j2`
- `backend/app/templates/email/{en,de}/account_created.{html,txt}.j2`
- `backend/app/templates/email/{en,de}/password_reset.{html,txt}.j2`
- `backend/app/templates/email/{en,de}/2fa_required.{html,txt}.j2`
- `backend/app/templates/email/{en,de}/login_alert.{html,txt}.j2` (sent in Phase 7)
- Plus existing P1a stubs upgraded to HTML+text + DE.

Template structure:
```
{# layout.html.j2 — shared header/footer #}
<table cellpadding=0 cellspacing=0 border=0 width=600 align=center>
  <tr><td>{{ logo_or_app_name }}</td></tr>
  <tr><td>{% block content %}{% endblock %}</td></tr>
  <tr><td>{{ unsubscribe_link }}</td></tr>
</table>
```

### Backend — wire-up
- Hook each notification source:
  - `services/share.py:create_outbound_share` → `send_notification(recipient, "share_created", ...)` per recipient
  - `services/share.py:create_inbound_share` → same
  - `routers/auth.py:reset_password` → `send_notification(user, "password_reset", ...)` (already partially in P1a — make it use the queue now)
  - `routers/auth.py:register_from_invite` → `send_notification(invite.created_by, "account_created", ...)`
  - `routers/public.py:download` → if `link.notify_on_download`, `send_notification(link.created_by, "public_link_downloaded", ...)`
  - Phase 7 hooks `login_alert` here when triggered

### Backend — new tests
- `backend/tests/test_email_worker.py` — retry, logs-fallback, hard-fail handling.
- `backend/tests/test_notification_preferences.py` — pref CRUD, channel filtering.
- `backend/tests/test_share_expiring.py` — idempotency, time-window correctness.
- `backend/tests/test_locale_resolution.py` — DE user gets DE; unknown locale falls back to EN.

## DB migrations

1. `notifications`
2. `user_notification_preferences`
3. `shares_add_expiring_notified_at`

## API endpoints (added this phase)

- `GET /api/notifications/preferences`
- `PUT /api/notifications/preferences`

## Frontend

None this phase. Phase 6b builds the bell + preferences UI.

## Dependencies added

**pip:** `babel` (locale-aware date formatting in templates).
**npm:** none.

## Risks / pitfalls

1. **Email deliverability** — send-from-our-server without DKIM/SPF/DMARC will be classified as spam. Document the required DNS records in README; nothing to do in code.
2. **Retry storm** — a misconfigured SMTP server causing every email to fail will pile up retries. Cap retries at 3 with a "permanent failure → log + give up" path.
3. **Template injection** — render with `autoescape=True`. Never `{{ payload | safe }}` unless the source is trusted.
4. **Expiry-warning idempotency** — the column `expiring_notified_at` is the durable flag; check it inside the same transaction as the update.
5. **Notification flood** — group share with 50 recipients triggers 50 emails. Acceptable for v1; future improvement is a digest, but not in scope.
6. **Time zone in templates** — render expiry timestamp in user's locale + their timezone (we don't store user TZ — render in UTC ISO with a "(UTC)" suffix in v1; consider adding `User.timezone` later).

## Verification

```bash
# Trigger a share-created email (Phase 4 share-create flow); without SMTP_HOST set, see logs:
docker compose logs backend | grep -A20 "EMAIL DEV"

# Check notification rows
docker compose exec db mysql -u... -e "SELECT category, COUNT(*) FROM notifications GROUP BY category"

# Trigger expiry-warning manually
docker compose exec worker python -c "
import asyncio; from app.workers.share_expiring import share_expiring_24h_warning
asyncio.run(share_expiring_24h_warning())
"

docker compose exec backend pytest -q
```

## Out of scope

- In-app notification UI / SSE → **Phase 6b**
- Admin user-management / audit-log UIs → **Phase 6b**
- New-device login alerts → **Phase 7**

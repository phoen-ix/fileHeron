# file:Heron v1.11.1

**Fix: SMTP test email timestamp.** The "Sent at" line in the test email
(Admin → Settings → Email → Send test) showed a raw UTC value like
`2026-06-05T09:26:47+00:00`. It now renders like every other file:Heron email —
24-hour time in your configured **site timezone**, with the zone label, e.g.
`Jun 5, 2026, 11:26:47 AM (Europe/Vienna)`.

Reminder: file:Heron uses a single, admin-set **site** timezone for all
human-facing timestamps (Admin → Settings → Site; default UTC) — there is no
per-user timezone. Set it there to see local time across the app and emails.

No `.env` change, no migration. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.11.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.11.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.11.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.11.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.11.1`

Click **Update** in `/admin/system` to roll forward.

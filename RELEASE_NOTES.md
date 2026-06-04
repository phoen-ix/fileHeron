# file:Heron v1.7.0

**Admins can now see and revoke every user's sessions.** Until now, session
visibility was self-service only — each person could see their own signed-in
devices on their Account page, but an admin had no way to audit who was signed
in across the organisation or to forcibly sign someone out (a departed
employee, a lost laptop, a suspicious device). This release adds a full admin
session view with one-click revoke.

## What's new

- **New "Sessions" admin page** (`Admin → Sessions`). A live, paginated list of
  every signed-in session across all users:
  - **User, device, IP, Started, Last active, Expires** per session.
  - **Search** by user name, email, or IP; **sort** by Started / Last active /
    Expires.
  - **Sorted by least-recently-active first by default** — the quickest way to
    spot stale or forgotten sessions ("hanging" devices).
  - **"Include expired/revoked"** toggle for forensics; revoked and expired
    rows are dimmed and tagged.
- **Revoke controls** — revoke a **single session** or **all sessions for one
  user**. Every revoke is written to the audit log as
  `refresh_token_admin_revoked` (with the target user + reason).
- **Sessions section on the user detail page** (`Admin → Users → <user>`) — the
  same list scoped to that one person, with per-device revoke and a
  "Revoke all" button.
- **Accurate "Last active" tracking.** Sessions now record a real
  `last_used_at` timestamp that advances each time the session is used, while
  `Started` keeps the original sign-in time. Your own Account page now shows
  "Last active" for each device too.

## Under the hood

- New `refresh_tokens.last_used_at` column. **A database migration runs
  automatically on update** and backfills existing sessions (no action
  required). The token-rotation path now threads the original sign-in time
  forward so "Started" and "Last active" are distinct, meaningful values.

No `.env` changes. No desktop-client change.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.7.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.7.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.7.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.7.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.7.0`

Click **Update** in `/admin/system` to roll forward.

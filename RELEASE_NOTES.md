# file:Heron v1.11.2

**Email timestamps are now 24-hour in every language.** English emails were
rendering times as 12-hour AM/PM (e.g. `Jun 5, 2026, 2:28:42 PM`); they now
match the German emails and the rest of the app — 24-hour with the timezone
label, e.g. `Jun 5, 2026, 14:28:42 (Europe/Vienna)`. Each language keeps its
own date style; only the time format changed (English only — German was already
24-hour).

Applies to every timestamp in outbound mail (share notices, expiry reminders,
login alerts, public-link downloads, and the SMTP test email).

No `.env` change, no migration. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.11.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.11.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.11.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.11.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.11.2`

Click **Update** in `/admin/system` to roll forward.

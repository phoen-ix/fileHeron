# file:Heron v1.10.2

**Small reliability + console-noise fixes.** No functional changes you'll see in
the UI; this removes some harmless-but-confusing browser-console output and makes
the live-notification stream recover better after a tab has been idle.

## What changed

- **Live notifications recover after a backgrounded tab.** The short-lived token
  the notification stream uses now lasts longer (5 min instead of 2), and the
  bell reconnects automatically when you switch back to a tab that had been idle
  — previously a long-backgrounded tab could stop receiving live updates until a
  page reload (and logged a stream `401` in the console).
- **Quieter sign-in on page load.** On a cold load the app now refreshes the
  session first and then loads your profile, instead of letting the first
  profile request fail with a `401` and retry. One fewer request, and no more
  stray `401` in devtools.
- **Removed a font-fallback warning.** Dropped an unused serif fallback
  ("Book Antiqua") from the display font stack, which Firefox (with
  resist-fingerprinting) logged a notice about. No visual change.

No `.env` change, no migration. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.10.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.10.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.10.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.10.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.10.2`

Click **Update** in `/admin/system` to roll forward.

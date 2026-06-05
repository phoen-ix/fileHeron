# file:Heron v1.14.0

**Resume interrupted downloads in the browser.** A large download that drops
partway — flaky Wi‑Fi, a closed laptop lid, a VPN blip — could not be resumed
from the browser; it always started over. The server already supports HTTP range
requests, so browsers *can* resume a failed download from the downloads shelf —
the blocker was that the one‑time download link expired after just 60 seconds,
so by the time you hit **Resume** the link was dead.

This release makes that window **admin-configurable** so browser resume actually
works:

- New **Settings → Advanced → Downloads** knob, *"Signed download‑URL lifetime
  (seconds)"*, defaulting to **15 minutes** (was a fixed 60 s). Within that
  window, a browser's built‑in **Resume** picks up where the transfer left off
  instead of re‑downloading the whole file.
- It's a trade‑off you control: a longer lifetime makes resume more forgiving
  (handy for big files on slow links); a shorter one shrinks the window in which
  a download link, if it ever leaked into a proxy/access log, would still work.
  Tune it to your environment (30 s – 24 h).

### Good to know

- This is for **browser** downloads. The browser decides whether to offer
  Resume (it generally does for network interruptions, not for downloads you
  cancel yourself), and there's no in‑page Pause button — that's a browser
  limitation. **True pause/resume controls live in the desktop client** — see
  its **0.11.0** release, published alongside this one.
- Nothing else changes: existing links, the desktop client (which uses a bearer
  token, unaffected by this lifetime), and all current behaviour are untouched.

### Upgrade notes

- **No `.env` change, no migration.** The new setting defaults to 15 minutes; if
  you'd rather keep the old tighter 60‑second window, set it under
  **Settings → Advanced → Downloads**.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.14.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.14.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.14.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.14.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.14.0`

Click **Update** in `/admin/system` to roll forward.

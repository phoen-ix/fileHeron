# file:Heron v1.20.0

**Anomaly detection.** file:Heron now watches for *patterns* that the existing
controls (lockout, rate-limit, new-device alerts) can't see — likely
stolen-session or token abuse and bulk exfiltration — and alerts an admin when
something looks off. It's advisory: it raises a flag, it never blocks anyone.

## What's new

An hourly background scan looks for three things, all from data file:Heron already
keeps (no new dependency, no external service, fully air-gapped-friendly):

- **Mass download** — one account downloading an unusually large number of files
  in a short window (possible exfiltration).
- **Token used from many networks** — one account's downloads coming from several
  distinct networks at once (a strong hint a session or API token was stolen).
- **Credential stuffing** — a flood of failed logins from a single source spread
  across many different accounts (which per-account lockout doesn't catch).

When something trips a threshold, every admin gets an **operational alert** (bell +
email), an `anomaly_detected` entry is written to the **audit log**, and any
**webhook** subscribed to that event is notified.

## Good to know

- **Alerts only — never auto-blocks.** These are heuristics meant to put a human in
  the loop, not to lock anyone out.
- **Tunable + a kill switch.** All three thresholds, and a master on/off, live under
  **Settings → Advanced** (the *anomaly* group). Repeated alerts for the same subject
  are de-duplicated to once per hour.
- **Privacy-respecting.** Detection uses coarse network fingerprints, not real
  geolocation — so there's no IP-location database and no third-party lookups.
- **Admins only.** No change for employees or clients.

## Upgrade notes

- **No database migration and no `.env` change.** Detection is on by default with
  conservative thresholds; tune or disable it any time under Settings → Advanced.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.20.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.20.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.20.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.20.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.20.0`

Click **Update** in `/admin/system` to roll forward.

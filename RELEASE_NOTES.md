# file:Heron v1.16.0

**A batch of small, high-leverage improvements — find shares faster, share
public links by QR, and run the server with real visibility and guardrails.**
Nothing here changes how sharing works; it sharpens the everyday edges and gives
operators the signals they've been missing.

## For everyone

- **Search your shares by subject.** The Outbox and Inbox now have a search box
  that filters by subject as you type. No more paging through long lists to find
  "Q3 budget" or "invoice 2024" — it works alongside the existing state and
  recipient/sender filters.
- **QR code for every public link.** When you create or view a public link, you
  now get a scannable QR alongside the URL, with a **Download as PNG** button.
  Hand it to someone in person, drop it in a slide, or print it — they scan and
  land on the download page. The QR encodes only the public URL, nothing secret.

## For operators

- **Prometheus metrics endpoint.** A new `GET /api/metrics` exposes
  storage used / free / total, user and share counts, clean vs. quarantined
  files, and DB / Redis / ClamAV health as standard Prometheus gauges — ready to
  wire into Grafana or your alerting stack. It's access-controlled: a request
  must carry a scraper bearer token (`METRICS_BEARER_TOKEN`) **or** come from an
  allow-listed IP (`METRICS_ALLOWED_IPS`); with neither set the endpoint stays
  closed. Results are cached for 60s so frequent scrapes don't load the database.
- **Low-disk protection.** A full storage volume used to fail uploads with an
  opaque server error and leave quota reservations stranded. Now an hourly check
  watches free space; when it crosses the threshold the server **refuses new
  uploads cleanly (HTTP 507)** and sends every admin an in-app **ops alert** —
  while **downloads keep working** so recipients are never cut off. Both
  thresholds (percent-free and bytes-free) are tunable live under
  **Settings → Advanced**.
- **Database connection-pool tuning.** Pool size, overflow, and timeout are now
  configurable (`DB_POOL_SIZE` / `DB_POOL_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_SEC`,
  default 10 + 20), and `GET /api/health` now reports live pool stats (size,
  overflow, checked-out, query latency) so connection pressure is visible before
  it turns into slowness.

### Good to know

- **Audit log lines now carry the client IP** alongside the request ID, so
  central log aggregation can correlate an action to its source.
- The new disk-space check appears as `disk_check` in the **System → Cron** table
  and can be run on demand like any other job.
- New interface strings (the subject search box, the QR labels) ship in English
  and German. **No behavioural change to sharing, links, or permissions.**

### Upgrade notes

- **No database migration and no required `.env` change.** The metrics endpoint
  stays closed until you set `METRICS_BEARER_TOKEN` or `METRICS_ALLOWED_IPS`; the
  disk thresholds and DB-pool sizes ship with safe defaults you can leave alone.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.16.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.16.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.16.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.16.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.16.0`

Click **Update** in `/admin/system` to roll forward.

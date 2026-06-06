# file:Heron v1.18.0

**A new Admin → Analytics dashboard.** Until now the admin shell could tell you
the system was *healthy*, but not how it was *being used*. This release adds a
trends-and-usage view so you can see storage growth, sharing activity, download
volume, scan outcomes, your biggest consumers, and who's about to run out of
quota — all in one place.

## What's new

- **Analytics page** under **Admin → Analytics** with a 7 / 30 / 90-day range
  selector and one-click **CSV export**:
  - **Storage used over time** — a growth line so you can plan capacity.
  - **Shares created**, **Downloads**, and **Quarantines** per day.
  - **Files by state** — clean vs. scanning vs. quarantined at a glance.
  - **Top uploaders** (by stored bytes) and **most-downloaded shares**.
  - **Quota warnings** — anyone over 90% of their limit, highlighted.

## Good to know

- **Live where it can be, snapshot only where it must be.** Every number except
  the storage-growth line is computed live, so it's always current. Storage
  history can't be reconstructed after files are deleted, so a tiny nightly job
  records one storage data-point per day; that one chart is labelled "as of last
  night." You can also trigger the job on demand from **Admin → System** to seed
  the first point immediately.
- **Charts are drawn natively** in the app's own style — no third-party chart
  library, no external resources, consistent with the rest of the interface.
- Available in English and German. **Admins only**; nothing changes for
  employees or clients.

## Upgrade notes

- **One small, automatic database migration** adds the `analytics_snapshots`
  table (a few rows per day). No `.env` change. Just click **Update**; the
  storage-growth chart fills in from the night after you upgrade (or sooner if
  you run the job manually from System).

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.18.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.18.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.18.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.18.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.18.0`

Click **Update** in `/admin/system` to roll forward.

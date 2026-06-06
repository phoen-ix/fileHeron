# file:Heron v1.28.0

**Scheduled tasks - every background job is now yours to tune.** A new admin page
lets you set how often each cron runs, pin a daily time for heavy jobs, or disable
any of them outright - no redeploy. All cron controls now live in one place.

## What's new

- **Scheduled tasks page** at *Admin -> System -> Scheduled tasks*: every background
  job (expiry, cleanups, history pruning, quota reconcile, IMAP fetch, update check,
  and more) listed with:
  - an **enable/disable** toggle,
  - a **schedule** you choose - *every N minutes* or *daily at a fixed time*,
  - live **status** (last run, last-24h success/failure) and **next run**,
  - a **Run now** button.
- **Daily times honour your site timezone**, so heavy housekeeping can be pinned to
  off-hours (e.g. 02:30 local).
- **One home for crons.** The cron table left the System page, and the polling
  controls left the Inbound mail and Updates settings pages - it's all on Scheduled
  tasks now. Those pages keep only their own config (mailbox connection; release API
  URL).

## Good to know

- **Nothing changes on upgrade** until you edit a schedule: every job keeps its
  previous cadence by default (hourly jobs hourly, daily housekeeping at its usual
  ~02:xx, IMAP every 5 minutes).
- Disabling a job stops its automatic runs; you can still trigger it with **Run now**.
- Changes take effect within a minute (a lightweight scheduler checks each job's
  configured cadence).
- Every schedule change is recorded in the audit log.

## Upgrade notes

- **No database migration.** Schedules are stored as settings.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.28.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.28.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.28.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.28.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.28.0`

Click **Update** in `/admin/system` to roll forward.

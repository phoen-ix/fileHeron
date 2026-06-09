# file:Heron v1.53.0

**A browsable error log, and alerts that can now cover client errors too.** The
error-alert feature added in v1.52 emailed admins about server errors but kept no
record you could look at - a deduplicated or throttled error simply vanished. This
release adds a real **Error log**: every captured error is stored and viewable
under Admin, filterable by code, status, source, and time. Logging and emailing
are now independent, and you can opt specific **4xx** client errors (like 429 rate
limits) into both.

## What's new

- **Error log page (Admin → System → Error log).** Every server error (HTTP 5xx)
  and failed scheduled task is recorded to a new table and listed newest-first.
  Filter by error code, HTTP status, source, or time range; click a row for full
  detail (exception, request id, acting user, signature); export the filtered view
  to CSV. Rows that also triggered an email are marked **emailed**.
- **Logging is separate from emailing.** The log captures *every* qualifying error
  even when the matching email was deduplicated, hit the hourly cap, or alerts are
  switched off entirely. The cooldown and cap now govern emails only - so a noisy
  error is still fully recorded while your inbox stays quiet.
- **Opt-in 4xx capture + alerts.** By default only 5xx and cron failures are
  handled. On the **Errors & alerts** settings page you can now turn on "record
  selected client errors (4xx)" with an allowlist of status codes (e.g. `429, 409`)
  and, optionally, email about those same codes. Noisy codes like 401/403/404/422
  are ignored unless you explicitly list them, so this can't flood the log or your
  inbox.
- **Retention.** Error-log entries are pruned daily; the window is admin-tunable
  (default 90 days, `0` keeps them forever).
- **Clearer 4xx emails.** When an alert is for a client error, the email now says
  "client error" rather than "server error".

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`.
- **One re-runnable database migration** adds the `error_log` table; it runs
  automatically when the backend restarts. No host step.
- Logging is on by default for 5xx and cron failures (it's lightweight and pruned).
  Email alerts remain **off** until you enable them - unchanged from v1.52.
- The old "Error alerts" settings page is now "Errors & alerts" and hosts both the
  logging and the email controls.
- Rolling back to a pre-v1.53 image is safe (the extra table is simply unused).

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.53.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.53.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.53.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.53.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.53.0`

Click **Update** in `/admin/system` to roll forward.

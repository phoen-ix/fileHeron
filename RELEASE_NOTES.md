# file:Heron v1.54.0

**The Error log now records the client IP.** Until now the log captured the path,
status, and code of each error but not *who* sent it - which is the one field you
need to spot and block a scan. This adds the real client IP to every error row,
makes it filterable, includes it in the CSV export and the alert email, and moves
the Error log next to its settings in the sidebar.

## What's new

- **Client IP on every error.** Each error-log row now stores the real client IP
  (proxy-resolved, IPv6-capable), shown as a column in **Admin → Error log** and in
  the row detail. A vuln scan now reads as a burst of bogus 404s from one IP.
- **Filter by IP.** A new IP filter on the Error log isolates every hit from a
  given address - one click to see everything a scanner touched. The IP is also in
  the CSV export.
- **IP in the alert email.** The server-error email gains a "from IP" line.
- **Sidebar tidy-up.** The Error log now sits directly beneath **Errors & alerts**
  (it was up by the Audit log), so the log and its settings live together.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. **One re-runnable migration**
  adds the `error_log.ip` column (+ an index); it runs on backend restart. No host
  step.
- Rows created before this release have no IP (it wasn't captured then) and show
  `-`; everything new gets it.
- IP is treated like the existing login-attempt log: stored in plain text, bounded
  by the Error-log retention window.
- Rolling back to v1.53.3 is safe (the extra column is simply unused).

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.54.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.54.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.54.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.54.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.54.0`

Click **Update** in `/admin/system` to roll forward.

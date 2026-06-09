# file:Heron v1.55.1

**Capture more of a scan, and make the ceiling tunable.** The error log front-guard
that bounds how many 4xx events are recorded per minute was a hard-coded 10/min, so
during a probe storm the log only sampled. The default is now **300/min** (matching
the per-IP edge rate limit) and it's an **admin knob** - so you decide the
scan-visibility vs database-volume tradeoff without a release.

## What's new

- **Higher default 4xx capture rate.** 10/min → **300/min** globally - a scan now
  shows up far more completely in the Error log out of the box.
- **New tunable** on **Settings → Advanced → Errors & alerts**: "Max 4xx errors
  logged per minute" (10-12000). The matching edge nginx throttle still sheds extreme
  per-IP floods first; this bounds the worst-case log-write rate.
- **Tidier Advanced page**: the error-alert cooldown / max-per-hour and error-log
  retention knobs now have proper labels (they previously showed raw setting keys).

## Notes

- This only governs *logging* throughput; the email cooldown + hourly cap are
  unchanged. 5xx capture stays at its own bound. Raising it past a few thousand/min
  on a busy instance will grow the `error_log` table faster - retention still prunes it.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Backend + frontend, **no
  migration, no host step**. Rolling back to v1.55.0 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.1`

Click **Update** in `/admin/system` to roll forward.

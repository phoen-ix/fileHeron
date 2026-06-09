# file:Heron v1.53.2

**See vulnerability scanning in the Error log.** Scanners probe for paths like
`/wp-login.php`, `/.env`, and `/.git/config`. Until now those hit the SPA fallback
and returned a harmless `200` with the app shell - so they never showed up
anywhere, and "lots of 404s" never actually rose. This release routes obviously
malicious probe paths to the backend so they return a real `404` and land in the
Error log, with the source IP - turning a scan into an obvious cluster of bogus
404s you can spot and block.

## What's new

- **Scanner-probe paths now 404 and get logged.** nginx sends requests for
  scanner-bait paths - common script/extension probes (`.php`, `.asp`, `.cgi`,
  `.sql`, `.bak`, `.env`, `.git`, `.aws`, key/cert files, …) and dotfiles
  (`/.env`, `/.git/config`, `/.aws/credentials`, except `/.well-known/`) - to the
  backend, which returns `404 NOT_FOUND`. With `404` in your **Errors & alerts →
  capture 4xx** allowlist, each one is recorded in the Error log as
  `404 / NOT_FOUND` with the attacker's IP and the probed path.
- **Real routes and assets are untouched.** SPA routes never contain a file
  extension or a dot-segment, so deep links, `/assets/*`, fonts, `favicon.ico`,
  `robots.txt`, and the like behave exactly as before.
- **Edge throttle.** A per-IP rate limit (5 req/s) sheds a scan flood at nginx so
  it can't hammer the backend; probes also stop receiving your app shell.

## How to use it

1. Update, then on **Errors & alerts** make sure **"record selected client errors
   (4xx)"** is on with `404` in the allowlist (you already set this up).
2. Watch **Admin → System → Error log**, filter status `404` - a scan shows up as
   a burst of bogus paths from one or a few IPs.

## Notes & limits

- During a heavy scan the log **samples** (the per-minute capture guard) - you'll
  see the pattern clearly, not necessarily every single probe.
- The probe list is a curated denylist of the usual suspects; it can be extended.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. The change is baked into the
  frontend image - **no host step, no migration.**
- Backend is unchanged; rolling back to v1.53.1 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.53.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.53.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.53.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.53.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.53.2`

Click **Update** in `/admin/system` to roll forward.

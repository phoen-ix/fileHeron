# file:Heron v2.1.0

**Production-hardening wave.** A deep audit + fix pass that closes real
correctness and security defects, makes the documented `.env` settings actually
take effect, and smooths first-time dev/prod setup. This is the release that
makes public self-hosting genuinely solid.

> **⚠️ This release needs a host step, not just the in-app Update.** It changes
> `docker-compose.yml` (see "Settings now apply" below), and the in-app updater
> only swaps the backend/worker/frontend images. Pull it on the host:
> `git pull && docker compose up -d`. No database migration.

## Settings now apply (important)

- **`docker-compose.yml` never forwarded your `.env`** beyond a curated handful
  of keys, so ~40 documented settings were silently inert - including
  **`WEBAUTHN_RP_ID`** (so passkeys couldn't work on your real hostname),
  `STORAGE_BACKEND`/`S3_*`, `METRICS_*`, `TUS_HOOK_ALLOWED_IPS`, and every
  retention / lockout / session / anomaly / error-log knob. It now injects the
  full `.env` (an `env_file`), verified against the running image. After the host
  step above, settings you put in `.env` take effect. `.env.example` is now the
  complete annotated reference it always claimed to be.

## Security fixes

- **Public link password:** a non-ASCII password (e.g. `Schlüssel`, `café`) made
  the *correct* password return a 500, permanently locking legitimate recipients
  out of the link (and let anyone flood 5xx un-throttled). Fixed.
- **Upload quota bypass:** a crafted deferred-length upload could release more
  quota than it reserved, letting a user store beyond their limit. Reserve and
  release are now symmetric on the authorised size.
- **OIDC:** the identity-provider discovery fetch had no response-size cap (a
  hostile IdP could exhaust a worker); now capped like the JWKS fetch.
- **Public-link brute force:** the attempt check + record are now serialized, so
  a concurrent burst can't slip past the rate limit.
- **JWT-secret rotation** now re-encrypts webhook signing secrets too (rotating
  previously broke all webhook deliveries).

## Reliability / correctness

- **GDPR erasure** commits each file deletion durably, so a mid-batch disk error
  can no longer leave rows pointing at bytes that are already gone; per-file audit
  events attribute the admin.
- **Quarantine** commits the infected + revoked state before the irreversible
  move, so a failure can't leave an infected file the DB thinks is clean.
- **Antivirus** fails safe (state `error`, not a crash) when clamd drops an
  oversize stream - which previously aborted the whole IMAP poll; the infected
  path no longer resurrects a file that expired mid-scan.
- **Inbound mail:** a Message-ID collision no longer deletes a distinct unread
  message from the server, and an unreadable UIDVALIDITY no longer triggers a
  full re-scan; a crafted RFC 2231 header no longer crashes classification.
- **S3 backend:** correct RFC 5987 download filenames, and transient S3 errors are
  handled backend-neutrally.
- Fixed a 500 when filtering the shares list by both a recipient user and group.

## Developer experience

- Fresh-clone dev setup works (`cp .env.example .env`, data-dir keepfiles, the
  data-dir ownership note), a fuller `make` (test/up/dev/build/seed), a new
  `CONTRIBUTING.md`, and several doc-drift corrections.

## Notes

- **Host step required** (compose change) - see the callout above. No migration.
- Known/accepted: a `Range: bytes=1-` continuation can finish an exhausted public
  download (deliberate, to not break resumed downloads); a full backend
  type-check gate is a scoped follow-up.

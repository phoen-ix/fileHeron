# file:Heron v2.0.0

**General availability.** This is the 2.0 milestone: file:Heron is now published
as a production-ready, self-hostable release. It is **not a breaking change** -
existing operators upgrade straight from v1.62.0 via the in-app Update with no
code or behavior changes - it is a version-marker release. No database
migration, no host step.

The major-version bump marks the stable, supported line rather than any runtime
change: the platform has been running hardened in production, and this release
formalizes the fresh-install, security-disclosure, and operator-hardening story
for people deploying it themselves.

## What's in the GA

- **Hardened installer.** `install.sh` now produces a production-ready `.env` on
  first run - `ENVIRONMENT=production` (secure cookies, `/docs` disabled, HSTS,
  fatal checks on weak/placeholder secrets), a `WEBAUTHN_RP_ID` derived from your
  URL, and no seeded dev account. Fresh boxes are safe by default.
- **Security policy.** A `SECURITY.md` with a private vulnerability-disclosure
  path (GitHub private reporting).
- **Operator hardening guide.** A consolidated "Production hardening checklist" in
  the README, a clear app-vs-infra upgrade boundary (which upgrades need a host
  step), and a shipped nightly-backup systemd timer alongside the weekly restore
  drill.
- **Desktop client.** Ships in lockstep as **client v1.0.0**. The Windows `.exe`
  is unsigned by design - the release now publishes a SHA-256 checksum, and the
  client README documents verification plus a build-and-self-sign path.

## Carried in from the recent line

- Web-app dependencies modernized to current majors (vue-router 5, Uppy 5,
  VueUse 14, vitest 4) with a resumable-upload proxy fix (v1.62.0).

## Notes

- **No migration, no host step** for existing operators - a plain in-app Update.
- Known, intentionally-deferred items: per-file envelope encryption (deferred
  while storage is single-server bind mounts); the desktop client has no
  auto-update and intentionally omits OIDC/WebAuthn/admin/SSE.

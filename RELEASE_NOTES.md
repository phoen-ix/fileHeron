# file:Heron v1.62.0

**Dependency modernization + upload hardening.** A maintenance release: the web
app moves to the current major versions of its core libraries (routing, the
upload engine, utilities, test tooling) and picks up a batch of minor and
security updates, plus a reverse-proxy fix for large-file uploads. No
admin-facing feature or workflow changes, no database migration, and no host
step - deploy from this banner.

## What's changed

- **Web-app libraries updated to their current majors** - the router, the
  resumable-upload engine (Uppy), the utility library (VueUse), and the test
  tooling, alongside routine minor/security bumps to HTTP, date, and i18n
  libraries and the CI actions. Behaviour is unchanged; this is upkeep and
  security hygiene so the frontend stays on supported, patched releases.
- **Large-file (resumable) uploads behind a non-standard port** - fixed an edge
  case where a resumable upload could fail when the app is served through a
  reverse proxy on a non-default port: the upload address was built without the
  port and the follow-up requests were refused. The standard HTTPS (:443) setup
  was never affected.

## Under the hood

- The browser upload engine now has end-to-end test coverage of the resumable
  path (previously only the direct path was exercised), so future upload-library
  upgrades are caught by CI.
- The direct-vs-resumable size threshold is now configurable at image-build time
  (default unchanged at 100 MB), mirroring the backend's existing limit.

## Notes

- **No migration, no host step** - the in-app Update swaps the backend, worker,
  and frontend images. The dependency changes are entirely in the web-app bundle.

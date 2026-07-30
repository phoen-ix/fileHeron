# file:Heron v2.2.0

**Audit remediation wave.** A full-surface audit of v2.1.0 - backend, SPA,
desktop client, Docker, CI, ops scripts and docs, plus the *running* deployment
and the public install path - found defects that four previous passes had missed,
mostly because a confident comment or doc line said the area was already handled.

That drift between what a document asserted and what the code did is the theme of
this release. So most fixes ship with a test that reads the real artefact
(`.env.example`, the live route table, clamd's own reported limits) rather than a
copy of it, to stop the same gap reopening.

> **⚠️ This release needs a host step, not just the in-app Update.** It changes
> `docker-compose.yml` (ClamAV version + a worker bind mount), and the in-app
> updater only swaps the backend/worker/frontend images:
>
> ```bash
> git pull && docker compose up -d
> ```
>
> **It also includes a database migration** (`files.av_unscanned`), applied
> automatically at backend boot. Because of that, rolling *back* past this
> release needs the documented `alembic stamp` recovery - see README §Upgrades.

---

## Read this if you self-host

Three things you may need to act on, independent of the code:

1. **Check your ClamAV container is actually current.** If it predates your
   `docker/clamav/clamd.conf`, clamd is running on stock limits and every upload
   over ~100 MB has been marked clean **without being scanned**.
   `docker compose up -d clamav` fixes it; verify with
   `docker exec <clamav> grep MaxFileSize /etc/clamav/clamd.conf`.
2. **Check your backups are actually running.** The systemd units in
   `scripts/ops/` are *available*, not installed - if you never installed them,
   nothing has been backing up. See README §Backups.
3. **Set `TUS_HOOK_ALLOWED_IPS`** (it now accepts CIDR, e.g. your compose
   subnet). It is the only control on `/api/internal/*` that does not depend on
   your reverse proxy's path handling.

## Security

- **A documented install could boot production on the published secrets.** The
  boot guard compared placeholder *literals* that had drifted from the ones
  `.env.example` ships, and both shipped placeholders are long enough to pass the
  length check. So `cp .env.example .env` + `ENVIRONMENT=production` started a
  real instance on the world-readable `JWT_SECRET` and `TUS_HOOK_SECRET` - every
  token forgeable, every encrypted field (TOTP secrets, OIDC client secrets,
  SMTP/IMAP passwords, public-link tokens) decryptable. Placeholders are now
  matched by prefix, and a test boots `.env.example` verbatim to prove it.
  **If you installed by hand and never replaced those two values, rotate them.**
- **`install.sh` never regenerated `DB_PASSWORD` / `DB_ROOT_PASSWORD`** - the
  mirror image of the same drift - so a fresh install kept the published database
  credentials and then refused to boot. Both are now generated, and the installer
  aborts rather than continue with a placeholder.
- **Any employee could make themselves an admin.** `POST /api/account/invite`
  accepted `target_role` unvalidated from any employee: invite an address you
  control, consume it, get the admin shell. Non-admin inviters are now restricted
  to `client`.
- **Invites could inject an account into any group.** `initial_group_ids` was
  checked for existence, never authority - and group membership grants access to
  every active share targeted at that group. Non-admins are now held to the rule
  the send path already enforced.
- **Mandatory 2FA was advisory.** The whole `/api/account` router was exempt from
  the gate, including API-token creation - and tokens bypass the gate by design.
  A user the policy covered could sign in with a password, mint a token, and use
  it everywhere. Only the routes needed to *complete* enrolment are exempt now.
- **ClamAV 1.5.2 → 1.5.3**, fixing six CVEs reachable by uploading a crafted
  file, including a heap buffer overflow write. Container image pins are now
  tracked by Dependabot, which they never were.
- **Unauthenticated 500s on every token endpoint.** A single non-ASCII byte in a
  signed download URL, unlock cookie, SSE token, unsubscribe link or tus
  signature crashed the comparison instead of rejecting the value.
- **`PATCH /api/shares/{id}`** returned share metadata to any authenticated
  caller when the request body changed nothing.
- **OIDC login planted the raw access token in a JavaScript-readable cookie**
  that nothing ever read. Removed, and cleared on next login.
- **Quota bypass:** deleting an upload that was registered but never transferred
  refunded quota that was never reserved, so repeating it lifted the cap.

## Antivirus: what "clean" now means

clamd silently clamps `MaxFileSize` to ~2 GiB regardless of `clamd.conf`, so the
30 GB limit this project configured never took effect - and files between 2 GiB
and 30 GiB were recorded as **clean on a verdict clamd never produced**.

No clamd setting changes that. Rather than quietly trust it, or block large files
outright, fileHeron keeps serving them and **says so**: such files are marked
`av_unscanned`, show an **unscanned** badge beside the file, and produce a
`file_served_unscanned` audit event. A `clean` state now means genuinely scanned.

Also fixed: scans no longer block the worker's event loop (one slow scan used to
stall every other background job), and the clamd timeout is raised from 60 s to
30 min - at 60 s any multi-GB scan timed out, left the file unscannable, and was
retried hourly forever.

## Uploads, downloads, links

- **Uploads slower than one hour died at the finish line.** The upload
  authorisation expiry was re-checked on every tus hook including the final one,
  so a transfer that took longer than the token's lifetime failed *after* every
  byte had been uploaded. It is now checked only when the upload starts.
- **Revoking a public link made it impossible to create a new one** for that
  share - the second attempt hit a database constraint and returned a 500, which
  is exactly the "revoke and re-create" the docs tell you to do.
- **In-browser preview** was gzip-compressed at maximum level on the request
  loop; on the anonymous public-link preview that is a cheap way for a stranger
  to burn CPU.

## Disaster recovery

Four independent failures, all of which had to line up to be noticed:

- **The weekly restore drill had not run since v1.56.0.** It called a script that
  release deleted as dead code, so it aborted at its first step - while the docs
  described drills as proven and scheduled weekly. The checks are now inlined.
- **Restoring onto a new host silently lost every encrypted field**, and nothing
  detected it: backups deliberately exclude `.env`, and every Fernet field is
  keyed from `JWT_SECRET`. `restore_validate.py` now decrypts a sample and fails
  loudly if the key does not match. **Back `.env` up separately** - it is not in
  your backups, by design.
- The database dump and the file archive are not a matched point-in-time pair.
  Documented, with the direction that matters and how the drill detects it.
- **Postponed updates could never fire.** The drain worker runs in the worker
  container, which had no access to the updater's state directory - and the code
  cleared the postponement *before* the hand-off, so every attempt silently threw
  it away. Mount added; failures now restore the postponement and retry.

## Inbound mail

**One malformed message stopped all inbound ingestion, permanently.** Any parse
failure aborted the poll before the read highwater was saved, so the next poll
fetched the same message and died the same way. Three ways in: a crafted `Date`
header, a raw 8-bit byte in a header, and no per-message error handling at all.
The poll now skips an uningestable message - leaving it on the server for you to
inspect - and carries on.

Related: a poll that had been failing every five minutes for weeks was recorded
as a **successful** scheduled task. Failures are now visible.

## Operator tooling that did not work

- `python scripts/promote_user.py` - the documented recovery when an admin loses
  their 2FA - failed with `ModuleNotFoundError`, as did `seed_dev` and
  `create_admin`. All three now run as documented.
- The `JWT_SECRET` rotation script was not in the backend image at all, despite
  its own instructions saying to run it there. Moved and verified.
- The README's fix for a permissions problem told you to `chown` the entire
  `data/` tree, which also rewrites the MariaDB and Redis data directories -
  after which neither starts. Now correctly scoped.
- The sample Traefik config routed resumable uploads to the backend, which has no
  such route, so every large upload 404'd for anyone following it.
- The production quickstart never set `ENVIRONMENT=production`, so the documented
  production deploy ran in development mode.

## Licensing

The desktop client's date picker (`tkcalendar`) is GPL-3.0 and was being compiled
into the MIT-licensed `.exe` published for download. Replaced with an in-tree
picker built on the standard library. **This reaches you in the next desktop
client release**, not this one.

## Behaviour changes to expect

- Employees can no longer invite employees or admins, only clients.
- A user covered by a mandatory-2FA policy cannot create API tokens until they
  have enrolled.
- Files larger than ~2 GiB now show an **unscanned** badge. They remain
  downloadable; the badge reflects what was always true of them.

## Under the hood

- A test meant to prevent unprotected API routes had been checking **zero**
  routes since a dependency upgrade, and its own safety check passed for the
  wrong reason. Repaired - the routes it now checks were all correct.
- Releases are now gated on the full test suite. Previously a tag could publish
  images and cut a release with no tests run at all.
- Two dependency advisories fixed (`pyasn1`, `postcss`).

Backend suite 1046 → 1105 tests. Every functional fix was confirmed to fail
against the previous code before being applied.

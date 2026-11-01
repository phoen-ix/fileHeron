# Operator guide

Part of the [file:Heron](../README.md) documentation.

Audience: whoever runs the server. Linux host, Docker + Compose, Traefik on the host
(not in compose).

## System requirements

- Linux host with Docker Engine ≥ 24 and **Docker Compose ≥ 2.24**. The
  compose file uses the long-form `env_file:` with `required: false`, which
  older Compose v2 releases reject outright - so "Compose v2" was not a
  sufficient requirement and an operator on 2.20 met the documented floor and
  still could not start the stack (audit 2026-07-30).
- ~2 GB RAM minimum (clamd holds ~1.5 GB of signatures in memory).
- Disk: ~500 MB for images + signatures + DB; the rest is uploads.
- Outbound HTTPS for ClamAV `freshclam` updates and HIBP checks.

## First install

```bash
git clone <repo> fileHeron && cd fileHeron
cp .env.example .env
```

Rotate **at minimum** these secrets - the stack refuses to start with placeholders in
production:

| Variable | Purpose | Generate with |
|---|---|---|
| `DB_PASSWORD` + `DB_ROOT_PASSWORD` | MariaDB app + root | `openssl rand -hex 24` |
| `JWT_SECRET` | JWT-HS256 + various HMACs | `openssl rand -hex 32` |
| `TUS_HOOK_SECRET` | HMAC for tusd ↔ backend hooks | `openssl rand -hex 32` |

Then either set `ADMIN_BOOTSTRAP_EMAIL` (and optionally `ADMIN_BOOTSTRAP_PASSWORD`) to
bootstrap an admin on first run, or use the `/setup` web wizard. The wizard is anonymous
until the first admin exists, so set `SETUP_TOKEN` (`openssl rand -hex 32`) before the
instance is reachable and open `/setup?token=<SETUP_TOKEN>`: without the token the wizard
refuses, and nobody who finds the fresh instance first can claim it. `install.sh` does this
for you and prints the URL. Then:

```bash
docker compose up -d
docker compose ps                       # healthy in ~45s (clamd warmup is slowest)
curl http://127.0.0.1:8000/api/health
```

## Compose ports

Everything binds to **127.0.0.1**; no service is publicly exposed - the host's Traefik
terminates TLS and routes.

- `127.0.0.1:${APP_BACKEND_PORT}` (default 8000) → FastAPI
- `127.0.0.1:${APP_FRONTEND_PORT}` (default 8080) → nginx (SPA + tusd proxy)

## Traefik on the host

<details>
<summary>Sample static + dynamic config</summary>

```yaml
# traefik.yml (static)
entryPoints:
  websecure:
    address: ":443"
certificatesResolvers:
  acme:
    acme:
      email: ops@example.com
      storage: /etc/traefik/acme.json
      tlsChallenge: {}
providers:
  file:
    filename: /etc/traefik/dynamic.yml
```

```yaml
# dynamic.yml - routes + the internal-deny rule
http:
  routers:
    fileheron-spa:
      rule: "Host(`files.example.com`)"
      service: fileheron-frontend
      entryPoints: [websecure]
      tls: { certResolver: acme }
    fileheron-api:
      rule: "Host(`files.example.com`) && PathPrefix(`/api`) && !PathPrefix(`/api/internal`)"
      service: fileheron-backend
      entryPoints: [websecure]
      tls: { certResolver: acme }
  services:
    fileheron-backend:
      loadBalancer:
        servers: [{ url: "http://127.0.0.1:8000" }]
    fileheron-frontend:
      loadBalancer:
        servers: [{ url: "http://127.0.0.1:8080" }]
```

</details>

The `!PathPrefix(/api/internal)` clause is defence-in-depth for
`/api/internal/tus-hooks` (HMAC-signed and network-isolated). Traefik **must overwrite,
not append**, client-supplied `X-Forwarded-For`, and you must **not** set
`forwardedHeaders.trustedIPs`/`insecure` on the public entrypoint - the backend trusts
the leftmost XFF for audit/rate-limit IPs.

## Production hardening checklist

`install.sh` produces a production-ready `.env` (sets `ENVIRONMENT=production`,
`COOKIE_SECURE=true`, derives `WEBAUTHN_RP_ID`, blanks the dev test account). If you
write `.env` by hand, confirm each of these before going live:

- **`ENVIRONMENT=production`** - forces secure cookies, disables `/docs` +
  `/openapi.json`, enables HSTS, and turns the secret/`AV_SKIP` placeholder checks
  into fatal boot errors (an unrecognised value aborts boot).
- **Strong unique secrets** - `JWT_SECRET`, `TUS_HOOK_SECRET`, `DB_PASSWORD`,
  `DB_ROOT_PASSWORD` (each >= 32 chars; production refuses placeholder values).
  `install.sh` generates these with `openssl rand -hex 32`.
- **`WEBAUTHN_RP_ID`** = your exact public host (no scheme/port), or passkeys break.
- **`AV_SKIP=false`** - the ClamAV upload-scan gate (fatal if `true` in production).
- **No dev test account** - leave `TEST_ACCOUNT_*` blank (only seeded outside production).
- **Reverse proxy** - terminate TLS at Traefik (or equivalent); it MUST overwrite,
  not append, `X-Forwarded-For`, and the backend/tusd ports stay bound to
  `127.0.0.1` (never exposed publicly). See [Traefik on the host](#traefik-on-the-host).
- **Defense-in-depth (recommended)** - set `METRICS_BEARER_TOKEN` (else `/api/metrics`
  is disabled) and `TUS_HOOK_ALLOWED_IPS` to restrict the internal hook surface.
- **Backups + drills** - enable the nightly backup timer + weekly restore drill
  (see [Backups](#backups)).

The runtime applies `no-new-privileges` to every container and drops **all Linux
capabilities** from each, adding back only what the image's root phase needs to
start - listed with the reason next to each one in `docker-compose.yml` (backend,
worker and tusd never run as root and get nothing back). The app images are pinned
by the release tag (`FH_TAG`, moved only by the in-app updater, which verifies
their signatures) and the database, Redis, ClamAV and tusd images by digest;
Argon2id + HIBP guard passwords.

## Storage layout

```
./data/
├── db/           # MariaDB datadir (bind-mounted)
├── redis/        # Redis AOF + RDB
├── uploads/      # tusd working dir; partial uploads
├── files/        # finalized uploads (yyyy/mm/{file-uuid}.bin)
└── quarantine/   # ClamAV-flagged files
```

`uploads/`, `files/`, `quarantine/` must share one filesystem (finalize is
`shutil.move`). `data/{uploads,quarantine,files,updater}` must stay UID-1000-owned (a
committed `.gitkeep` per dir + `install.sh` keep them so); if a bind-mount source is
missing when compose starts, the root daemon recreates it `root:root` and UID 1000 can
no longer write.

### Storage backend (`STORAGE_BACKEND`)

Default `local`. Set `STORAGE_BACKEND=s3` for any S3-compatible store (AWS, MinIO, …)
via `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` / `S3_ACCESS_KEY_ID` /
`S3_SECRET_ACCESS_KEY` / `S3_KEY_PREFIX`.

<details>
<summary>S3 behaviour + caveats</summary>

- Uploads stream to the bucket (multipart for large files); downloads **307-redirect to a presigned URL** (app does auth + the single budget decrement first); AV scans via clamd **INSTREAM**; quarantine is a server-side key-prefix copy.
- **Pick the backend at install time** - switching local↔s3 with existing data isn't automatic (an operator script copies bytes + rewrites `files.storage_path`). `uploads/` (tusd staging) always stays local.
- Bucket durability/versioning is your responsibility; `scripts/backup.sh` only tars local `./data/files`. INSTREAM is bounded by clamd `StreamMaxLength` (raise it for large files; a file over that limit but under clamd's ~2 GiB ceiling scans as `error` and is not served). Files past the ceiling are not streamed to clamd at all - they are served flagged `av_unscanned`, since no verdict is obtainable. The low-disk guard is a no-op on s3. At-rest encryption is your bucket's SSE - or file:Heron's own, which also keeps the contents from the bucket's operator ([Encryption at rest](#encryption-at-rest)).

</details>

## Encryption at rest

Off by default. Turn it on at **Admin › Security & audit › Encryption at rest**:
tick that you have a safe copy of `.env`, then confirm with your password.

**What it does.** Every file is stored encrypted (AES-256-GCM in 1 MiB chunks,
a fresh key per file, wrapped by a key derived from `JWT_SECRET`). A new upload
is encrypted right after its virus scan and before anyone is told it is there;
until then it answers "scan in progress", as before. Files already stored are
encrypted in the background by the scheduled task `encrypt_existing_files`, a
few at a time; the unencrypted copy is deleted an hour later (a download that
is still reading it finishes). Inbound mail attachments are stored encrypted
from the start. Downloads, previews, resumes, the desktop client and ZIP
archives work exactly as before.

**What it protects:** file contents in backups, restic copies, disk images,
a stolen disk, and the S3 bucket. **What it does not:** anyone who controls
the server - the key lives beside the files. Also not covered: tusd's staging
copy while an upload is in progress, and the disk blocks of a deleted
unencrypted copy. On a **versioned** bucket, replaced unencrypted objects stay
as noncurrent versions until a lifecycle rule removes them.

> **Backups restore encrypted files only together with `.env`.** The archives
> hold ciphertext; the key comes from `JWT_SECRET`, which backups deliberately
> do not contain. A restore without it brings back the database and not one
> encrypted file. `scripts/restore_validate.py` decrypts a sample and fails
> loudly when the key does not match.

**Turning it off** (also password-gated) stops encrypting new uploads; files
already encrypted stay encrypted and keep working. A file that fails to encrypt
is stored unencrypted for now (audited `file_encryption_deferred`) and the
background task tries it again; after three failures it is set aside for a day
(`file_encryption_failed`, **Try them again** on the page).

**Rolling back** to a release from before encryption at rest is refused while
encrypted files exist - that release would serve them as noise. Decrypt first:

```bash
docker compose exec backend python scripts/decrypt_files_at_rest.py --turn-off --purge-now
```

It refuses while encryption is on (unless `--turn-off`), waits for a running
background pass, needs free space for one file at a time, and is safe to re-run.

## Backups

```bash
./scripts/backup.sh
# → ./backups/<YYYY-MM-DD_HHMMSS>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}
```

> **Back up `.env` separately - the backup does not contain it, and a restore
> without it is unrecoverable.**
>
> Every encrypted field in the database - TOTP secrets, OIDC client secrets,
> SMTP/IMAP passwords, public-link tokens, webhook secrets - is encrypted under
> a key derived from `JWT_SECRET`. Restore onto a host with a different one and
> all of those rows come back **intact and permanently unreadable**: every 2FA
> user locked out, SSO dead, outbound mail dead. Row counts, checksums and the
> restore script's own output all look correct, because the data is there - it
> just cannot be decrypted, and nothing can recover it.
>
> Secrets are kept out of `./backups` deliberately: it is a plain directory,
> frequently synced onward. Keep `.env` in your password manager or secret
> store, and re-save it whenever you rotate `JWT_SECRET`.
> `scripts/restore_validate.py` samples those fields and fails loudly on a key
> mismatch - but only once you are already on the new host, which is too late to
> go and fetch the old key.
>
> The weekly restore drill cannot catch this: it restores on the same host,
> reading the same `.env`.
>
> With [encryption at rest](#encryption-at-rest) on, the same goes for every
> encrypted **file** in `files.tar.gz` and `quarantine.tar.gz`.

With `BACKUP_RESTIC_REPO` + `BACKUP_RESTIC_PASSWORD` set, the dated dir is also pushed
to that restic repo (S3/B2/SFTP/REST/local; password via `--password-file`, not env).
Every snapshot carries a stable `fileheron` tag plus a per-run `fileheron-<stamp>`
one, and retention selects on the stable tag - so the sweep only ever touches
fileHeron's own snapshots and a repo shared with another application is safe.

Without `BACKUP_RESTIC_REPO` every run says so (`this backup is local-only, on the
same disk as the data it protects`) and still exits 0. With it set, a run whose offsite
copy was NOT written - password missing, `restic` not installed, or the push failed -
exits **1** after the local backup and its retention are done, so the systemd unit's
`OnFailure=` alert fires instead of a nightly silent gap.

> **Upgrading to v2.12.0 with an existing restic repo:** snapshots written by
> earlier versions carry only the per-run tag, so retention will skip them and
> they will accumulate. Nothing was ever pruned before this release either, so
> this is not new - but to bring the backlog under the policy, adopt them once
> (needs `jq`):
>
> ```bash
> restic --repo "$BACKUP_RESTIC_REPO" snapshots --json \
>   | jq -r '.[] | select(any(.tags[]?; startswith("fileheron-"))) | .short_id' \
>   | xargs -r restic --repo "$BACKUP_RESTIC_REPO" tag --add fileheron
> ```
>
> The next nightly run then applies keep-daily 7 / weekly 4 / monthly 12 to
> them. Check what it would drop first with `forget --dry-run`.

Schedule it nightly. A ready-made systemd timer ships in `scripts/ops/` (adapt
`User=`/paths to your install):

```bash
sudo cp scripts/ops/fileheron-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fileheron-backup.timer
```

Or via host cron:

```cron
30 3 * * * cd /opt/fileHeron && ./scripts/backup.sh >> /var/log/fileheron-backup.log 2>&1
```

## Restore

```bash
./scripts/restore.sh ./backups/<stamp>/   # sha256-verifies, prompts for literal "restore", then reimports
```

`restore.sh` reads the manifest before it asks anything: a full backup (`db.sql`,
`redis.rdb`, `files.tar.gz`, `quarantine.tar.gz`) replaces the database, Redis and
`data/files` + `data/quarantine`; a **pre-update backup** (`db.sql` + `redis.rdb`,
see below) replaces the database and Redis only and leaves the files alone. A
manifest of neither shape stops before anything is touched.

### Restoring a pre-update backup

The in-app Update can back up the database and Redis first (a checkbox in the
Update dialog; always, by default, when the release upgrades the database). Those
backups land in `backups/pre-update/<stamp>_<from>-to-<to>/` - apart from the
nightly ones, so they are not counted by the nightly retention, not drilled and not
pushed to restic. The updater keeps the newest `updates.backup_keep` (3) and
deletes any older than `updates.backup_max_age_days` (30) on each update; the newest
one is always kept.

**Undo an update's data, the server itself is fine** (for example a migration that
went wrong): roll the app back, then restore:

```bash
scripts/rollback.sh <from-tag>          # or Rollback in /admin/system
./scripts/restore.sh backups/pre-update/<dir>
```

The database, Redis, ClamAV and tusd stay on their new versions; the old app runs
against them.

**The database (or Redis) did not come back during an update.** The job names the
backup and the old app is running again, but the data service is stuck on the
release's definition. Go back to the previous compose file first - enough whenever
the data directory itself is intact (a bad setting, an image problem):

```bash
git checkout <from-tag>                  # the compose file of the version you came from
docker compose up -d --no-deps db        # or redis
```

**MariaDB cannot read its datadir any more** (it upgraded `data/db` before failing),
or you need the old server version back: a major upgrade rewrites `data/db` in
place and cannot be undone, so go back to an empty datadir on the old version and
load the backup into it:

```bash
docker stop fileheron-backend fileheron-worker && docker compose stop db
docker run --rm -v "$PWD/data":/d alpine mv /d/db /d/db.failed-upgrade
git checkout <from-tag>                  # the compose file pins the old images
sed -i 's/^FH_TAG=.*/FH_TAG=<from-tag>/' .env
docker compose up -d db                  # fresh datadir, created from .env
./scripts/restore.sh backups/pre-update/<dir>
```

The next update upgrades again, after a fresh backup. A database-only restore
leaves files uploaded after the backup on disk without a row, and rows deleted
after it are back without their bytes; `restore_validate.py` lists both:

```bash
docker cp scripts/restore_validate.py fileheron-backend:/app/scripts/
docker compose exec -T backend python scripts/restore_validate.py
```

**Restore drills** prove the backup actually restores. `scripts/restore_drill_e2e.sh`
restores the latest backup into an **isolated throwaway compose project** (own name,
data, port - never touches the live stack), runs `alembic upgrade head`, then
`scripts/restore_validate.py` (row counts, on-disk files, no orphan FKs, schema at
head), and records success in `backups/LAST_SUCCESSFUL_DRILL`. It refuses an
auto-selected backup older than `DRILL_MAX_BACKUP_AGE_HOURS` (default 48), so a
drill cannot report green once backups have stopped arriving.

> **Neither the backup nor the drill runs until you schedule it.** The units in
> `scripts/ops/` ship with the repo; shipping is not scheduling. Install and
> enable them explicitly, and check the result — a copied-but-not-enabled unit
> is the failure mode that leaves an instance with no backups at all while the
> files look present:
>
> ```bash
> sudo cp scripts/ops/fileheron-*.{service,timer} /etc/systemd/system/
> sudo systemctl daemon-reload
> sudo systemctl enable --now fileheron-backup.timer fileheron-restore-drill.timer
> systemctl list-timers 'fileheron-*'     # confirm both are listed and active
> ```
>
> The units are copied, not symlinked, so re-copy and `daemon-reload` after
> changing them. Both carry `OnFailure=fileheron-alert-failure@%n.service`, which
> emails the app's admins - install that unit too, or a failed backup is only a
> `failed` unit and a journald line. Set `BACKUP_RESTIC_REPO`/`BACKUP_RESTIC_PASSWORD` if you want the
> archive pushed offsite; unset means local-only.

## Upgrades

Prefer the **in-app self-update**: `/admin/system` polls GitHub (filtered to backend
`vX.Y.Z` tags, matched in full - so `v1.2.3-rc1` and the desktop client's
`client-v*` tags are skipped) and surfaces an "Update available" banner with the changelog;
**Update** drives the updater shim/executor and records the previous `FH_TAG` for
one-click rollback. Manual path:

```bash
git pull && docker compose pull && docker compose up -d --build
```

**Automatic updates** are off by default. Turn them on under **Status & updates**; it
asks for your password, because they install releases without anyone entering it.
Choose which releases qualify (**patch only** - the default -, patch and minor, or any)
and how long a release must have been public first (default **24 h**, so a quick
follow-up fix can come out first). The task `auto_update` - its own row on Scheduled
tasks, daily at 03:30 site time - then schedules an eligible release exactly like
**Postpone**: new transfers pause, running ones finish (at most the drain wait), and the
update below runs with the usual pre-update backup. Admins are alerted when one is
scheduled and when it ends. A release whose automatic install fails or rolls back is not
retried automatically; newer releases, and a manual **Update**, still are. Releases
outside the chosen scope are announced as before.

Alembic migrations run from the backend entrypoint on every boot - idempotent
(`_has_table` / `_has_column` / `_has_index` guards), safe to re-run. **Roll-forward
only**; back up before upgrading. (Image downgrade after a forward migration needs an
`alembic stamp` from the newer image first.)

**What an in-app Update does**, in order - nothing on the host changes before the
backup has succeeded:

1. Works out which infra services (database, Redis, ClamAV, tusd) the release
   changes: a new image, a changed `docker-compose.yml` section, or a changed file
   the service mounts from the checkout (`docker/clamav/clamd.conf`).
2. Pulls the release's app images and the new infra images.
3. Backs up the database and Redis to `backups/pre-update/` when the Update
   dialog's box is checked - and regardless when the release changes the database
   (`updates.backup_on_db_change`). A failed backup stops the update with nothing
   changed.
4. Fast-forwards your checkout to the release commit (`git merge --ff-only`, as
   the checkout's owner), so `docker-compose.yml`, `docker/` and `scripts/` match
   the release.
5. Recreates the database and Redis if the release changes them, one at a time,
   each waiting for its healthcheck. The backend and worker are stopped
   meanwhile, so expect a few minutes of downtime when MariaDB upgrades itself
   (`MARIADB_AUTO_UPGRADE=1`). If the database or Redis does not come back
   healthy, the update stops, the old app is started again and the job names the
   backup to restore (see *Restoring a pre-update backup*).
6. Swaps backend, worker and frontend and waits for the new version; a failed
   start rolls the APP back automatically. Infra is never rolled back
   automatically - a MariaDB or Redis major has no way back in place.
7. Recreates ClamAV and tusd if the release changes them, with the new app
   already serving - neither is needed to answer a request, so the app is not
   kept down while ClamAV loads its signatures. A failure only adds a warning.
   Then it recreates the updater-shim.

**What the updater trusts.** Every server image published from v2.29.0 on is
signed when it is built (Sigstore cosign, keyless, under the identity of this
repository's release workflow on a release tag). The updater-shim verifies that
signature on the updater image for the requested version before running it, and
runs it by the verified digest; the executor verifies the backend, worker,
frontend and updater images the same way before swapping anything. An image
without that signature is refused and nothing changes. The shim also refuses an
Update to a version older than the one running (it asks Docker, not the
request), and lets a Rollback return only to the version that was running before
the last update - it records that itself in `data/updater-private/`, a
root-owned directory the app containers never see (the first Rollback after
v2.29.0 has no record yet and is allowed with a warning). **If you publish your
own images** (a fork), set `SHIM_REQUIRE_SIGNATURE=false` in `.env` - you are
then trusting your registry account alone - or sign them the same way and set
`SHIM_SIGNER_IDENTITY_RE` to your workflow's identity if you renamed the
repository. The database, Redis, ClamAV and tusd images are pinned by digest in
`docker-compose.yml`, so a rebuild on Docker Hub arrives as a Dependabot pull
request and is applied by the next update like any other infra change.

**When the infra step is skipped** the app update still runs, and the job shows
a warning with the manual command:

```bash
git fetch --tags && git merge --ff-only <tag> \
  && docker compose up -d --no-deps db redis clamav tusd
```

It skips when the checkout has local edits to files the release changes, has
diverged, or is ahead of the release with infra changes the release does not
have; when a `docker-compose.override.yml` exists or `.env` sets `COMPOSE_FILE`
(the updater cannot apply either); when the install is not a git checkout or is a
shallow clone; when the release's compose file needs a variable your `.env` lacks;
or when `updates.infra_sync` is off. Keep site settings in `.env`, not in
`docker-compose.yml`. Your host Traefik config and the `scripts/ops/` systemd units
(copies) stay manual. If the host reboots while the data layer is being
recreated, bring the rest back with `docker compose up -d`.

**The first update from a version without this** (v2.18.0 and older) already runs
the new updater - the shim always runs the target release's executor - so it syncs
infra and backs up before a database upgrade; the checkbox itself appears in the
Update dialog from the following update on.

**Scripted deploy / rollback (bootstrap + hotpatch).** `scripts/deploy.sh` pulls
the GHCR images for `FH_TAG`, taken from the environment first and `.env` second
(so `FH_TAG=v1.2.3 scripts/deploy.sh` works), defaulting to `latest`.

If the pull fails it does **not** blindly build: with every image already present
locally at that tag it proceeds with those and builds nothing, and for a published
release tag (`vX.Y.Z`) it **fails with exit 3** rather than building - a local
build is not that release, and tagging it as one would overwrite the very image a
rollback needs. The build-from-source fallback remains for first install and for
hotpatch tags, which is what it exists for; local builds are not sticky, so the
next deploy tries GHCR again.

`scripts/rollback.sh` with no args lists the rollable tags; with a `<tag>` it pins
`FH_TAG=<tag>` in `.env` and rolls the four services onto it. (It used to re-tag
the target as `:latest` instead, which left `.env` still naming the broken version
so the next pull silently undid the rollback.) It requires the four service images
locally; the `updater-executor` image is not required, since the in-app updater
pulls that itself.

Prefer the in-app updater for routine upgrades - it maintains its own rollback
state (`data/updater/rollback_target.json`) that a manual `FH_TAG` flip leaves
stale.

## Health checks & metrics

- `GET /api/health` → `{"status":"ok"}`, or `{"status":"db_unavailable"}` with a 503
  when the database is unreachable. A Redis or ClamAV outage does NOT change
  `status` - it adds a `degraded: [...]` field alongside `"status":"ok"`, because
  the app still serves requests with rate-limit and AV fail-open semantics.
  **Alert on the presence of `degraded`, not on `status`.** Build identifiers,
  pool stats and `degraded` are returned only to loopback / compose-network
  callers; a public caller gets bare liveness.
  Each container also has its own Docker healthcheck (`docker compose ps`).
- `GET /api/metrics` - Prometheus exposition, guarded by `METRICS_BEARER_TOKEN` and/or `METRICS_ALLOWED_IPS` (cached `METRICS_CACHE_TTL_SEC`).

## Background jobs & housekeeping

The ARQ `worker` runs the cron set (no host crontab) plus event-driven AV scan + email
send. Cadences are admin-tunable on [Scheduled tasks](admin-guide.md#scheduled-tasks-adminscheduled-tasks);
every retention window is a setting (`0` disables that pruning job). Full list with
defaults: [ARQ workers + cron](developer-guide.md#arq-workers--cron).

## Common operational issues

- **clamd slow to come up** - first boot does a full `freshclam` sync (~150 MB); watch `docker compose logs clamav`.
- **tusd 500 on finalize** - usually a `TUS_HOOK_SECRET` mismatch between backend + tusd; restart both after changing.
- **Upload stalls at "finalising"** - `data/uploads` and `data/files` must resolve to the same mount inside the backend container.
- **Permission denied writing data/** - a missing bind-mount dir was recreated `root:root`. Chown only the app's own dirs:
  `docker run --rm -v "$PWD/data":/data alpine chown -R 1000:1000 /data/{uploads,quarantine,files,updater}` (no restart needed).
  Do **not** `chown -R 1000:1000 /data` wholesale - that also rewrites `data/db` and `data/redis`, whose
  datadirs MariaDB and Redis expect to own; they will refuse to start.

## Operator escape hatches

- **Lost admin access** - `docker compose exec backend python scripts/promote_user.py <email>`.
- **Leave encryption at rest** (or roll back to a release from before it) - `docker compose exec backend python scripts/decrypt_files_at_rest.py --turn-off` decrypts every stored file; add `--purge-now` before such a rollback so no replaced copy is left behind. See [Encryption at rest](#encryption-at-rest).
- **Bypass ClamAV in CI/dev** - `AV_SKIP=true` (boot refuses `production + true`).
- **Rotate `JWT_SECRET`** - `docker compose exec backend python /app/scripts/rotate_jwt_secret.py` re-encrypts every
  Fernet-protected field (TOTP secrets, OIDC client secrets, public-link tokens,
  the encrypted SMTP/IMAP passwords, each secret's wrapped key and secret links)
  under the new secret - a secret's passphrase layer is untouched, so no passphrase
  is needed. Stop the worker, run
  with `OLD_JWT_SECRET`/`NEW_JWT_SECRET` set (`--dry-run` first; safe to re-run
  after a crash), update `.env`, restart backend + worker. Rotating *without* it
  locks out every TOTP user and breaks SSO, the stored SMTP password,
  public-link re-view and every active secret. All sessions invalidate (forced re-login) - plan a window.

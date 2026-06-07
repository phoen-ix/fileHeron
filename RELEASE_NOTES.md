# file:Heron v1.33.0

**Back up your configuration, and rebuild a crashed instance from it.** A new
admin page exports this instance's settings, branding, OIDC/webhook configs,
groups and user accounts to a single file, and imports it to restore a fresh or
crashed system. Shared files are deliberately out of scope - they're short-lived,
and importing a backup invalidates every active share.

## What's new

- **New page: Admin -> Settings -> Backup & restore.**
- **Category-selectable export.** Tick what to include: Settings & branding (incl.
  the logo image and legal pages), OIDC providers & webhooks, Groups & memberships,
  User accounts (incl. password hashes, roles, 2FA - TOTP / recovery codes /
  passkeys), and optionally Logs (audit / email / download / login / notifications).
- **Three ways to handle secrets**, chosen per export:
  - *Passphrase-encrypted (portable)* - secrets are decrypted and the whole file is
    re-encrypted with a passphrase you choose (scrypt + Fernet). Restores onto any
    server, even one with a different `JWT_SECRET`.
  - *Keep ciphertext* - raw encrypted blobs travel as-is; only restores if the
    target reuses the same `JWT_SECRET`.
  - *Exclude secrets* - secret values are left out; re-enter them after import.
- **Optional `.env` snapshot** (passphrase mode only) - bundles infrastructure
  secrets (`JWT_SECRET`, DB/SMTP credentials, S3 keys) for the operator to restore
  by hand. It is shown for you to apply manually and is **never** written
  automatically.
- **Import is a preview-then-replace flow.** Upload a backup, see exactly what will
  change (counts, which users/groups get purged, how many shares get invalidated),
  then confirm. Import:
  - replaces the in-scope configuration (settings, branding, OIDC, webhooks, email
    templates);
  - upserts users and groups by their natural key (email / group name) and remaps
    all foreign references - including the user/group IDs embedded inside settings
    like the 2FA, public-link and approver allowlists;
  - **purges users and groups not present in the backup** (FK-safe: hard-deleted
    where possible, otherwise anonymised via the erasure path; the admin running the
    import is always kept);
  - **invalidates every active share** (state -> expired, file bytes deleted) before
    touching config, since a restore changes the world out from under any live share;
  - **revokes all sessions** so everyone re-authenticates against the restored
    credentials.

## Good to know

- The backup file is versioned; importing a file from a newer format than the
  running app is refused, and a schema-revision mismatch is surfaced as a warning so
  you can review for drift.
- A passphrase-encrypted backup contains decrypted secrets once opened - store it
  safely and never ship the passphrase alongside the file.
- This is the application-level *configuration* backup; the host-level full backup
  (`scripts/backup.sh` - database + file bytes + Redis) is unchanged and still the
  tool for byte-for-byte disaster recovery.

## Upgrade notes

- **No database migration.** The two new audit events
  (`config_backup_exported` / `config_backup_imported`) reuse the existing
  free-string `event_type` column.
- No new environment variables. The passphrase KDF and all crypto reuse the
  `cryptography` library already in the image.
- Safe to roll straight forward from v1.32.0.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.33.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.33.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.33.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.33.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.33.0`

Click **Update** in `/admin/system` to roll forward.

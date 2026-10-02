# Config backup

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/services/config_backup.py`, `backend/app/routers/admin/backup.py`, `frontend/src/views/AdminSettingsBackup.vue`

## Config backup

Admin export/import of **configuration** for disaster recovery (UI
`/admin/settings/backup`, engine `services/config_backup.py`). Files/shares
excluded by design; **import invalidates all active shares**.

- **File** = versioned `*.fhbackup.json`; outer envelope always plaintext (magic + `format_version` + `secret_mode` + categories) so import sniffs the mode without a passphrase; payload inline or passphrase-encrypted.
- **Categories** (opt-in): settings+branding (incl. logo bytes + legal), oidc+webhooks, groups, users (incl. password_hash + 2FA), logs.
- **Secret modes:** `passphrase` (decrypt → scrypt-encrypt whole file, portable) · `ciphertext` (raw Fernet, only decrypts on the same `JWT_SECRET`) · `exclude`. Optional whitelisted `os.environ` snapshot via `include_env` (passphrase only; display-only on import, never written). Key derivation: `utils/crypto.py::{derive_backup_key,encrypt_with_passphrase}`.
- **Import = REPLACE** (`apply_backup`): wipe+reload standalone tables; **upsert** users/groups by natural key with old→new ID remap (incl. ids embedded in `app_settings` JSON); **purge** identities absent from the backup (hard-delete where FK-safe, else `erasure.erase_user`; the importing admin is always kept); rehydrate secrets under the target `JWT_SECRET`; **revoke all sessions**. Share invalidation runs in its OWN committed pass first via `share.py::invalidate_all_active_shares` (byte delete is irreversible).
- **`apply_backup` is deliberately NOT split.** It commits twice mid-flight (after the share invalidation, and again after the identity purge), both ordered against irreversible byte-unlinking with the reasoning inline, and every phase both consumes and produces shared state (`user_id_map`, `group_id_map`, `summary`, `warnings`, `deferred_erasures`) - helpers would relocate the coupling and make the transaction boundaries LESS visible. The test pinning the commit-before-purge ordering must not use `str.index` (raises `ValueError` rather than failing) and needs a vacuity guard.
- **`_columns` must return ORM attribute names, not table column names** - `AuditLog.extra` maps to `metadata_json`, and getting this wrong made the whole `logs` category raise on export.
- **`apply_backup` preserves every `user_erased` audit row** plus everything written after its own high-water mark; don't reinstate a blanket `audit_log` wipe.
- **An import must not resurrect an erased subject.** Users match on EMAIL, and an erased row's email is the `erased-<id>@erased.invalid` tombstone - so a backup taken before the erasure INSERTed a fresh row with the subject's original email, display name and password hash, and step 5 purged the tombstone for not being in the backup; the surviving `user_erased` receipt then pointed at a live account. Tombstoned users are **skipped and WARNED**, not silently dropped, and are left out of `user_id_map` so their TOTP secret, recovery codes, WebAuthn credentials and preferences drop out with them.
- **`_build(OIDCProvider, ...)` must preserve the provider id.** `users.oidc_provider_id` is matched against it in step 3, so `skip={"id"}` would sever every SSO binding; and because `jwks._cache` is keyed on that id alone with a 1h TTL, a reused id under a different issuer verified ID-token signatures against the previous IdP's keys.
- **`_reconcile_after_import` replays the side effects the raw `AppSetting` writes skip**, and runs after the final commit beside the deferred erasures: it clears the JWKS cache and releases `IpBlock` rows stamped under an old `scan_guard.network_prefix_v6` - because `is_blocked` matches by CIDR CONTAINMENT, an orphan kept denying service while the admin page showed nothing to release.

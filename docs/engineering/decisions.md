# Don't re-propose / don't re-file

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** none of its own - read it before proposing a split or a "fix", or filing a finding; `backend/app/services/file.py` cites accepted residual #4 by number

## Don't re-propose / don't re-file

### Deliberately NOT split

Each split silently breaks a pin:

- **`services/scan_guard.py` (1,874 lines).** Six module-level globals behind two `global` statements form a closed cache unit, and `tests/test_scan_guard_middleware.py` does `monkeypatch.setattr(sg, "_distinct_paths_seen", ...)` twice - `note_offence` resolves that name from its OWN module globals, so a package split leaves both tests **passing while testing unpatched behaviour**. 34% of the file is documented invariants.
- **`services/share.py` (1,965).** `_user_group_ids` is a hub across four clusters and is imported BY NAME from `routers/account.py`; cluster C's notification helpers fan into three other clusters; 47 function-local imports already mark cycle pressure; and `test_share_recipient_privacy.py` AST-scans a hardcoded path.
- **`services/config_backup.py::apply_backup` (550 lines)** - see `docs/engineering/config-backup.md`.

### Open / deferred / dropped

- **Dropped:** Locust load-test baseline (real-load operation supersedes); zxcvbn-ts strength meter (HIBP is the real defense).
- **Rejected:** OpenAPI codegen for the frontend types (see CLAUDE.md §Testing + CI gates); a per-token access denylist (see `docs/engineering/auth.md` §Revocation); splitting the three files above.

### Verified FALSE - don't re-file

Checked against the code and found not to be defects. The gitignored audit file
that raised them was deleted, so this is the last copy:

- `drain_pending_update` does **not** double-fire - see `docs/engineering/self-update.md` §Maintenance mode + drain-before-update.
- `share_expiring` is **not** at-least-once-unsafe - `notification.dispatch` defers its enqueue to `run_after_commit`, so the marker and the email share a transaction.
- `image.py`'s decompression-bomb guard **is** tested - in `test_guard_thresholds.py`, **not** `test_image.py`.
- `<a href="javascript:">` **is** covered - in `test_email_template_overrides.py`.
- **16 CodeQL alerts were dismissed on 2026-09-29, each with its reason on GitHub**: SHA-1 in `hibp.py` (the range API requires it), SHA-256 in `sha256_hex` (random tokens only), logged key or event NAMES taken for passwords, 0644 on the updater's job and rollback files (root executor ↔ uid-1000 backend), the admin OIDC probe's URL, and the admin live-check `str(e)`. **Four more on 2026-10-02**, same class: `secret_reveal.py`'s lock warning (secret id, recipient id, two counts) and the desktop client's `i18n.py` missing-key warnings (a translation KEY such as `secrets.reveal.passphrase_label`, never user input). If one of them comes back under a new number after a code move, dismiss it again with the same reason rather than changing the code. The admin OIDC probe's URL did exactly that: it came back as #51 once the outbound size caps moved every request into `utils/http_fetch.fetch_json_capped`, and was dismissed again on 2026-10-02.

### Accepted residuals (deliberately CLOSED, don't re-file)

**These numbers are permanent IDs.** `backend/app/services/file.py:269` cites
"accepted residual #4" by number. Closing one leaves a tombstone line; never
renumber, or that comment silently points at a different rule.

1. **The replayed tus creation.** @uppy/tus replays the creation POST when the response is lost, so a superseded working file can linger. It is not a quota bypass: `handle_pre_finish`/`handle_post_finish` both gate on `state == uploading` so exactly one upload finalizes, post-terminate sets `state = deleted`, `quota_reconcile` is DB-authoritative, and the per-upload ceiling is the envelope's `max_size` (equality-enforced), not the 1 TiB backstop. The residual is transient staging-space amplification, reclaimed by `cleanup_abandoned_uploads` after 24h. **Pre-create must STAY idempotent rather than unlinking the superseded file** - that would delete a file tusd holds open.
2. **Single-source brute force is indistinguishable from a NAT'd office.** The guard cannot separate one determined guesser from a building behind one address, which is why `login_stuffing` needs ≥3 distinct emails and excludes a source that ALSO logged in successfully in the window, and why the auth signal ships OFF. On a single-user instance a stale password manager and a slow stuffer are indistinguishable by volume, because the limiter caps both identically and the shared-egress exemption needs two accounts. Lockout (`users.locked_until`) is the per-account control for this; the IP guard is not, and **widening it to try is how you 404 a customer's whole office**.
3. **A partial destination file if `finalize` itself dies mid-copy.** The direct-upload path compensates (`run_after_rollback` registered immediately before the commit in `routers/uploads.py`), so this is the narrower window inside `shutil.move`'s copy fallback on a cross-device bind mount. Reclaimed by the orphan sweep; not worth a second write path.
4. **`file.py`'s `was_infected` orphan is unreachable, not absent.** `mark_deleted_for_expiry` deliberately returns a None locator for a `was_infected` row so an unlink-by-`storage_path` cannot destroy quarantined evidence (`quarantine_file` REWRITES `storage_path` to the quarantine locator). The row would fall out of both purge filters if it ever got there - it cannot today, because every expiry entry point filters `Share.state == active` while quarantine revokes the parent share on marking. **Don't "fix" the None locator without re-reading that pair.**
5. **`files.sha256_hex` is direct-upload-only and verified nowhere** - see CLAUDE.md §Database schema.
6. **Encryption at rest leaves plaintext where it never reaches**: tusd's staging copy in `data/uploads` until finalize, and the disk blocks of an unlinked plaintext copy (no secure erase on a journalling filesystem or an SSD). It protects backups, restic copies, disk images and the bucket; the key lives beside the files, so it does not protect against the host.
7. **On S3 the plaintext object exists until its swap, and a VERSIONED bucket keeps it as a noncurrent version** until a lifecycle rule removes it. The admin page says so on S3; file:Heron does not manage bucket lifecycle.
8. **A backfill swap changes a file's ETag** (`"fhe-…"` instead of FileResponse's), so a browser resume paused across the swap restarts once (If-Range misses) instead of splicing. ZIP archives are byte-identical either way.

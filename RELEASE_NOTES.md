# file:Heron v2.3.0

**Tier 3 remediation.** v2.2.0 fixed the audit's high-severity findings and left
a bucket of ~295 low/info ones. Re-verifying that bucket against the shipped code
found it was never the homogeneous tail it was filed as: **262 were still valid,
15 had already been fixed, and once severities were normalised across the
original per-area reports, 24 turned out to be medium or above** - including a
second licensing violation and a credential-leaking OIDC default.

> **No host step and no migration.** In-app Update is sufficient.
>
> **One breaking change for API tokens** - see below.

---

## Breaking: a new API-token scope

`GET /api/shares/{id}/public-link` returns the **decrypted plaintext link URL**
and a QR of it - an anonymous, password-free route to the file bytes. It was
gated on `shares:read`, so a token issued purely for reading metadata could
harvest download URLs for every share its owner controlled, while holding no
`files:download`.

It now requires a new **`public_links:read`** scope. **A token scoped
`shares:read` only will get 403 on that one route** until you add it. Tokens with
no scope restriction (the default) are unaffected.

## Licensing

`zipstream-ng`, which backed both the authenticated bulk-ZIP and the public-link
ZIP, is **LGPL-3.0-only** and shipped inside every published backend image while
the project declares MIT. It is replaced by an in-tree streaming ZIP writer.

This is the same class of problem as the GPL date-picker removed from the desktop
client in 1.1.0. LGPL would have permitted keeping it with prominent notice, so
this was a choice rather than a legal necessity - taken to restore a genuinely
permissive-only dependency tree.

Nothing about downloads changes: archives are still STORED, still streamed with a
real `Content-Length`, and the new writer was validated against a 4 GiB+ archive
(zip64 offsets past the 32-bit boundary, verified by an independent
implementation) as well as unicode names, empty members, and duplicate names.

## Security

- **OIDC accepted plaintext `http://` issuers.** Worse than it sounds: the token
  exchange POSTs your provider's **client secret** to whatever token endpoint the
  discovery document names, so a plaintext issuer put that secret on the wire in
  cleartext **on every login**, and a network attacker could rewrite discovery to
  redirect it. HTTPS is now required. If you genuinely run an internal IdP without
  TLS, `OIDC_ALLOW_INSECURE_HTTP=true` is an explicit, documented opt-out.
- **Omitting the `User-Agent` header suppressed the new-device alert.** The check
  bailed out on an empty fingerprint, so an attacker who simply left the header
  off recorded no known device, sent you no alert, and had the login logged as a
  familiar one - on every sign-in method.
- **A lockout never really expired.** Only a *successful* login reset the failure
  counter, so after the first lockout the count stayed at the threshold and the
  next single wrong password re-locked the account. A user who mistypes could be
  locked out indefinitely, and anyone could keep an account locked with one
  attempt per window.
- **The rich-text editor parsed untrusted HTML into the live page**, so event
  handlers in stored content ran before the editor stripped them. Now parsed in an
  inert document, where scripts cannot execute and no resource URLs are fetched.
- **Stored MIME types reached the `Content-Type` header unchecked** on the two
  download routes (previews were already sanitised), which could make a file
  permanently undownloadable.
- **A display name containing a newline silently killed other people's
  notification emails** - three subjects interpolate the sender's name, and the
  mail library rejects such a header outright.
- **A crafted config-backup file could exhaust the server's memory** during
  import: the scrypt cost parameters were read from the file with no bounds, and
  they are read *before* the passphrase is checked.
- **The container holding the Docker socket ran an end-of-life base image.**

## Localisation

**Error messages are now translated.** 214 error codes existed; only 68 had
translations, and the UI quietly fell back to the raw English backend string - so
it never looked broken, it just wasn't German. 99 user-reachable codes are now
translated in both languages: sign-in and 2FA, uploads and downloads, previews,
public links, share validation, and the full SSO failure set.

Codes only an administrator can reach are deliberately left untranslated rather
than padded out with strings nobody will read.

## Reliability

- **Signing in froze the whole server** for the duration of the password check
  (~0.2s, more for recovery codes), because the hash ran on the main loop. So did
  every direct upload, for the whole file - and the "same-filesystem rename" that
  was supposed to make finalising cheap was in fact a full copy, because uploads
  and storage are separate mounts. Both now run off the request loop.
- **`/api/health` could hang for 30 minutes.** v2.2.0 raised the antivirus scan
  timeout so multi-GB scans would stop failing, but the health check shares that
  connection helper - so an unresponsive scanner tied up a worker for the full
  scan timeout on an endpoint that is public and unthrottled. Probes now have
  their own short timeout. **This was a regression introduced by v2.2.0.**
- **A non-default `DB_NAME` broke first boot.** The database init hardcoded the
  name, so the stack never came up and the utf8mb4 collation was never applied.

## Also

- Backup/restore scripts, approval-workflow edge cases, inbound-mail resource
  limits and a set of smaller correctness items were triaged and deferred rather
  than rushed; they are tracked for the next pass.
- Accessibility (17 findings) and documentation drift (11) were deliberately
  scoped out of this release.

Backend suite 1105 → 1165 tests. Every fix was confirmed to fail against the
previous code before being applied.

# file:Heron v1.46.0

**Access-policy hardening.** Tightens three defaults from the audit so the product is
safer out of the box. Backend-only; no database migration.

## What's changed

- **Public links and API tokens are staff-only by default.** On a server that hasn't
  customised the policy, only employees and admins can create public download links
  or API tokens now (previously any client could). You can still open this up - set
  the policy to "everyone", or allow specific clients/groups - under
  *Settings -> Advanced* / the API-token + public-link policy screens. Existing custom
  policies are unchanged.
- **Signed download links expire within an hour.** The one-click download URL's
  maximum lifetime is capped at 1 hour (was 24). It's a transferable link to the file,
  so a shorter ceiling limits the window if one is forwarded or leaked; the default is
  still 15 minutes, which is plenty for browser resume.
- **Share creation is rate-limited per user.** A single account can now create at most
  60 shares per 15 minutes, so a compromised or abusive account can't flood colleagues
  with unsolicited shares. Far above any normal use.

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system`. No database
  migration. **If you currently rely on clients creating public links or API tokens,**
  set the relevant policy back to "everyone" (or allowlist them) after updating.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.46.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.46.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.46.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.46.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.46.0`

Click **Update** in `/admin/system` to roll forward.

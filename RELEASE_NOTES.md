# file:Heron v1.49.0

**Scoped API tokens.** You can now mint an API token that's limited to *specific*
actions instead of carrying your full account access. Create a token that can only
create a share and upload files, for example, and it will be refused if it tries to
download other files, list shares, delete anything, or touch your account. Existing
tokens are unchanged - they keep full access until you choose to replace them with a
scoped one.

## What's new

- **Per-token scopes (least privilege).** When you create a token (under *Account →
  API tokens*, or *Admin → API tokens* for a token on someone's behalf) you can now pick
  **Full access** (the default, same as before) or **Limited access** and tick exactly
  what the token may do:
  - **Sharing:** create shares · add files to existing shares · list & read shares ·
    manage shares (edit/expire/revoke) · search recipients · create & revoke public links
  - **Files:** upload · download & preview · delete
- **Everything else is refused.** A limited token that tries anything outside its scopes
  gets a clean `403 INSUFFICIENT_SCOPE` telling it which scope it was missing - it cannot
  reach any other part of the API. Public-link creation is its own scope, so a plain
  "send files" token can't expose files to the world unless you grant it.
- **Existing tokens keep working.** A token with no scopes set means *unrestricted* -
  identical to today. Nothing you've already issued changes.
- **Scopes are visible.** Each token shows its granted scopes (or "full access") in the
  list, and a programmatic client can read its own scopes from
  `/api/account/api-tokens/current`.

## Why

An automation credential should be able to do its one job and nothing more. Before this,
a token stolen from (say) a backup script could download every file the owner could
reach; now that script's token can be limited to "upload + create share" and is useless
for anything else.

## Notes for the security-minded

- Scopes only ever *narrow* a token below its owner's permissions - they can never grant
  more than the user already has, and browser/session logins are completely unaffected.
- Enforcement is deny-by-default and guarded by a test that fails the build if any
  token-reachable endpoint is ever left ungated, so the restriction can't silently
  develop a hole as the API grows.

## Upgrade notes

- Backend rolls forward via **Update** in `/admin/system`. The release adds one
  **re-runnable, back-compatible database column** (`api_tokens.scopes`, nullable) -
  applied automatically on update, no host step, and existing tokens stay full-access
  (the column is NULL for them).

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.49.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.49.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.49.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.49.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.49.0`

Click **Update** in `/admin/system` to roll forward.

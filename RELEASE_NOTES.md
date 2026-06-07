# file:Heron v1.40.0

**Correctness & privacy.** Two more audit fixes: notification side-effects now wait
for the action to actually commit, and share recipients can no longer see the full
co-recipient list. Backend-only; no database migration.

## What's fixed

- **No more emails for actions that didn't happen.** A notification email (and the
  in-app bell update) used to be sent the moment a notification was prepared, even
  if the underlying action was then rolled back by an error - so a recipient could
  get a "files were shared with you" email whose link led nowhere. Notifications now
  fire only after the action has successfully saved, and are dropped if it's rolled
  back.
- **Share recipients can't enumerate each other.** When you open a share you
  received, the app no longer reveals the full list of other recipients - you see
  only yourself (and any of your own groups). The sender, admins, and approvers
  still see the complete recipient list, as before.

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system`. No frontend
  change, no database migration, no configuration change.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.40.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.40.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.40.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.40.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.40.0`

Click **Update** in `/admin/system` to roll forward.

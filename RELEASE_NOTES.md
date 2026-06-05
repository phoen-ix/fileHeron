# file:Heron v1.10.0

**Admin file management.** File History is tidier, admins can delete files, and
each user's detail page now shows what they currently have stored.

## What's new

- **File History hides dead files by default.** Deleted files and abandoned
  (failed) uploads are no longer shown unless you tick **"Show deleted /
  abandoned"**. Picking a specific state from the dropdowns still surfaces them
  on demand.
- **Admins can delete a file.** Each File History row now has a **Delete**
  action (orphans keep their existing **Reclaim** action). It hard-deletes the
  bytes, frees the uploader's storage, is recorded in the audit log with the
  admin as the actor, and — if it was the last live file in a share — revokes
  that share automatically.
- **"Current files" on the user page.** `Admin → Users → <user>` now lists the
  files that user currently has stored (with sizes), explaining their Storage
  figure, with a per-file **Delete** button and a link to the full File History
  filtered to that user.

No `.env` change, no database migration. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.10.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.10.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.10.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.10.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.10.0`

Click **Update** in `/admin/system` to roll forward.

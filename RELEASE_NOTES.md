# file:Heron v1.12.0

**Add files to an existing share.** The person who created a share can now add
more files to it at any time while it's still active — no need to create a new
share. Open the share, click **+ Add files**, drop or pick the files, and upload.
The new files go through the same virus scan and appear in the share alongside
the originals.

- **Owner only.** Just like the original upload, only the share's creator can add
  to it, and only while the share is **active** (not expired or revoked).
- **Optional notification.** An *"Also notify recipients of the new files"*
  checkbox (defaulting to your usual share-notify setting) sends recipients a
  short "new files were added" email + in-app notice. Leave it unchecked to add
  files quietly — recipients will simply see them next time they open the share.
- Same upload experience as creating a share (drag-and-drop, resumable for large
  files), reusing the existing pipeline.

No `.env` change, no migration. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.12.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.12.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.12.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.12.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.12.0`

Click **Update** in `/admin/system` to roll forward.

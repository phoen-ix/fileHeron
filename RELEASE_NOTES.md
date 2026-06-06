# file:Heron v1.18.1

**Fix: admin CSV exports now download correctly.** The **Export CSV** buttons on
**Audit log** and **Mail log** were triggering the download as a plain browser
link, which couldn't carry the admin sign-in and so failed with an
authentication error. They now fetch the file through the authenticated app
connection, so the export works as expected. The new **Analytics** export already
worked this way and is unchanged in behaviour.

## What changed

- **Audit log → Export CSV** and **Mail log → Export CSV** now download reliably
  for signed-in admins (previously they could fail to authenticate).
- Internally, all three admin CSV exports (audit, mail, analytics) now use one
  shared, authenticated download path, with a brief disabled state while the
  file is fetched and a clear error toast if it can't be retrieved.

## Good to know

- **Admins only**; no change for employees or clients.
- **No database migration and no `.env` change** — purely a client-side fix to
  how the export request is made.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.18.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.18.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.18.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.18.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.18.1`

Click **Update** in `/admin/system` to roll forward.

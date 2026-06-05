# file:Heron v1.10.1

**Performance, security hardening, and internal cleanup.** No new features; this
makes a few hot paths faster, closes two rate-limit gaps, and de-duplicates
front-end code.

## Performance

- **Faster group lists.** The share-create recipient picker and the admin groups
  list previously ran one member-count query per group; they now do it in a
  single query. Group detail also loads its members in one go instead of one
  query per member.
- **New index** on `files(uploaded_by_id, state)` — speeds up the per-user
  storage sum used by the admin user list, the user Files section, and the
  hourly quota reconcile. **A migration adds it automatically on update.**

## Security

- **Rate-limited password endpoints.** "Reset password" and "Change password"
  now enforce the same per-IP rate limit as login / register / forgot, so they
  can't be hammered.

## Internal

- De-duplicated the front-end: a shared pager component, a debounced-search
  helper, and a single share-state pill mapping replace ~5–7 copies each. No
  visible change.

No `.env` change. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.10.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.10.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.10.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.10.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.10.1`

Click **Update** in `/admin/system` to roll forward.

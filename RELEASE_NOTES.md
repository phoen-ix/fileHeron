# file:Heron v1.26.0

**Safer in-app updates: automatic rollback + a release boot-gate.** After the
v1.25 update incident — a packaging bug took the site down and the in-app
rollback couldn't recover it — this release makes updates **self-healing** and
stops a broken build from ever being offered in the first place.

## What's new

- **Failed updates roll themselves back.** If an update doesn't come up healthy,
  the updater now automatically restores the previous version and brings the
  site back — no manual rollback, no command line, no waiting. Instead of a
  "Bad Gateway" you'll see an amber *"update failed — automatically rolled back
  to <version>"* notice on the System page.
- **Rollback survives database changes.** Rolling back across a database update
  used to get stuck. The updater now records the database revision before each
  update and moves it back on rollback **without deleting anything** — your data
  is preserved. This also repairs the manual **Roll back** button for the same
  case.
- **Broken releases can't be installed.** Every release now boots the actual
  backend image against a throwaway database and confirms it answers *before* the
  image is published. A build that can't start — a missing dependency, a broken
  migration — fails the release and never appears in your Update banner. This is
  the safeguard that would have caught the v1.25.0 problem.

## Good to know

- **No action needed** — these protections apply automatically from this version
  onward, including the update *to* v1.26.0 itself.
- **If automatic rollback can't recover** (rare — e.g. the previous version also
  refuses to start), the System page shows a clear failure with the reason and
  asks for operator help. It never silently leaves the site on a broken version.
- **No database changes** in this release.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.26.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.26.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.26.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.26.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.26.0`

Click **Update** in `/admin/system` to roll forward.

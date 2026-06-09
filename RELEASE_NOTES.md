# file:Heron v1.55.11

**Admin settings pages now use the full width.** The form/settings pages (Error alerts, General,
Advanced, Branding, Email, 2FA, the policy pages, SSO, Maintenance, …) were each capped to a
narrow column (~720-1100px) while the rest of the admin (logs, lists, Scheduled tasks) already
filled the screen. They now fill the content width too, consistent with everything else.

## What's new

- **Settings pages fill the width.** Removed the per-page width caps so every admin settings page
  uses the full content area instead of a narrow centered column - no more "limited" feeling and
  no wasted right-hand space.
- General's content column now expands (its quicknav rail stays on the right).

## Notes

- Pure layout/CSS change - no behavior, data, settings, or API changes.
- Descriptive intro/help **text** stays at a readable line length (it isn't stretched across the
  whole screen), and the Branding logo preview keeps its size - only the form/content area widens.
- Single-column forms now have wide inputs; if a specific page reads better in two columns, that's
  an easy per-page follow-up.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker rebuilt at the
  same version, code unchanged), **no migration, no host step**. Rolling back to v1.55.10 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.11`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.11`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.11`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.11`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.11`

Click **Update** in `/admin/system` to roll forward.

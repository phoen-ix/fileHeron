# file:Heron v1.55.2

**Use the whole screen.** The app was hard-capped at 1152px wide and centered, so on
a common 1920x1080 (or 1440p) monitor a big band of space was wasted in each side
gutter while data-dense admin pages got squeezed. The layout now widens to use that
space, and the Scheduled-tasks table gets a styling pass so the reclaimed room is put
to work instead of left as gaps.

## What's new

- **Wider, fluid layout, app-wide.** The shared page width goes from a fixed 1152px to
  `min(94vw, 90rem)` - it scales with the screen up to ~1440px and stays edge-aligned
  with the top header on every page (dashboard, shares, files, and the whole admin
  area). On a 1920px screen the usable content area grows roughly +35%.
- **Scheduled tasks, de-crammed.** The cron table (Admin → Scheduled tasks) was
  effectively unstyled, so its rows wrapped and ran together. It now has proper column
  headers, cell padding and hairline rules, the schedule/status/next cells no longer
  wrap, and the task column absorbs the extra width.

## Notes

- Pure layout/CSS change - no behavior, data, settings, or API changes.
- Long-form reading pages (e.g. the legal pages) keep their narrower, more legible
  width on purpose; only the operator/data surfaces widen.
- Below ~720px the admin sidebar still collapses and wide tables scroll within their
  own box rather than breaking the page.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker are
  rebuilt at the same version, code unchanged), **no migration, no host step**. Rolling
  back to v1.55.1 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.2`

Click **Update** in `/admin/system` to roll forward.

# file:Heron v1.55.9

**Scheduled tasks: all sections now share one full-width column layout.** Each job group
(Shares & files, Mail, Maintenance, Operations) was its own table that shrank to fit its own
content, so the sections ended at different right edges, their columns didn't line up, and the
right side of the page was unused. They now all use a single fixed column template at full
width.

## What's new

- **Every group is the same width and lines up.** All sections span the full content width and
  end at the same right edge, with columns (Task / Schedule / Recent / Next run / actions) that
  line up vertically from one group to the next - no more ragged section widths or wasted
  right-hand space.

## Notes

- Pure layout/CSS change - no behavior, data, settings, or API changes.
- On very narrow windows the table scrolls inside its own box rather than breaking the page.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker are
  rebuilt at the same version, code unchanged), **no migration, no host step**. Rolling
  back to v1.55.8 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.9`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.9`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.9`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.9`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.9`

Click **Update** in `/admin/system` to roll forward.

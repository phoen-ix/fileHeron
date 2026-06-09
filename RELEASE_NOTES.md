# file:Heron v1.55.5

**Full-width layout.** Earlier v1.55.x steps widened the layout but still capped it at
1440px and centered it, so wide monitors kept showing empty bands on both sides. The
shared page width is now **100%** - the content and the top header both fill the whole
window, with only the normal page gutter padding the edges.

## What's new

- **Use the entire window width.** The shared `--fh-max-width-page` cap goes to **100%**
  (was a centered 1440px max). Every operator page and the top header now span the full
  viewport instead of sitting in a centered column - no more wasted left/right gutters
  on 1080p, 1440p, or ultrawide screens.
- **Header included.** The header shares the same width token, so it stays edge-aligned
  with the content at full width.

## Notes

- Pure layout/CSS change - no behavior, data, settings, or API changes.
- Long-form reading pages (e.g. legal pages) keep their separate, narrower
  reading-width cap on purpose, so body text stays legible and doesn't stretch.
- (v1.55.4 was a mis-tagged no-op build identical to v1.55.3; this is the release that
  actually ships full width - update straight to v1.55.5.)

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker are
  rebuilt at the same version, code unchanged), **no migration, no host step**. Rolling
  back to v1.55.3 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.5`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.5`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.5`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.5`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.5`

Click **Update** in `/admin/system` to roll forward.

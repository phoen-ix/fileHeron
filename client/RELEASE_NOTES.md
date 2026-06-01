# Desktop client 0.9.2

A slimming + packaging release. No change to how you sign in or use the app.

## Smaller download

The Windows `.exe` is **substantially leaner**. Two things were bloating the
bundle:

- **Babel locale data** — the date picker pulled in the *entire* Unicode CLDR
  database (~30 MB across 1,000+ locale files). The app only ever shows dates in
  English or German, so the build now ships just those (plus the universal
  fallback) and drops the rest.
- **Pillow** — an image library pulled in transitively but never actually used
  (the window icon uses Tk's own image loader). It's now excluded from the build.

Same features, same behavior — just a faster download and less disk.

## Small fix

- The expiry **date picker now follows the app language** (English/German) instead
  of the operating system's locale. Dates were already displayed as `YYYY-MM-DD`;
  this makes the pop-up calendar's month/day names match the rest of the app, and
  is what lets the build above ship without every locale's data.

---

**Requires:** Windows. Download `fileheron-client.exe` below.

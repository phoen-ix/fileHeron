# file:Heron v1.48.0

**Legal-page editor: one language at a time.** The *Imprint* and *Privacy* editors
under *Admin -> Settings -> Branding & legal* now switch language with a tab instead
of showing every language side by side. Frontend-only; nothing about how legal pages
are stored, saved, or published changes.

## What's changed

- **A language tab above the legal editors.** Previously both languages sat next to
  each other in a two-column grid, so each Markdown editor was squeezed into half the
  width - and a third language would have made it unusable. Now a single
  `English | German` tab sits above *Imprint* and *Privacy*, and only the selected
  language is shown, at full width, for both documents at once.
- **No lost keystrokes when you switch.** Every language's editor stays mounted behind
  the scenes (just hidden), so flipping the tab mid-edit never drops the last thing you
  typed - switch to German, type, switch back, and your English text is exactly as you
  left it. Save still writes **both** languages every time, regardless of which tab is
  open.
- **Ready for more languages.** Adding a language later now adds a tab, not another
  column, so the editor stays readable no matter how many languages there are.

## Also in this release

- **Housekeeping:** the admin-sidebar taxonomy test now reflects the *Maintenance* and
  *Config backup* pages added in v1.34 / v1.33 (a stale count that had been failing
  silently), and the legal-page renderer + branding editor are lint-clean. No
  user-visible effect.

## Upgrade notes

- Frontend rolls forward via **Update** in `/admin/system`. **No database migration,
  no configuration change**, no host step.
- Nothing about existing legal-page content changes - the same per-language text is
  stored and the public `/imprint` and `/privacy` pages render exactly as before. This
  is purely how the editor is laid out for admins.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.48.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.48.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.48.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.48.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.48.0`

Click **Update** in `/admin/system` to roll forward.

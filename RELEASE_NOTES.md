# file:Heron v1.50.0

**A real rich-text editor for legal pages and emails.** The editor for *Imprint /
Privacy* pages and *email templates* was a bare 5-button Markdown box that couldn't
do tables or text alignment. It's been replaced with a proper visual HTML editor:
alignment, tables, underline, more, all in a what-you-see-is-what-you-get toolbar.

## What's new

- **Full formatting toolbar** - headings (H1-H6), **bold / italic / underline /
  strikethrough / inline code**, **left / center / right / justify alignment**,
  bulleted & numbered lists, quotes, code blocks, horizontal rules, **links**,
  **images by URL**, and **tables** (insert, add/remove rows & columns) - plus
  undo/redo.
- **What you see is what you get.** Content is authored and stored as HTML, so the
  editor shows the real result instead of Markdown source.
- **Still locked down.** Legal pages are public, so every page is sanitised on the
  server with a tight allowlist - alignment is applied through a fixed, safe set of
  classes (no arbitrary styles or scripts can ever be stored). Email bodies are
  sanitised the same way, and alignment is auto-inlined so it survives in mail
  clients like Outlook.
- **Built on open foundations.** The editor is built directly on **ProseMirror**
  (MIT-licensed) - no third-party editor vendor. It's lazy-loaded, and the bundle is
  actually *smaller* than the old one.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. The update **migrates your
  existing legal-page and email-template content from Markdown to HTML automatically**
  (one-time, on upgrade) - nothing is lost, and pages keep rendering as before.
- **Re-styling old content:** previously-written text comes across as clean
  paragraphs; use the new toolbar to add alignment/tables/etc. where you want them.
- **Rolling back** to a pre-v1.50 image after updating is possible but the converted
  legal/email content is now HTML, so the old (Markdown) renderer would show it
  unformatted - re-save those pages if you roll back.
- One re-runnable, back-compatible database column is added; no host step.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.50.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.50.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.50.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.50.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.50.0`

Click **Update** in `/admin/system` to roll forward.

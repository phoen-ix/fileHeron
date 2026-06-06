# file:Heron v1.25.0

**Editable email templates.** Admins can now rewrite the wording of every email
file:Heron sends — subject and body — **per language**, from a new editor in the
admin area. A built-in WYSIWYG editor, friendly placeholders, live preview, and a
"send a test to myself" button make it safe to change copy without touching code
or shipping a release.

## What's new

- **Edit any email, in any language** at *Settings → Email templates*. Pick a
  template, switch between languages (English / German today; any future language
  appears automatically), and edit the subject and body.
- **A real WYSIWYG editor** (bold, italic, headings, lists) — no HTML or template
  syntax to learn. Built on the MIT-licensed Milkdown editor; loaded only on this
  page so the rest of the app stays lean.
- **Friendly placeholders.** Insert tokens like `[RECIPIENT]`, `[SHARE_LINK]` or
  `[RESET_LINK]` from a toolbar menu; each template shows a collapsible list of the
  placeholders it supports. They're replaced with the real values when the email
  is sent.
- **Live preview & test send.** Preview the rendered email (HTML and plain-text)
  with sample data right in the page, or send a test to your own address through
  the configured SMTP server.
- **Reset to default.** Every customisation can be reverted to the built-in text
  per template and language, any time.

## Good to know

- **Nothing changes until you save.** Each template falls back to its built-in
  default until an admin saves a custom version; existing installs are unaffected.
- **Security is preserved.** Your text is sanitised before sending (no scripts or
  unsafe markup), dynamic values are always escaped, and the one-time links in
  password-reset / verify / invite / email-change mails keep working and stay
  masked in the mail log — the editor won't let you remove a required link from
  those mails.
- **Auth emails gain a branded look.** The handful of security emails that were
  previously plain-text only (password reset, verify, invite, lockout,
  email-change) get the standard branded HTML layout once you customise them.

## Upgrade notes

- **One small migration** adds a table to store template overrides. It's
  re-runnable and applied automatically on update; no existing data changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.25.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.25.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.25.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.25.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.25.0`

Click **Update** in `/admin/system` to roll forward.

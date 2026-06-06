# file:Heron v1.15.0

**One unified admin menu — Settings is no longer a place you go, it's part of
the sidebar.** Until now `/admin` had two competing navigation styles: top-level
pages (Users, Groups, Audit log…) *plus* a separate **Settings** hub you had to
click into and then navigate its own list. Every settings page was second-class
— two clicks deep and disconnected from the operational page it relates to.

This release folds all of that into a single, categorized, collapsible left menu:

- **Every settings page is now a first-class sidebar entry.** No more hub page.
- **Grouped by topic into four collapsible categories** — *Access · Sharing ·
  Messaging · System* — so related config sits next to the page it affects.
  Email / SMTP lives under **Messaging** beside the Mail log; Quarantine alerts
  under **Sharing** beside the Quarantine queue; 2FA, SSO and the API-token
  policy under **Access** beside Users, Groups and Sessions.
- **You choose how the categories collapse** — a new preference under
  **Account → Admin sidebar**:
  - **Accordion** (default) — one category open at a time; opening one closes
    the rest. Most compact.
  - **Manual** — open as many as you like; each group toggles independently and
    your choices stick.
  - **Always expanded** — everything open, closest to a flat list with headers.
- **Your open/closed groups sync to your account**, so the sidebar looks the
  same on every browser and device you sign in from.

### Good to know

- **It always shows you where you are.** The current page's category expands
  automatically, and detail screens (a specific user, a mail-log entry, an SSO
  provider) highlight their parent entry and open its group.
- **Clearer labels.** The two pairs that used to read identically are now
  disambiguated: **API tokens** (the token list) vs **Token policy** (the rule),
  and **Quarantine** (the queue) vs **Quarantine alerts** (the notification
  setting).
- **Nothing moved or disappeared.** Every settings page keeps its URL —
  bookmarks to a specific settings page still work. The old **Settings** hub
  bookmark now lands on **General**.
- Smooth expand/collapse, keyboard-operable category headers, and it respects
  *reduce motion*. **Admins only** — employees and clients see no change.

### Upgrade notes

- **One small migration, no `.env` change.** Two columns are added to the
  `users` table to remember each admin's sidebar mode and open groups; this runs
  automatically when you update. New interface strings ship in English and
  German.
- First load after updating uses the default (**Accordion**) until you pick a
  different mode under **Account → Admin sidebar**.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.15.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.15.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.15.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.15.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.15.0`

Click **Update** in `/admin/system` to roll forward.

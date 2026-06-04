# file:Heron v1.9.1

**Accessibility & UX polish.** Consistent confirmation dialogs, better keyboard
and screen-reader support, and clearer empty states.

## What changed

- **Styled confirmation dialogs.** Destructive actions (revoke a session or
  token, delete a group, remove a member, end a share, disconnect SSO, remove a
  passkey, reclaim a file, …) now show an in-app confirm dialog matching the
  design system instead of the browser's plain pop-up. The dialog traps focus on
  the confirm button, closes on Escape or backdrop click, and highlights
  destructive actions in red.
- **Keyboard focus follows navigation.** Moving between pages now sends focus to
  the main content region (unless a page already focuses an input), so
  keyboard/screen-reader users aren't left inside the previous page's menu.
- **Screen-reader labels** added to the language switcher, the notification list,
  and the Shares list filters.
- **Empty states.** The admin Users list now shows a clear "no users match"
  message instead of an empty table.

No `.env` change, no migration. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.9.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.9.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.9.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.9.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.9.1`

Click **Update** in `/admin/system` to roll forward.

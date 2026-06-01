# Desktop client 0.9.1

A UX-polish release focused on the sign-in experience and the session
lifecycle. The client now lives in a **single window** for the whole session.

## Highlights

- **Login is now an in-window overlay.** Instead of a separate login window
  popping up *before* the main window, the app opens directly and presents the
  sign-in form as a dimmed overlay with a centered card. One window, start to
  finish.
- **Signing out no longer closes the app.** After you sign out you land back on
  the sign-in overlay (server and email pre-filled, secrets cleared), ready to
  sign in again — no relaunch needed.
- **Expired sessions recover gracefully.** If your session is revoked or
  expires (an admin disables the account, the refresh token is no longer
  valid, …), the app returns you to the sign-in screen with a clear
  *“Your session expired — please sign in again”* message instead of leaving
  you stuck on a dead screen.

## Polish

- The main window and every dialog now open **centered on screen** rather than
  in the top-left corner.
- A **progress indicator** runs during sign-in, so a slow connection no longer
  looks like a frozen button.
- The **recipient picker is fully localized** now (German + English) — a few
  buttons and labels that were still English-only have been translated.

## Under the hood

- The login → main → sign-out → session-expiry flow is coordinated by a single
  controller in one Tk mainloop (no modal `wait_window`), which also sidesteps
  the long-standing Windows “invisible window” race more cleanly.
- Client-only release — no server/API changes; it talks to the same REST API.

---

**Requires:** Windows. Download `fileheron-client.exe` below.

# Desktop client 0.9.12

Cleaner session handling on close, and the app now shows which API token it's
signed in with.

## Sign out cleanly when you close the app

If you sign in with **email + password**, closing the app normally now revokes
that session on the server, instead of leaving it to expire on its own. Closing
the window does the tidy thing — no piled-up sessions in your Account page.

This is best-effort and quick: if the server is unreachable, the app still
closes promptly (within a few seconds) rather than hanging. Signing in with an
**API token** doesn't create a session, so nothing is revoked on close — your
token keeps working for the next launch, as before.

## See which API token you're using

When you sign in with an API token, **Settings** now shows that token's name and
`fh_…` fingerprint, with a note on where to revoke it in the web app (Account →
**Connected API clients**). Handy if you have several tokens and need to find the
right one to disconnect this device.

> Requires server **v1.5.3+** to show the token *name* and last-used; against
> older servers the app still shows the `fh_…` fingerprint.

---

**Requires:** Windows. Download `fileheron-client.exe` below.

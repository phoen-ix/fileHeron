# Desktop client 0.9.14

Signs you in automatically when your API token is saved.

## Auto sign-in

If you sign in with an **API token**, the token is kept in **Windows Credential
Manager**. On the next launch the app now uses it to **sign you in
automatically** — no clicking *Sign in*, straight to your files.

- Only applies to API-token logins (password logins still ask each time — the
  password is never stored).
- If the saved token has been revoked or the server is unreachable, the app
  falls back to the normal login screen so you can fix it.
- Don't want it? **Settings → Sign out** clears the saved token, and the next
  launch shows the login screen again.

---

**Requires:** Windows. Download `fileheron-client.exe` below.

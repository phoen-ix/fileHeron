# Desktop client 0.9.3

A sign-in flow that works the way you expect.

## Two-step second factor

If your account has two-factor authentication, the client now asks for your
code **the normal way**:

1. Enter your email and password and click **Sign in**.
2. *Only if* 2FA is on, a single **Authentication code** field appears.

Previously the code field was shown up front on the first screen, before you'd
even entered your password — out of step with how every other app does it.

## One field for code *or* recovery

That second-factor field accepts **either** your 6-digit authenticator code
**or** one of your recovery codes (`XXXX-XXXX`). You no longer have to flip a
"use a recovery code instead" switch — just type whichever you have and the
client figures out the rest. There's a **← Use a different account** link if you
need to go back and re-enter your email/password.

No change to API-token sign-in, and nothing to reconfigure.

(The web app gets the same single-field treatment in this release.)

---

**Requires:** Windows. Download `fileheron-client.exe` below.

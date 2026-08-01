# Desktop client 1.3.1

**Two fixes, both about telling you the truth.** Expiry times were rendered *and
read back* in your laptop's timezone while the web interface uses the instance's,
and every error the server sent arrived in English no matter which language you
had chosen.

Both were found in the 2026-08-01 audit. Requires server **v2.6.1 or newer**.

*(1.3.1 replaces 1.3.0, which never shipped: the timezone fix silently did
nothing on Windows, because Windows has no time-zone database of its own and
the client fell back to the machine's local zone - the exact defect the fix
exists to close, on the only platform this client runs on. The database now
ships inside the .exe.)*

---

## Expiry times were up to a working day out

The web interface renders every date and time in the **instance's** timezone -
the one an administrator sets under Site settings - and interprets what you type
the same way. This client used **your machine's** timezone for both, and showed
no zone on either screen.

Concretely: an instance on Europe/Vienna, a laptop on America/New_York. You set
a share to expire at 17:00. The client sent 21:00 UTC. The recipient opened the
same share in their browser and saw 23:00. Neither surface said which zone it
meant, so you believed 17:00 and they believed 23:00 - and the same six hours
applied in reverse to any expiry set on the web and read here.

The client now reads the instance's timezone when you sign in, renders in it,
interprets the expiry picker in it, and **says which zone it is using** in the
dialog. A server too old to report one, or an unreadable value, falls back to
local time exactly as before.

## Errors arrived in English

Every label around them was translated; the one piece of text that mattered when
something went wrong was not. The client now translates the server's errors from
the error code - the stable part of the response - using the same wording as the
web interface, and falls back to the server's own text for a code this build has
never heard of. An expiry of "Never" is translated too.

---

**Requires:** Windows. Download `fileheron-client.exe` below.

Unsigned, as before: the `.exe` carries no code-signing certificate, so Windows
SmartScreen will warn on first run. That is a deliberate choice.

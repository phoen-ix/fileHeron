# Desktop client 1.2.0

**Five fixes to transfers and to signing out - including a resume that could
throw away everything it had already downloaded.** All were found in the
2026-07-30 audit; this is the build that carries them.

Requires server **v2.6.1 or newer**. See *Server compatibility* below.

## Downloads

**A failed size probe could discard a completed partial.** Every download opens
with a one-byte request to learn the file's size. If that probe failed, the
client still wrote a checkpoint recording the total as zero - and a zero total
can never match the real one on the next attempt. So the resume was refused, the
`.part` file was discarded, and every byte already fetched was fetched again. On
a 30 GB file that is the entire point of resumable downloads, undone by one
transient failure at the very start.

**Resume after restarting the app had no progress bar.** The downloads registry
recorded a total of zero for every fresh download, because it was written before
the first byte arrived and nothing wrote the real size back afterwards. The
Resume offered on the next launch worked; it just had nothing to draw a bar
against.

**A download that died before any bytes landed left a stray file behind.** An
orphaned `.fhdownload` bookmark stayed in your Downloads folder, referenced by
nothing and mentioned by no screen again.

## Uploads

**Direct uploads showed no progress at all.** Everything up to 100 MB - the
common case - reported once, after the transfer had already finished: the row sat
at "Pending", 0%, for the whole upload and then jumped straight to done. The
resumable path immediately beside it reported properly throughout, which made the
small-file path look stalled.

## Signing out

**Sign-out froze the window, and froze it longest when it mattered most.** The
settings overlay called the server on the interface thread, so the app stopped
responding for the round trip - and for the client's *full* network timeout when
the server could not be reached at all. That is exactly the situation in which
someone wants to sign out: a laptop that has left the office.

It now runs in the background, and the local credentials are cleared either way.
Signing out no longer depends on the server being reachable.

## Server compatibility

**Server v2.6.1 or newer.**

The one-byte size probe each download opens with is the reason. Server v2.6.0
briefly counted that probe as a download in its own right, which charged two
units of a share's budget for a single transfer and made a share limited to one
download impossible to fetch from this client - while a browser could still fetch
it. v2.6.1 fixed that on the server, so updating the server repairs any client
already installed; nothing here works around it.

Servers at v2.5.0 and earlier are fine too, for a different reason. **v2.6.0
exactly** is the one to avoid.

## Also

The bundled documentation described a program that did not quite exist - a test
count off by an order of magnitude, and a UI toolkit named in the test notes that
this client has never used. Corrected.

---

**Requires:** Windows. Download `fileheron-client.exe` below.

Unsigned, as before: the `.exe` carries no code-signing certificate, so Windows
SmartScreen will warn on first run. That is a deliberate choice.

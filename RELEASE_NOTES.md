# file:Heron v2.6.1

**A regression fix for v2.6.0.** If you run the desktop client, update before you use
it. No host step, no migration.

v2.6.0 made a `Range:` continuation prove itself instead of merely claiming to be
one. That was right, and it closed a real unlimited-download bypass. But the
desktop client opens **every** download with a one-byte ranged request to learn
the file's size and whether the server honours ranges - and that probe was
relying on the property v2.6.0 removed. So the probe started being charged as a
download.

The effect, on v2.6.0 only:

- every first download from the desktop client spent **two** units of a share's
  download budget instead of one, and appeared twice in the owner's download
  history and audit log;
- a share with `download_limit = 1` became **undownloadable from the client**:
  the probe spent the single allowance, and the transfer that followed was
  refused with `410 SHARE_DOWNLOAD_LIMIT_REACHED`. The same share downloaded
  normally in a browser, which is what made it look like a client bug;
- a limit of N gave the client floor(N/2) downloads.

Public links and browser downloads were unaffected.

## The fix

A download is now charged on **how much is being taken, not on where it starts**.

That distinction is the whole thing. `Range: bytes=1-` asks for the entire file
minus one byte - it is a download, and treating it as a free continuation is
exactly the bypass v2.6.0 closed. `Range: bytes=1-1` asks for one byte. The
server now recognises a single-byte range on a larger file as a size probe and
does not charge it, does not log it, and does not count it against the link's
budget. Everything else is unchanged: a genuine resume still has to be
corroborated, and a fabricated range still pays.

The exemption is deliberately one byte wide. Reading a file through it would
cost one authenticated, rate-limited request per byte, against a product whose
normal file is measured in gigabytes - and it would yield nothing the caller
could not get by spending a single download they are already authorised to make.
A download budget limits how many copies leave; it was never the thing deciding
whether this person may have one.

Updating the server repairs **already-installed** desktop clients. There is no
client update to install for this.

## How it was found, and what it says

This was the audit's own signature failure, committed by the remediation itself:
a docstring asserting a property the code no longer had. The client's probe
function documented its reasoning in full -

> Probes at `bytes=1-1` (not `0-0`): [...] A start > 0 is treated as an uncounted
> continuation.

- and v2.6.0 deleted that sentence's truth from the server without anyone
looking at the client that depended on it. The backend suite did not catch it
because no backend test sent the request the client actually sends.

It now does. There is a test named for the client's real byte sequence, and a
test that fails if the client's docstring drifts from the server's rule again.

## Upgrading

In-app Update, or `FH_TAG=v2.6.1`. Nothing else to do.

If you are still on v2.5.0 or earlier, you never had the regression - update
straight to v2.6.1 and read the v2.6.0 notes for what changed there.

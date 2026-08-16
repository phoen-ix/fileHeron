# Desktop client 1.4.4

**Signing in less often, and one wrong error message.**

A patch release, all of it in how the app renews your sign-in during long
transfers. Nothing about it is visible when everything is working; it matters on
a slow or restarting server.

Requires server **v2.6.1 or newer** (unchanged).

---

## A large download renewed your sign-in several times over

A big file is fetched over several connections at once, all sharing one sign-in.
When that sign-in came up for renewal mid-download — every fifteen minutes on a
long transfer — each connection renewed it separately.

The server allows exactly one of those, and treats the rest as suspicious. In
the worst case it reads as a stolen credential and revokes every session you
have, on every device, including your browser. On a long download that could
happen at every renewal.

The connections now share a single renewal.

## "Couldn't reach the server" when the server was fine

Resuming a download starts with a small probe request. If that probe met an
expired sign-in in a particular way, the app could retry it with the very
credential that had just been rejected, then report "Couldn't reach the server
to resume this download" — a wrong explanation for an expired session, and one
that repeats every time you try.

The probe now reports what it actually sent, so the renewal happens properly and
the download resumes.

---

# Desktop client 1.4.3

**Two faults found reviewing 1.4.2, one of them older than it.**

A patch release. 1.4.2 made Cancel stop the transfer instead of waiting for it;
this makes Cancel actually immediate, and fixes a much older bug that could
leave a downloaded file quietly incomplete.

Requires server **v2.6.1 or newer** (unchanged).

---

## A download could finish "successfully" while missing part of the file

The worst kind of bug for a file-transfer tool, and it predates 1.4.2.

A large file is fetched in several parts. If one part met an expired sign-in on
every one of its three attempts, that part gave up without reporting anything —
and the app treated silence as success. The finished file was the right size,
because the space is reserved up front, but the missing span was left as zeros.
Nothing said so, and resuming would not fetch it either, because that part had
been recorded as done.

It now fails the way any other unrecoverable part does, so the download reports
an error you can retry instead of handing you a damaged file.

## Cancel is now actually immediate

1.4.2 stopped waiting for the remaining parts, but still paused for up to two
seconds before removing the partial file — the wait was accidentally counting
parts that had already been dropped, so it always ran to its full timeout. It
now waits only for parts genuinely still running, which in practice means
Cancel returns at once.

# Desktop client 1.4.2

**Cancel stops now, and a failed download stops with it.**

Two faults in the same place: when a parallel download ended early - because
you cancelled it, or because one part of it failed for good - the app kept
downloading everything else first, and only then told you. Nothing was lost,
but a Cancel could take as long as the transfer it was cancelling.

Requires server **v2.6.1 or newer** (unchanged from 1.4.1).

---

## Cancel and Pause take effect immediately

A large file is fetched in several parts at once. Stopping the transfer waited
for every part still queued to be started and every part already running to
finish, before the app acted on your click. On a slow connection that is the
rest of the download - the one thing you had just said you did not want.

The remaining parts are now dropped, and the ones already running are told to
stop rather than discovered to have stopped.

## A part that fails for good no longer downloads the rest first

If one part of a download failed permanently, the app waited for the other
parts to finish downloading their share of the file, then threw all of it away
and reported the error it had been holding the whole time. On a multi-gigabyte
file that is hours of transfer with a guaranteed-useless result.

It now reports the failure straight away and stops the other parts. The partial
file and its resume point are kept exactly as before, so **Resume** still picks
up where it left off.

## Resuming after an interrupted download re-fetches less

Parts that had finished when a download was interrupted were sometimes not
recorded as finished, so resuming downloaded them a second time. The bytes were
always correct - this only cost time and bandwidth.

## A sign-in warning that could freeze the app

If signing in with an API token succeeded but the token could not be saved to
your keyring, the notice explaining that was delivered in a way that can lock up
the window on Windows. It now goes through the same path as every other message
from a background task.

# Desktop client 1.4.1

**Long transfers no longer die when your sign-in gets old.**

A download that took longer than your session's access token simply stopped -
and it stopped looking like a network problem, so there was nothing to tell you
that signing in again would fix it. Large files were the ones that hit it,
because they are the ones that take long enough.

Requires server **v2.6.1 or newer**.

---

## Downloads survive an expiring sign-in

Every byte transfer sent the same authorisation header it had captured when the
transfer started. Sessions expire (15 minutes by default), and nothing renewed
that header mid-transfer - so a segmented download of a large file would run
until the token aged out and then fail, no matter how much had already been
transferred. Resuming hit the same wall immediately.

Transfers now renew the session automatically and carry on. If the session has
genuinely ended, the app says so and returns you to the sign-in screen, instead
of reporting it as a connection failure.

## "Couldn't reach the server" when the server was fine

Resuming a paused download with an expired sign-in reported
`Couldn't reach the server to resume this download; try again` - and trying
again reproduced it forever, because nothing was renewing the session. That
message now only appears when the server really is unreachable.

# Desktop client 1.4.0

**The Windows release that actually gets tested on Windows.**

1.3.0 was tagged and never shipped: the timezone support it existed for was
silently dead on Windows, and nothing in CI had ever run this suite on the
platform this program is built for. Fixing that properly found a family of
Windows-only faults behind it. This release is that family.

Requires server **v2.6.1 or newer**.

---

## Expiry times were still wrong when you CREATE a share

1.3.1 fixed the *edit* dialog and left the *create* panel alone, so the two
halves of one feature disagreed with each other. Setting an expiry while
creating a share still used your laptop's timezone, not the instance's - and the
create panel didn't say which zone it meant, so there was nothing to notice.

Both surfaces now read the instance's zone, both label it, and both interpret
what you type the way the web interface does.

## Downloads no longer break on ordinary filenames

The server decides each file's name, and Windows forbids characters that are
perfectly legal everywhere else. A file called `Q3:final.xlsx` did not fail
visibly - it wrote into a hidden NTFS data stream, so the download "succeeded"
and nothing appeared in the folder. `<`, `>`, `"`, `|`, `?` and `*` failed the
save outright, and one such name in "Save all to folder" abandoned every file
after it in the batch with no error message at all.

Those characters are now stripped, each file in a batch stands on its own, and
anything that still cannot be saved is named in a message instead of vanishing.
Very long names are shortened to fit Windows' 260-character path limit rather
than failing at the last moment.

## Finishing a download no longer fails because something else was reading it

Windows refuses to rename a file another process has open - an antivirus
scanner, a search indexer, or a preview pane holding it for a fraction of a
second. A completed download could fail at the final step and then fail the same
way on every retry, permanently stuck at 100%. It now waits a moment and
retries.

## Large downloads start immediately

Reserving space for a multi-gigabyte download wrote the entire file in zeros
first, so a 4 GB transfer sat at 0% with no network activity for as long as the
disk took, then wrote it all over again for real. Space is now reserved without
that pass.

## Works behind corporate TLS inspection

The client now trusts the **Windows certificate store** as well as its built-in
list, so an organisation's own root CA - the kind a TLS-inspecting proxy uses -
is accepted exactly as your browser accepts it. Previously the browser reached
the server and the client alone could not, which looks like a server fault and
is not one. Uploads use the same trust, so a large upload can no longer fail
where signing in succeeded.

## Downloaded files carry their origin

Files saved by the client are now marked as coming from the internet, the same
mark browsers apply. Windows SmartScreen and Office Protected View treat them
with the same caution as a browser download - which matters, because the client
offers a one-click **Open** on a file the server named.

## Smaller things

- **"Folder" now selects the file.** It opened your default folder with nothing
  selected on every Windows machine.
- **File types are consistent between colleagues.** The type recorded on upload
  came from the uploader's Windows registry, so two people uploading the same
  `.csv` could label it differently for everyone who downloads it.
- **A token that can't be saved says so.** Where Windows credential storage is
  switched off by policy, sign-in worked and the token was silently discarded -
  you were asked for it again every launch with no explanation.
- **The timezone label is readable.** With no instance zone configured it showed
  a long localized Windows zone name; it now shows a plain UTC offset.
- `COM²`-style device names and a colon-prefixed drive letter are handled.

---

**Requires:** Windows. Download `fileheron-client.exe` below.

Unsigned, as before: the `.exe` carries no code-signing certificate, so Windows
SmartScreen will warn on first run. That is a deliberate choice.

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

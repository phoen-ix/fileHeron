# file:Heron desktop client

CustomTkinter desktop client for the file:Heron platform (migrated off
PySide6 in v0.4.0 - pure-Python GUI, ~10 MB of deps vs Qt6's ~150 MB).
Talks to the same REST API as the SPA - login (email/password + TOTP or
API token), browse Inbox & Outbox, download files, and create new
shares (direct multipart for ≤100 MB, TUS resumable for larger).

Windows-first. Linux + macOS source-runs the same; only the
release-build pipeline is Windows-only for now.

## Install (end users)

Grab the latest `fileheron-client.exe` from the
[releases page](https://github.com/phoen-ix/fileHeron/releases) and
run it. First launch asks for the server URL
(e.g. `https://files.example.com`) and credentials. Config is
stored at `%APPDATA%\fileHeron\config.json`. An **API token**, if you sign in
with one, is held in Windows Credential Manager (the OS keyring). A
password sign-in keeps its refresh cookie in memory for the life of the
process only - nothing about that session survives a restart, so you sign in
again next launch. (This said the refresh token was stored in Credential
Manager; it never has been - audit 2026-07-30, client-9.)

### Unsigned build - the SmartScreen warning is expected

The official `.exe` is **not code-signed**, so Windows SmartScreen shows a
"Windows protected your PC" prompt on first run - click **More info -> Run
anyway**. To confirm you downloaded an authentic, untampered build, verify it
against the `fileheron-client.exe.sha256` published next to it on the release:

```powershell
# PowerShell: this hash must equal the one in fileheron-client.exe.sha256
(Get-FileHash .\fileheron-client.exe -Algorithm SHA256).Hash.ToLower()
```

Want a signed binary? The build is fully reproducible from source - build it
yourself and Authenticode-sign it with your own certificate (see
[Build a Windows .exe locally](#build-a-windows-exe-locally)).

## Develop

```bash
cd client
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .[dev]
pytest                        # 147 tests across 29 files (no GUI, no network)
python -m fileheron_client    # launches the GUI against the server URL the config dialog asks for
```

Python 3.12+. The runtime deps (customtkinter, tkinterdnd2, httpx, pydantic,
keyring, platformdirs) are all standard PyPI; no system packages required
beyond Python itself. Every one is permissively licensed (MIT/BSD/Apache), so
the published `.exe` can stay MIT - the date picker is built in-tree on stdlib
tkinter rather than pulling GPL-3.0 tkcalendar.

## Build a Windows .exe locally

```bash
pip install -e .[build]
pyinstaller pyinstaller.spec    # produces dist/fileheron-client.exe
```

PyInstaller's `--onefile` mode extracts the bundled assets to a temp
dir at runtime (~3 s cold start on Windows).

To ship a **signed** build to your own users, Authenticode-sign the output with
your code-signing certificate (this removes the SmartScreen warning as your
signature builds reputation):

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a `
  dist\fileheron-client.exe
```

## Release

Push a tag `client-vX.Y.Z` to trigger
`.github/workflows/client-release.yml`. The workflow builds the
Windows .exe and publishes it to the matching GitHub release.

```bash
# bump src/fileheron_client/__init__.py::__version__ first
git tag client-v0.1.0
git push origin client-v0.1.0
```

## Regenerate the icon

Source artwork is `assets/heron.svg` (copied from
`frontend/public/heron.svg`). To regenerate the `.ico` and `.png`
after the SVG changes:

```bash
pip install pillow cairosvg     # one-off, not in [build]
python scripts/make_icon.py
```

## Layout

```
client/
├── pyproject.toml
├── pyinstaller.spec
├── assets/                # heron.svg + generated icon.ico, icon.png
├── src/fileheron_client/
│   ├── __main__.py        # entry point
│   ├── config.py          # platformdirs + keyring
│   ├── models.py          # subset Pydantic mirrors of backend schemas
│   ├── api/               # httpx wrappers (auth, shares, files, uploads)
│   ├── tus.py             # raw TUS 1.0.0 client (chunked PATCH + resume)
│   └── ui/                # CustomTkinter windows + workers
└── tests/                 # pytest, no GUI, no real network
```

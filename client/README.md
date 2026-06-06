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
stored at `%APPDATA%\fileHeron\config.json`; the refresh token is
held in Windows Credential Manager.

## Develop

```bash
cd client
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .[dev]
pytest                        # ~15 unit tests (no GUI, no network)
python -m fileheron_client    # launches the GUI against the server URL the config dialog asks for
```

Python 3.12+. The runtime deps (customtkinter, tkinterdnd2, tkcalendar,
httpx, pydantic, keyring, platformdirs) are all standard PyPI; no system
packages required
beyond Python itself.

## Build a Windows .exe locally

```bash
pip install -e .[build]
pyinstaller pyinstaller.spec    # produces dist/fileheron-client.exe
```

PyInstaller's `--onefile` mode extracts the bundled assets to a temp
dir at runtime (~3 s cold start on Windows).

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

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the fileHeron desktop client (v0.4.0+).

Onefile, no-console (no terminal pop-up on Windows). Bundles every
asset under ``assets/``. Run from the ``client/`` directory:

    pyinstaller pyinstaller.spec

GUI stack is CustomTkinter + tkinterdnd2 (drag-drop) + tkcalendar
(date picker). v0.4.10 mis-ejected tkinterdnd2 blaming it for a
crash that was actually a self.{_root} attribute shadowing in widget
subclasses (fixed in v0.4.11); v0.5.0 brings it back. PyInstaller's
stock hooks find tkinter + tkinterdnd2's Tcl extension on their own;
the explicit hidden-imports below cover packages whose static
analysis sometimes misses dynamic submodule loads.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# PyInstaller exec's the spec file (no __file__); SPECPATH is the
# absolute directory of this spec, injected by the build runner.
HERE = Path(SPECPATH).resolve()  # noqa: F821

# Explicitly COLLECT the data files / native libs these packages need at
# runtime (finding C11). Previously the spec only listed them as
# hiddenimports and trusted PyInstaller's contrib hooks to collect their
# data. That works with the currently-pinned versions, but a hook/version
# regression would silently ship a .exe that crashes on launch (missing
# customtkinter theme JSON) or on drag-drop (missing tkinterdnd2 `tkdnd`
# Tcl lib). collect_all returns (datas, binaries, hiddenimports) and is
# self-healing across versions; the CI self-check (see client-release.yml)
# proves the bundle actually launches.
_ctk_datas, _ctk_bins, _ctk_hidden = collect_all("customtkinter")
_dnd_datas, _dnd_bins, _dnd_hidden = collect_all("tkinterdnd2")

datas = [
    (str(HERE / "assets"), "assets"),
    # v0.8.0: i18n locale JSON files. Loaded at runtime via a
    # __file__-relative path that resolves under sys._MEIPASS in the bundle.
    (
        str(HERE / "src" / "fileheron_client" / "locales"),
        "fileheron_client/locales",
    ),
]
datas += _ctk_datas + _dnd_datas
datas += collect_data_files("tkcalendar")

# tkcalendar pulls Babel for its date rendering, and Babel ships the FULL
# CLDR locale database — ~30 MB across 1000+ `locale-data/*.dat` files, the
# single biggest chunk of the .exe. The app only ever renders the date picker
# in en/de (DateEntry is pinned to the app locale in the UI code), so keep
# only `root` (Babel's ultimate fallback) + `en*` + `de*` and drop the rest.
# Everything outside `locale-data/` (e.g. global.dat) is kept untouched.
_BABEL_KEEP_LANGS = {"root", "en", "de"}
for _src, _dest in collect_data_files("babel"):
    _p = Path(_src)
    if "locale-data" in _p.parts and _p.suffix == ".dat":
        if _p.stem.split("_", 1)[0] not in _BABEL_KEEP_LANGS:
            continue  # drop this locale's CLDR data
    datas.append((_src, _dest))

binaries = _ctk_bins + _dnd_bins

hiddenimports = [
    "customtkinter",
    "tkinterdnd2",
    "tkcalendar",
    # Submodules pulled in by string in the API package.
    "fileheron_client.api.client",
    "fileheron_client.api.auth",
    "fileheron_client.api.shares",
    "fileheron_client.api.files",
    "fileheron_client.api.uploads",
    "fileheron_client.api.users",
    "fileheron_client.api.groups",
    # keyring backends are dynamically loaded. The desktop client
    # ships Windows-only via .github/workflows/client-release.yml; the
    # Linux (SecretService) and macOS backends are dead weight in the
    # .exe (~200 KB saved by v0.6.2 trim).
    "keyring.backends.Windows",
]
hiddenimports += _ctk_hidden + _dnd_hidden

a = Analysis(
    [str(HERE / "src" / "fileheron_client" / "__main__.py")],
    pathex=[str(HERE / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    # v0.4.7: anchor the Windows DLL search path to the bundle dir so
    # the bundled OpenSSL wins over any system-wide libssl-3.dll /
    # libcrypto-3.dll that Git for Windows / Conda / Node.js / OpenVPN
    # etc. may have on PATH. Without this hook, _ssl.pyd's loader
    # picks up the wrong OpenSSL build and crashes with ACCESS_VIOLATION.
    runtime_hooks=[str(HERE / "runtime_hooks" / "dll_search_path.py")],
    excludes=[
        # Belt-and-braces — if PySide6 sneaks in via a transitive
        # dep (it shouldn't, but it has happened in the wild), keep
        # it out of the bundle. Comment out if PyInstaller complains.
        "PySide6",
        "PyQt6",
        "PyQt5",
        # Pillow (~17 MB) is pulled in only by customtkinter's CTkImage,
        # which this app never uses (the window icon goes through
        # tkinter.PhotoImage, not PIL). CTkImage's `from PIL import ...`
        # is wrapped in try/except ImportError, so excluding PIL is safe —
        # `import customtkinter` still works; only CTkImage would be
        # unavailable. Saves a large chunk of the bundle.
        "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

ICON = HERE / "assets" / "icon.ico"
icon_arg = str(ICON) if ICON.is_file() else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="fileheron-client",
    debug=False,
    bootloader_ignore_signals=False,
    # v0.4.8: strip DISABLED. PyInstaller invokes MSYS/GNU `strip` on
    # every native binary in the bundle (visible in the build log as
    # `Executing: strip ...`). On Windows PE files — particularly
    # Python C extensions like _ssl.pyd + their OpenSSL dependencies
    # libssl-3.dll + libcrypto-3.dll — this can corrupt the section
    # layout, leaving DLLs that LOAD successfully but ACCESS_VIOLATE
    # the moment a function inside them is called. That's exactly the
    # "DLL load failed while importing _ssl: ACCESS_VIOLATION" crash
    # users hit in v0.4.0–v0.4.7 (we only saw it post-v0.4.4 because
    # earlier versions had different sign-in bugs blocking the
    # network path before _ssl was needed).
    strip=False,
    # v0.4.5: UPX disabled. We learned the hard way that:
    #   1. In PyInstaller --onefile mode the outer bootloader ZIP layer
    #      largely un-does UPX's gains — the v0.4.2 build with UPX on
    #      every DLL came out the same 31 MB as v0.4.0 without UPX.
    #   2. UPX broke _ssl.pyd in v0.4.2/0.4.3/0.4.4 — SSL imports failed
    #      with "DLL load failed... Invalid access to a memory region"
    #      (Windows ACCESS_VIOLATION). Likely also affects other native
    #      crypto / openssl DLLs that depend on aligned-load semantics.
    # No size cost from disabling — the v0.4.5 .exe is the same ~31 MB
    # as the broken v0.4.2-0.4.4 builds, just functional.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)

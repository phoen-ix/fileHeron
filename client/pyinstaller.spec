# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the fileHeron desktop client (v0.4.0+).

Onefile, no-console (no terminal pop-up on Windows). Bundles every
asset under ``assets/``. Run from the ``client/`` directory:

    pyinstaller pyinstaller.spec

GUI stack is CustomTkinter (+ tkinterdnd2 for drag-drop, + tkcalendar
for the date picker). PyInstaller's stock hooks find tkinter on their
own; the explicit hidden-imports below cover the three packages whose
static analysis sometimes misses dynamic submodule loads.
"""
from pathlib import Path

block_cipher = None

# PyInstaller exec's the spec file (no __file__); SPECPATH is the
# absolute directory of this spec, injected by the build runner.
HERE = Path(SPECPATH).resolve()  # noqa: F821

datas = [
    (str(HERE / "assets"), "assets"),
]

hiddenimports = [
    # GUI deps. tkinterdnd2 ships its own .tcl + native binary that
    # PyInstaller's stock hook collects; the explicit import below
    # nudges the static analyser to follow.
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
    # keyring backends are dynamically loaded.
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.macOS",
]

a = Analysis(
    [str(HERE / "src" / "fileheron_client" / "__main__.py")],
    pathex=[str(HERE / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Belt-and-braces — if PySide6 sneaks in via a transitive
        # dep (it shouldn't, but it has happened in the wild), keep
        # it out of the bundle. Comment out if PyInstaller complains.
        "PySide6",
        "PyQt6",
        "PyQt5",
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
    # strip + upx: enabled in v0.4.0 alongside the CTk swap so the
    # 60 MB → ~18 MB drop lands in one release. Builds need `upx` on
    # PATH (Windows CI: choco install upx).
    strip=True,
    upx=True,
    upx_exclude=[
        # Tcl/Tk DLLs sometimes break under UPX; exclude defensively.
        # Empty list means "compress everything"; the patterns below
        # match the substrings PyInstaller logs.
        "tcl*.dll",
        "tk*.dll",
        "vcruntime140.dll",
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)

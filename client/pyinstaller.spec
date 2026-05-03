# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the fileHeron desktop client.

Onefile, no-console (no terminal pop-up on Windows). Bundles every
asset under ``assets/``. Run from the ``client/`` directory:

    pyinstaller pyinstaller.spec
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
    # PySide6 modules PyInstaller's static analyser sometimes misses.
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    # Submodules pulled in by string in the API package.
    "fileheron_client.api.client",
    "fileheron_client.api.auth",
    "fileheron_client.api.shares",
    "fileheron_client.api.files",
    "fileheron_client.api.uploads",
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
        # Slim the .exe — these PySide6 modules aren't used.
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtMultimedia",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
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
    strip=False,
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

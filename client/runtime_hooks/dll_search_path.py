"""PyInstaller runtime hook: anchor the Windows DLL search path to
the bundle directory so the OpenSSL DLLs we ship win over any system-
wide ones the user happens to have on PATH.

Symptom this fixes: on a fresh Windows machine with (e.g.) Git for
Windows, Conda, Node.js, OpenVPN, NordVPN, or any other product that
ships its own ``libssl-3.dll`` / ``libcrypto-3.dll`` somewhere on
PATH, ``import _ssl`` would fail with::

    ImportError: DLL load failed while importing _ssl:
        Unzulässiger Zugriff auf einen Speicherbereich. (ACCESS_VIOLATION)

The bundled `_ssl.pyd` is loaded from the PyInstaller extract dir,
but its dependent DLLs are resolved via Windows' default search order
— which can pick the WRONG OpenSSL build from a PATH dir. Its symbol
offsets don't match what `_ssl.pyd` expects, so its initialiser
crashes the loader.

``os.add_dll_directory(sys._MEIPASS)`` inserts the bundle dir into
the DLL search path with HIGH priority, so the bundled OpenSSL
unambiguously wins. Python 3.8+ requires this explicit opt-in for
non-system DLL dirs (PEP 587 security tightening).
"""
from __future__ import annotations

import os
import sys


if sys.platform == "win32":
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        try:
            os.add_dll_directory(bundle)
        except (FileNotFoundError, OSError):
            # If the bundle dir isn't a valid directory (shouldn't
            # happen post-extract) just continue — better to crash
            # later with the legible "DLL load failed" message than
            # to crash here with no diagnostic.
            pass

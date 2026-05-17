"""Root window + theme bootstrap (v0.4.0 CustomTkinter migration).

The desktop client is built around a single ``ctk.CTk`` root.

**v0.4.10: tkinterdnd2 was ripped out.** Both attempts to combine it
with CTk crashed post-login with the same pattern:
- v0.4.8 — mutated ``ctk.CTk.__bases__`` to add ``TkinterDnD.Tk``;
  raised ``TypeError: 'CTk' object is not callable`` from
  ``tkinter._substitute`` on the first event dispatch.
- v0.4.9 — clean subclass mixin ``class CTkDnD(ctk.CTk,
  TkinterDnD.DnDWrapper)``; same crash, just with ``'CTkDnD'``
  in the error.

The common factor was the DnDWrapper mixin itself — its presence in
the MRO shadows / interacts badly with one of CTk's internals in a
way that takes out the Tk event dispatcher. Rather than chase yet
another permutation, drag-drop is dropped entirely; the "Add files…"
button in the upload panel covers the same ground. We can revisit a
different dnd library (or hand-rolled tkdnd Tcl bindings) in a
separate change."""
from __future__ import annotations

import sys

import customtkinter as ctk

from ..assets_loader import asset_path


def _safe_windows_set_titlebar_color(self, color_mode: str) -> None:
    """v0.4.17 replacement for CTk's ``_windows_set_titlebar_color``.

    The original (``customtkinter/windows/ctk_tk.py`` and
    ``ctk_toplevel.py``) wraps the DWM immersive-dark-mode call in
    ``withdraw()`` → DWM → ``deiconify()`` to work around a Windows
    repaint bug. That dance races with the rest of the event loop —
    v0.4.13 traced "invisible window after sign-in" to this routine's
    deiconify being lost. v0.4.15 worked around it by aggressively
    re-deiconifying the root, but that pre-empts the DWM call too,
    leaving the title bar (and the File menu bar that inherits from
    it) light when the OS is dark.

    This replacement skips the withdraw/deiconify entirely and only
    issues the DWM attribute write. Tradeoff: the title bar may not
    repaint instantly on a runtime theme toggle. On Windows 11 the
    DWM compositor picks the change up at next frame, so initial
    application is fine. Worst case (Settings → Dark) the user may
    need to drag/resize the window once to see the new tint —
    acceptable for the win of the bug going away."""
    if not sys.platform.startswith("win"):
        return
    if getattr(self, "_deactivate_windows_window_header_manipulation", False):
        return
    mode = (color_mode or "").lower()
    if mode == "dark":
        value = 1
    elif mode == "light":
        value = 0
    else:
        return
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
        v = ctypes.c_int(value)
        rc = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(v), ctypes.sizeof(v),
        )
        if rc != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1,
                ctypes.byref(v), ctypes.sizeof(v),
            )
    except Exception:
        pass


# Apply at module import time, BEFORE any CTk window is constructed.
# Both CTk (root) and CTkToplevel (login + future toplevels) share
# the same routine name; patch both.
ctk.CTk._windows_set_titlebar_color = _safe_windows_set_titlebar_color
ctk.CTkToplevel._windows_set_titlebar_color = _safe_windows_set_titlebar_color


def build_root() -> ctk.CTk:
    """Return the single application root."""
    ctk.set_appearance_mode("system")  # follows Windows light/dark
    ctk.set_default_color_theme("blue")  # CTk stock; tweak in widgets

    root = ctk.CTk()
    root.title("file:Heron")
    root.geometry("1000x640")

    # Window icon — fall back to PNG if .ico missing in dev. iconbitmap
    # only works on Windows + uses .ico; iconphoto is cross-platform
    # but takes a PhotoImage. Try both so dev on Linux + prod on
    # Windows both look right.
    ico = asset_path("icon.ico")
    if ico.is_file():
        try:
            root.iconbitmap(default=str(ico))
        except Exception:
            pass
    png = asset_path("icon.png")
    if png.is_file():
        try:
            from tkinter import PhotoImage

            root._fh_icon = PhotoImage(file=str(png))  # keep ref alive
            root.iconphoto(True, root._fh_icon)
        except Exception:
            pass

    return root


def set_appearance_mode(mode: str) -> None:
    """Wrapper around ``ctk.set_appearance_mode`` so the settings
    dialog can swap themes at runtime without importing customtkinter
    directly. ``mode`` ∈ {"light", "dark", "system"}."""
    if mode in ("light", "dark", "system"):
        ctk.set_appearance_mode(mode)

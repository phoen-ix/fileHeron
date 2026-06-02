"""Root window + theme bootstrap.

The desktop client is built around a single ``ctk.CTk`` root.

**v0.5.0 brought tkinterdnd2 back.** v0.4.10 had ripped it out
blaming a ``TypeError: 'CTk' object is not callable`` crash on the
mixin's interaction with CTk's MRO. The real culprit turned out to
be our own widget subclasses doing ``self._root = root``, which
shadowed ``tkinter.Misc._root`` (an inherited method tkinter calls
during event substitution). v0.4.11 fixed that by renaming the
attribute to ``self._app_root`` everywhere, so the
``class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper)`` mixin pattern
works cleanly now."""
from __future__ import annotations

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from ..assets_loader import asset_path
from .context_menu import install_context_menus


class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    """ctk.CTk + tkinterdnd2 drag-drop. ``DnDWrapper`` is a pure
    mixin (no Tk state of its own) that supplies
    ``drop_target_register`` / ``dnd_bind``; the explicit
    ``_require()`` call loads the Tcl ``tkdnd`` package against
    this root's interpreter so the dnd methods actually work."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


def build_root() -> ctk.CTk:
    """Return the single application root."""
    ctk.set_appearance_mode("system")  # follows Windows light/dark
    ctk.set_default_color_theme("blue")  # CTk stock; tweak in widgets

    root = CTkDnD()
    root.title("file:Heron")
    # Standard right-click Cut/Copy/Paste/Select-all in every text field
    # (Tkinter has no native one). Interpreter-global, so one call covers
    # widgets built later too.
    install_context_menus(root)
    # The New share form pins its submit/Add files row to the bottom
    # (side="bottom") so 1000x640 fits everything; the file list area
    # absorbs slack space. center_window opens it mid-screen rather than
    # in the OS default top-left corner.
    center_window(root, 1000, 640)

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


def center_window(win, w: int, h: int) -> None:
    """Set ``win`` to ``w``×``h`` centered on the primary monitor.

    Multi-monitor caveat: ``winfo_screenwidth/height`` report the primary
    screen only, so on a multi-head setup the window centers on the primary
    display even if launched elsewhere — acceptable for this app."""
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")


def reassert_visible(win, remaining_ticks: int = 60) -> None:
    """Safety net for CTk's ``_windows_set_titlebar_color`` routine, which
    runs withdraw() → DWM call → deiconify() on Windows and can lose the
    deiconify, leaving the window stuck withdrawn. Poll ``state()`` every
    50ms for ~3s and force it back to ``normal`` if anything withdrew us.

    Hoisted out of MainWindow (was ``_reassert_visible``) so both the first
    overlay show (AppController) and post-sign-in (MainWindow) can reuse it
    against the single root."""
    try:
        if win.state() != "normal":
            win.deiconify()
            win.lift()
    except Exception:
        return
    if remaining_ticks > 0:
        win.after(50, lambda: reassert_visible(win, remaining_ticks - 1))


def set_appearance_mode(mode: str) -> None:
    """Wrapper around ``ctk.set_appearance_mode`` so the settings
    dialog can swap themes at runtime without importing customtkinter
    directly. ``mode`` ∈ {"light", "dark", "system"}."""
    if mode in ("light", "dark", "system"):
        ctk.set_appearance_mode(mode)

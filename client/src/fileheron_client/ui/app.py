"""Root window + theme bootstrap (v0.4.0 CustomTkinter migration).

The desktop client is built around a single ``TkinterDnD.Tk`` root —
tkinterdnd2 requires its own Tk subclass to wire drag-and-drop into
the X11 / Win32 event loop. We instantiate it once here, hide it
during the login phase (login is its own ``CTkToplevel``), then
show + populate it as the main window after a successful sign-in."""
from __future__ import annotations

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from ..assets_loader import asset_path


def build_root() -> ctk.CTk:
    """Return the single application root.

    Multiple-inheritance trick: CustomTkinter exposes its theming
    through ``ctk.CTk`` (a subclass of ``tk.Tk``). tkinterdnd2 exposes
    drag-drop through ``TkinterDnD.Tk`` (another subclass of ``tk.Tk``).
    The widely-used pattern in the customtkinter community is to mix
    the dnd2 base into CTk.CTk so the result is one class that's a Tk
    root, exposes ctk theming, AND accepts dnd2 calls. The MRO is
    well-defined because the two bases don't overlap in __init__
    signatures."""
    if TkinterDnD.Tk not in ctk.CTk.__bases__:
        ctk.CTk.__bases__ = (TkinterDnD.Tk,) + ctk.CTk.__bases__

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

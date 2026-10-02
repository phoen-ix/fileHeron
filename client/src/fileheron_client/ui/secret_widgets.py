"""Building blocks shared by the secret views (server v2.24.0).

``RevealCard`` is the only widget that ever holds a secret's text. It shows a
FIXED number of dots until Show is pressed - the mask never follows the
text's length, which would tell an onlooker how long the password is - and
Copy works while it is masked. ``clear()`` drops the text; the card calls it
itself when it is destroyed, so leaving a view never leaves the text behind in
a widget nobody can see.
"""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from ..i18n import t
from .widgets import copy_to_clipboard_with_feedback

# The mask is a constant, never derived from the text (see module docstring).
MASK = "•" * 12

MUTED = ("gray40", "gray65")
ERROR = ("#991b1b", "#fca5a5")
OK = ("#166534", "#bbf7d0")


def heading(master, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master, text=text, anchor="w", font=ctk.CTkFont(size=13, weight="bold"),
    )


def help_label(master, text: str = "", *, wrap: int = 640, **kwargs) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master, text=text, anchor="w", justify="left", wraplength=wrap,
        text_color=kwargs.pop("text_color", MUTED), **kwargs,
    )


def back_button(master, text: str, command: Callable[[], None]) -> ctk.CTkButton:
    return ctk.CTkButton(
        master, text=text, width=140, height=28, fg_color="transparent",
        border_width=1, hover_color=("gray85", "gray25"), command=command,
        text_color=("gray10", "gray90"),
    )


def danger_button(master, text: str, command: Callable[[], None], **kwargs) -> ctk.CTkButton:
    return ctk.CTkButton(
        master, text=text, command=command,
        fg_color="#991b1b", hover_color="#7f1d1d", **kwargs,
    )


class PassphrasePair(ctk.CTkFrame):
    """Passphrase + repeat, both masked. The values are read, never shown."""

    def __init__(self, master, *, label: str, on_change: Optional[Callable[[], None]] = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.passphrase_var = ctk.StringVar()
        self.repeat_var = ctk.StringVar()
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        row.grid_columnconfigure(0, weight=1, uniform="pp")
        row.grid_columnconfigure(1, weight=1, uniform="pp")
        ctk.CTkLabel(row, text=label, anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkLabel(row, text=t("secrets.passphrase_repeat"), anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(6, 0),
        )
        ctk.CTkEntry(row, textvariable=self.passphrase_var, show="•").grid(
            row=1, column=0, sticky="ew", padx=(0, 6),
        )
        ctk.CTkEntry(row, textvariable=self.repeat_var, show="•").grid(
            row=1, column=1, sticky="ew", padx=(6, 0),
        )
        if on_change is not None:
            self.passphrase_var.trace_add("write", lambda *_a: on_change())
            self.repeat_var.trace_add("write", lambda *_a: on_change())

    def values(self) -> tuple[str, str]:
        return self.passphrase_var.get(), self.repeat_var.get()

    def wipe(self) -> None:
        self.passphrase_var.set("")
        self.repeat_var.set("")


class LinkRow(ctk.CTkFrame):
    """A read-only link with its own Copy button and "✓ Copied" feedback."""

    def __init__(self, master, url: str, *, caption: Optional[str] = None,
                 on_copy_failed: Optional[Callable[[], None]] = None) -> None:
        super().__init__(master, fg_color="transparent")
        if caption:
            ctk.CTkLabel(self, text=caption, anchor="w").pack(fill="x")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        self.url_var = ctk.StringVar(value=url)
        ctk.CTkEntry(row, textvariable=self.url_var, state="readonly").pack(
            side="left", fill="x", expand=True,
        )
        self._feedback = ctk.StringVar(value="")
        ctk.CTkButton(
            row, text=t("secrets.copy_link"), width=110,
            command=lambda: copy_to_clipboard_with_feedback(
                self, self.url_var.get(), feedback_var=self._feedback, on_fail=on_copy_failed,
            ),
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(row, textvariable=self._feedback, width=90, text_color=OK).pack(
            side="left", padx=(6, 0),
        )


class BlockerBox(ctk.CTkLabel):
    """The visible reasons Send is disabled (the ShareCreate rule)."""

    def __init__(self, master, *, wrap: int = 620) -> None:
        super().__init__(
            master, text="", anchor="w", justify="left", wraplength=wrap,
            text_color=("#92400e", "#fcd34d"),
        )

    def show_blockers(self, blockers: list[tuple[str, dict]]) -> None:
        if not blockers:
            self.configure(text="")
            return
        lines = [t("secrets.blockers.title")]
        lines += [f"• {t(key, **kwargs)}" for key, kwargs in blockers]
        self.configure(text="\n".join(lines))


class RevealCard(ctk.CTkFrame):
    """The revealed text: masked until Show, Copy at any time."""

    def __init__(self, master, *, on_copy_failed: Optional[Callable[[], None]] = None) -> None:
        super().__init__(master, border_width=1, corner_radius=6)
        self._secret_text: Optional[str] = None
        self._shown = False
        self._on_copy_failed = on_copy_failed

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=10, pady=10)
        self._display = ctk.CTkTextbox(inner, height=90, wrap="word")
        self._display.pack(fill="x")
        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.pack(fill="x", pady=(8, 0))
        self._toggle_btn = ctk.CTkButton(btns, text=t("secrets.reveal.show"), width=100,
                                         command=self.toggle)
        self._toggle_btn.pack(side="left")
        ctk.CTkButton(btns, text=t("secrets.reveal.copy"), width=100, command=self.copy).pack(
            side="left", padx=(8, 0),
        )
        self._feedback = ctk.StringVar(value="")
        ctk.CTkLabel(btns, textvariable=self._feedback, text_color=OK).pack(side="left", padx=(8, 0))
        self.note_var = ctk.StringVar(value="")
        help_label(inner, textvariable=self.note_var).pack(fill="x", pady=(6, 0))
        self._paint()

    def set_text(self, text: str, note: str = "") -> None:
        self._secret_text = text
        self._shown = False
        self.note_var.set(note)
        self._paint()

    def has_text(self) -> bool:
        return self._secret_text is not None

    def toggle(self) -> None:
        self._shown = not self._shown
        self._paint()

    def copy(self) -> None:
        if self._secret_text:
            copy_to_clipboard_with_feedback(
                self, self._secret_text, feedback_var=self._feedback,
                on_fail=self._on_copy_failed,
            )

    def clear(self) -> None:
        """Drop the text and empty the box."""
        self._secret_text = None
        self._shown = False
        try:
            self._paint()
        except Exception:
            pass

    def _paint(self) -> None:
        shown = self._shown and self._secret_text is not None
        self._display.configure(state="normal")
        self._display.delete("1.0", "end")
        self._display.insert("1.0", self._secret_text if shown else MASK)
        self._display.configure(state="disabled")
        self._toggle_btn.configure(text=t("secrets.reveal.hide") if shown else t("secrets.reveal.show"))

    def destroy(self) -> None:
        self.clear()
        super().destroy()

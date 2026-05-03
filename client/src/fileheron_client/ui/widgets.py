"""Small shared widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


# State pill colours, mirroring the SPA's design tokens.
_PILL_COLOURS: dict[str, tuple[str, str]] = {
    # state -> (background, text)
    "active": ("#dcfce7", "#166534"),
    "expired": ("#fef3c7", "#92400e"),
    "revoked": ("#fee2e2", "#991b1b"),
    "deleted": ("#e5e7eb", "#374151"),
    "clean": ("#dcfce7", "#166534"),
    "ready_unscanned": ("#fef3c7", "#92400e"),
    "infected": ("#fee2e2", "#991b1b"),
}


class PillLabel(QLabel):
    """Compact rounded chip for share / file states."""

    def __init__(self, text: str = "", state: str | None = None) -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(20)
        self.setContentsMargins(8, 2, 8, 2)
        self.setState(state or text)

    def setState(self, state: str | None) -> None:
        bg, fg = _PILL_COLOURS.get(state or "", ("#e5e7eb", "#374151"))
        self.setStyleSheet(
            f"QLabel {{ background:{bg}; color:{fg}; border-radius:10px; "
            f"padding:1px 8px; font-size:11px; font-weight:600; }}"
        )


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    k = n / 1024
    if k < 1024:
        return f"{k:.1f} KB"
    m = k / 1024
    if m < 1024:
        return f"{m:.1f} MB"
    return f"{m / 1024:.2f} GB"

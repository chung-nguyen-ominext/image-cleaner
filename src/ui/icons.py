"""Icon library access (QtAwesome + Font Awesome, bundled offline).

The app no longer hand-draws glyphs: every small icon comes from a real icon
font so the shapes stay consistent across platforms. QtAwesome ships the font
files inside its wheel, so rendering works without network access.
"""

from __future__ import annotations

from functools import lru_cache

import qtawesome as qta
from PySide6.QtGui import QIcon, QPixmap

from src.ui.theme import MUTED

# Semantic names: call sites do not depend on the Font Awesome naming scheme.
NAMES: dict[str, str] = {
    "info": "fa5s.info-circle",
    "chevron_down": "fa5s.chevron-down",
    "chevron_up": "fa5s.chevron-up",
    "images": "fa5s.images",
    "folder": "fa5s.folder-open",
    "analyze": "fa5s.search",
    "save": "fa5s.save",
    "cancel": "fa5s.times",
    "settings": "fa5s.cog",
    "log": "fa5s.terminal",
    "original": "fa5s.image",
    "result": "fa5s.magic",
    "remove": "fa5s.trash-alt",
    "fit": "fa5s.expand",
    "plus": "fa5s.plus",
    "minus": "fa5s.minus",
    "grip_vertical": "fa5s.ellipsis-v",
    "grip_horizontal": "fa5s.ellipsis-h",
}


def icon(name: str, color: str = MUTED) -> QIcon:
    """A themed icon by semantic name."""
    return qta.icon(NAMES[name], color=color, color_disabled="#5d6270")


@lru_cache(maxsize=128)
def pixmap(name: str, size: int = 16, color: str = MUTED) -> QPixmap:
    """Rendered icon pixmap (cached: paintEvent must stay cheap)."""
    return icon(name, color).pixmap(size, size)

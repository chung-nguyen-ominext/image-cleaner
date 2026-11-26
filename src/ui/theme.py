"""Design tokens and stylesheet loading for the desktop app.

Widget styling lives in ``styles/dark.qss``.  The small JSON token file is the
single source for the handful of colours that must be used from Python (icon
pixmaps and the preview's custom painting).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


def _resource_root() -> Path:
    """Repository root in dev, PyInstaller extraction dir when frozen."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parents[2]


ROOT = _resource_root()
STYLE_DIR = ROOT / "styles"
TOKENS: dict[str, str] = json.loads(
    (STYLE_DIR / "tokens.json").read_text(encoding="utf-8")
)

BG = TOKENS["bg"]
PANEL = TOKENS["panel"]
PANEL_ALT = TOKENS["panel_alt"]
PANEL_RAISED = TOKENS["panel_raised"]
PREVIEW = TOKENS["preview"]
BORDER = TOKENS["border"]
BORDER_STRONG = TOKENS["border_strong"]
TEXT = TOKENS["text"]
MUTED = TOKENS["muted"]
MUTED_MIN = TOKENS["muted_min"]
PLACEHOLDER = TOKENS["placeholder"]
ACCENT = TOKENS["primary"]
ACCENT_HOVER = TOKENS["primary_hover"]
ON_PRIMARY = TOKENS["on_primary"]
GREEN = TOKENS["success"]
ON_SUCCESS = TOKENS["on_success"]
ORANGE = TOKENS["warning"]
WARNING = TOKENS["warning"]
WARNING_BG = TOKENS["warning_bg"]
REGION = TOKENS["region"]
RED = TOKENS["danger"]
DANGER_BG = TOKENS["danger_bg"]
DISABLED = TOKENS["disabled"]

STYLESHEET = (STYLE_DIR / "dark.qss").read_text(encoding="utf-8").replace(
    "__STYLES__", STYLE_DIR.as_posix()
)


def repolish(widget: QWidget) -> None:
    """Re-evaluate QSS after changing a dynamic property."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def dim_placeholder(widget) -> None:
    """Use the muted placeholder colour on one widget."""
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PLACEHOLDER))
    widget.setPalette(palette)


def apply_theme(app: QApplication) -> None:
    """Apply the dark palette and the external stylesheet."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(PANEL_ALT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL_ALT))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PLACEHOLDER))
    palette.setColor(QPalette.ColorRole.Button, QColor(PANEL_ALT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(RED))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(ON_PRIMARY))
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(DISABLED))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

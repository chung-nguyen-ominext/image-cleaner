"""Dark theme for the desktop app (no external assets, fully offline)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#17181c"
PANEL = "#1f2127"
PANEL_ALT = "#262932"
BORDER = "#333743"
TEXT = "#e7e9ee"
MUTED = "#9aa1b1"
# Placeholders must be clearly dimmer than typed text so an empty field is
# never mistaken for real content.
PLACEHOLDER = "#6f7789"
ACCENT = "#4f8cff"
ACCENT_DARK = "#3c6fd1"
GREEN = "#3ecf8e"
ORANGE = "#f0a03c"
RED = "#f0616d"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* Labels sit on coloured panels; a transparent background stops the global
   QWidget fill from drawing a tight dark box around the text, and the small
   horizontal padding keeps glyphs off the panel edge. */
QLabel {{
    background: transparent;
    padding: 1px 4px;
}}
QLabel#hint_badge {{
    background: transparent;
    padding: 0;
}}

QGroupBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding: 8px 10px 8px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {MUTED};
}}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 7px;
    selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid {BORDER};
    background: transparent;
}}
QSpinBox, QDoubleSpinBox {{ padding-right: 28px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 22px;
    background: transparent;
    border-left: 1px solid {BORDER};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: 6px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 6px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DARK};
    outline: none;
}}

QPushButton {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 32px;
}}
QPushButton:hover {{ background: #2e323d; border-color: #454b5a; }}
QPushButton:pressed {{ background: #21242c; }}
QPushButton:disabled {{ color: #5d6270; background: #202229; border-color: #2a2d36; }}
QPushButton#primary {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #0d1220;
    font-weight: 700;
}}
QPushButton#primary:hover {{ background: #6a9cff; }}
QPushButton#primary:disabled {{ background: #2c3a55; border-color: #2c3a55; color: #7d879b; }}
QPushButton#danger {{ color: {RED}; }}
QPushButton#danger:hover {{ border-color: {RED}; }}

/* Segmented Original / Result switch */
QPushButton#segment_left, QPushButton#segment_right {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 0;
    padding: 5px 14px;
    min-height: 30px;
}}
QPushButton#segment_left {{
    border-top-left-radius: 6px;
    border-bottom-left-radius: 6px;
    border-right: none;
}}
QPushButton#segment_right {{
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}
QPushButton#segment_left:hover, QPushButton#segment_right:hover {{
    background: #2e323d;
}}
QPushButton#segment_left:checked, QPushButton#segment_right:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #0d1220;
    font-weight: 700;
}}

QPushButton#zoom_btn {{
    padding: 4px 10px;
}}
QPushButton#zoom_square {{
    padding: 2px;
    font-size: 16px;
    font-weight: 700;
}}
QLabel#zoom_label {{
    color: {MUTED};
    font-weight: 600;
}}

QLabel#uncertain_badge {{
    background: #4a3a14;
    color: #f5c542;
    border: 1px solid #80621c;
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 700;
}}

QListWidget, QTableWidget, QTreeWidget {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    alternate-background-color: #232630;
    outline: none;
}}
QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {ACCENT_DARK}; color: white; }}
QTableWidget {{ gridline-color: #2c303a; }}
QTableWidget::item:selected {{ background: {ACCENT_DARK}; color: white; }}
QHeaderView::section {{
    background: {PANEL_ALT};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 6px;
    font-weight: 600;
}}

QProgressBar {{
    background: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    height: 14px;
    text-align: center;
    color: {TEXT};
    font-size: 11px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a3f4c; border-radius: 5px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3a3f4c; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCheckBox {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: {PANEL_ALT};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{
    background: {PANEL_ALT};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 6px;
}}
QStatusBar {{
    background: {PANEL};
    border-top: 1px solid {BORDER};
    color: {MUTED};
    padding-left: 8px;
}}
"""


def dim_placeholder(widget) -> None:
    """Use the muted placeholder colour on one widget (works without app theme)."""
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PLACEHOLDER))
    widget.setPalette(palette)


def apply_theme(app: QApplication) -> None:
    """Apply the dark stylesheet plus a placeholder colour that stays dim."""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PLACEHOLDER))
    app.setPalette(palette)

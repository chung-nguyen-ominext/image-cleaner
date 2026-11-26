"""Desktop entry point: `imagecleaner` or `python -m src.ui.app`."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap, QColor
from PySide6.QtWidgets import QApplication

from src.ui.theme import ACCENT, BG, apply_theme
from src.ui.main_window import MainWindow


def _make_icon() -> QIcon:
    """Simple generated icon: rounded frame with a cleared stripe (no asset files)."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(ACCENT))
    painter.drawRoundedRect(6, 6, 52, 52, 12, 12)
    painter.setBrush(QColor(BG))
    painter.drawRoundedRect(14, 16, 36, 8, 3, 3)
    painter.drawRoundedRect(14, 30, 24, 6, 3, 3)
    painter.drawRoundedRect(14, 41, 32, 6, 3, 3)
    painter.end()
    return QIcon(pix)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Image Cleaner")
    app.setApplicationDisplayName("Image Cleaner")
    app.setOrganizationName("ImageCleaner")
    apply_theme(app)
    app.setWindowIcon(_make_icon())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

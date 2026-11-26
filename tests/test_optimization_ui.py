"""Stage R UI regressions for Phase 8 (red before the UI fixes).

Covered:
- mask overlay must show the refined mask that will actually be removed;
- failed analysis must update its list item;
- Delete must remove a selected image;
- saving must use an inline summary instead of a modal dialog.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from pathlib import Path

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.mask_refinement import refine_element_mask
from src.core.models import AnalysisResult, Match, Region, RegionKind, TextLine, Word
from src.ui.main_window import MainWindow, SaveOutcome
from src.utils.image_utils import load_image

FIXTURES = Path("tests/fixtures")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qt_app, monkeypatch, tmp_path):
    monkeypatch.setenv("IC_CONFIG_DIR", str(tmp_path / "cfg"))
    win = MainWindow()
    win.resize(1366, 768)
    win.show()
    yield win
    win.close()


def _line(text: str, box: tuple[int, int, int, int]) -> TextLine:
    x, y, w, h = box
    return TextLine(
        text, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], 0.99, 0,
        [Word(text, (x, y, w, h), 0.99)],
    )


def _icon_case(window):
    path = FIXTURES / "vpn_button_icon_annotation.png"
    window._add_paths([path])
    entry = window._all_entries()[0]
    loaded = load_image(path)
    lines = [
        _line("VPN", (112, 181, 47, 37)),
        _line("G", (36, 160, 72, 80)),
    ]
    match = Match("VPN", [0], (112, 181, 47, 37), "VPN", 1.0, 0.99)
    region = Region(
        0, RegionKind.ELEMENT, (90, 182, 120, 36), 0.95,
        match=match, enabled=True,
    )
    entry.result = AnalysisResult(
        str(path), loaded.width, loaded.height, lines=lines, regions=[region],
    )
    window._show_entry(entry)
    return entry, region, loaded


def test_mask_overlay_uses_refined_mask(window):
    entry, region, loaded = _icon_case(window)
    captured: list[np.ndarray] = []
    window.preview.set_mask_overlay = lambda rgba: captured.append(rgba)

    window._rebuild_mask_overlay()

    assert captured, "overlay was not rebuilt"
    overlay = captured[-1][:, :, 3] > 0

    options = window._collect_options()
    expected = refine_element_mask(
        loaded.bgr, region, entry.result.lines, options
    ).mask > 0

    assert np.array_equal(overlay, expected), (
        f"overlay differs from the refined mask by {int((overlay != expected).sum())} px"
    )


def test_failed_analysis_updates_list_item(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window._add_paths([FIXTURES / "btn_filled.png"])
    entry = window._all_entries()[0]
    window._on_analyze_done([(str(entry.path.resolve()), None, "boom")])

    item = window.file_list.item(0)
    assert item is not None
    assert "error" in item.text().casefold(), item.text()
    assert "boom" in item.toolTip(), item.toolTip()


def test_delete_key_removes_selected_file(window):
    window._add_paths([FIXTURES / "btn_filled.png", FIXTURES / "no_match.png"])
    assert window.file_list.count() == 2
    window.file_list.setCurrentRow(0)
    window.file_list.setFocus()

    QTest.keyClick(window.file_list, Qt.Key_Delete)
    QApplication.processEvents()

    assert window.file_list.count() == 1
    assert len(window._entries) == 1


def test_save_uses_inline_summary_not_modal(window, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: calls.append("information")),
    )

    window._on_save_done(SaveOutcome())

    assert calls == [], "saving must not open a modal dialog"
    assert window.banner_label.isVisible()
    assert window.banner_label.text().strip() != ""

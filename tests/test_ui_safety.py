"""UI safety tests for the simplified single-screen layout (Stage U)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox,
)

from src.core.models import AnalysisResult, Region, RegionKind
from src.ui.main_window import MainWindow
from src.utils.image_utils import load_image

FIXTURE = Path("tests/fixtures/btn_filled.png")


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


def _visible_controls(win):
    types = (QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QCheckBox, QSpinBox)
    out = []
    for kind in types:
        out.extend(w for w in win.findChildren(kind) if w.isVisible())
    return out


AUXILIARY_CONTROLS = {
    "segment_left", "segment_right",       # preview mode switch
    "zoom_btn", "zoom_square",             # zoom toolbar
    "uncertain_filter",                    # region review filter
}


def test_control_budget_is_small(window):
    """D8: the workflow stays compact; view helpers live in a small toolbar."""
    visible = _visible_controls(window)
    primary = [w for w in visible if w.objectName() not in AUXILIARY_CONTROLS]
    assert len(primary) <= 12, [
        (type(w).__name__, w.objectName()) for w in primary
    ]
    assert len(visible) <= 20, [
        (type(w).__name__, w.objectName()) for w in visible
    ]


def test_advanced_panel_collapsed_by_default(window):
    panel = window.findChild(QPushButton, "advanced_toggle_btn")
    assert panel is not None
    assert panel.isCheckable()
    assert not panel.isChecked()


def test_analyze_disabled_until_images_and_keywords(window):
    assert not window.analyze_btn.isEnabled()
    window._add_paths([FIXTURE])
    assert not window.analyze_btn.isEnabled()
    window.keyword_edit.setPlainText("VPN")
    assert window.analyze_btn.isEnabled()
    window.keyword_edit.clear()
    assert not window.analyze_btn.isEnabled()


def test_no_validation_modal_spam(window, monkeypatch):
    calls = []
    for method in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox, method,
            staticmethod(lambda *a, _m=method, **k: calls.append(_m)),
        )
    window.analyze_btn.click()  # disabled: must not open anything
    window._add_paths([FIXTURE])
    window.keyword_edit.clear()
    window.analyze_btn.click()
    assert calls == []


def test_save_disabled_when_only_uncertain_regions(window):
    window._add_paths([FIXTURE])
    entry = window._all_entries()[0]
    loaded = load_image(FIXTURE)
    region = Region(id=0, kind=RegionKind.UNCERTAIN, bbox=(10, 10, 40, 20),
                    confidence=0.4, enabled=False)
    entry.result = AnalysisResult(str(FIXTURE), loaded.width, loaded.height, regions=[region])
    window._show_entry(entry)

    assert not window.save_btn.isEnabled()
    assert not window.save_all_btn.isEnabled()


def test_result_view_hides_boxes(window):
    window.preview.set_show_region_boxes(False)
    assert window.preview.show_region_boxes is False


def test_responsive_1366x768(window):
    assert window.minimumSize().width() <= 1366
    assert window.analyze_btn.height() >= 32

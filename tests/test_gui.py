"""GUI logic tests (skipped when PySide6 is not installed)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from src.core.models import AnalysisResult, Region, RegionKind
from src.ui.main_window import MainWindow
from src.utils.image_utils import load_image

FIXTURE = "tests/fixtures/btn_filled.png"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qt_app):
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    yield win
    win.close()


def _make_window_with_region(window, kind=RegionKind.TEXT, source="ocr"):
    from pathlib import Path

    import numpy as np

    window._add_paths([Path(FIXTURE)])
    entry = window._all_entries()[0]
    region = Region(
        id=0, kind=kind, bbox=(10, 20, 30, 40), confidence=0.55,
        enabled=(kind is not RegionKind.UNCERTAIN), source=source,
        evidence={"element_conf": 0.5} if source != "ocr" else {"reason": 1.0},
    )
    loaded = load_image(FIXTURE)
    region.mask = np.zeros((loaded.height, loaded.width), dtype="uint8")
    entry.result = AnalysisResult(FIXTURE, loaded.width, loaded.height, regions=[region])
    window._show_entry(entry)
    return entry, region


def test_window_builds(qt_app):
    win = MainWindow()
    for name in ("analyze_btn", "save_btn", "save_all_btn", "preview_btn",
                 "region_table", "file_list", "keyword_edit", "progress"):
        assert getattr(win, name) is not None


def test_edit_box_updates_region(window):
    entry, region = _make_window_with_region(window)
    assert region.mask is not None

    window.preview.regionEdited.emit(0, (12, 22, 44, 55))
    app = QApplication.instance()
    app.processEvents()

    assert region.bbox == (12, 22, 44, 55)
    assert region.mask is None


def test_uncertain_never_bulk_enabled(window):
    entry, region = _make_window_with_region(window, RegionKind.UNCERTAIN, source="color")
    assert region.enabled is False

    window._set_all_enabled(True)
    assert region.enabled is False

    checkbox = window.region_table.item(0, 0)
    checkbox.setCheckState(Qt.Checked)

    assert region.enabled is True
    assert region.kind in (RegionKind.ELEMENT, RegionKind.TEXT)


def test_region_policy_combo_sets_per_region_override(window):
    entry, region = _make_window_with_region(window, RegionKind.ELEMENT, source="color")
    combo = window.region_table.cellWidget(0, 3)
    assert combo is not None
    combo.setCurrentIndex(combo.findData("text"))
    QApplication.instance().processEvents()
    assert region.mask_mode == "text"
    assert region.mask is None
    combo.setCurrentIndex(combo.findData("element"))
    QApplication.instance().processEvents()
    assert region.mask_mode == "element"


def test_region_margin_spin_expands_and_shrinks_selected_region(window):
    entry, region = _make_window_with_region(window, RegionKind.TEXT)
    spin = window.region_table.cellWidget(0, 4)
    assert spin is not None
    spin.setValue(5)
    QApplication.instance().processEvents()
    assert region.margin_px == 5
    assert region.mask is None
    spin.setValue(-3)
    QApplication.instance().processEvents()
    assert region.margin_px == -3


def test_advanced_form_uses_width_and_stays_left(window):
    window.advanced_toggle_btn.setChecked(True)
    QApplication.instance().processEvents()

    outer = window.advanced_panel.layout()
    assert outer.spacing() >= 8

    # The output path is one long line that uses the panel width.
    assert window.output_edit.width() > 800
    # The left column still hugs the left edge.
    preset_x = window.preset_combo.mapTo(
        window, window.rect().topLeft()
    ).x()
    assert preset_x < 220, preset_x
    # Both right-hand controls end flush with the panel.
    assert window.engine_combo.x() + window.engine_combo.width() == (
        window.match_combo.x() + window.match_combo.width()
    )

    labels = {label.text(): label for label in window.findChildren(QLabel)}
    assert bool(labels["Output folder"].alignment() & Qt.AlignRight)

    from src.ui.controls import HintLabel

    for hint in window.findChildren(HintLabel):
        assert hint.sizePolicy().horizontalPolicy().name == "Maximum"


def test_icons_come_from_qtawesome_library(window):
    from src.ui import icons

    assert not icons.icon("info").isNull()
    for button in (window.add_files_btn, window.add_folder_btn,
                   window.analyze_btn, window.save_all_btn,
                   window.advanced_toggle_btn, window.result_btn):
        assert not button.icon().isNull(), button.text()

    img = icons.pixmap("chevron_down", 14, "#9a9a9a").toImage()
    assert not img.isNull() and img.width() == 14


def test_placeholder_is_dimmer_than_typed_text(window):
    from src.ui.theme import PLACEHOLDER, TEXT

    color = window.keyword_edit.palette().color(
        QPalette.ColorRole.PlaceholderText
    )
    assert color.name() == PLACEHOLDER.lower()
    assert color.lightness() < QColor(TEXT).lightness()


def test_labels_and_file_panel_have_horizontal_padding(window):
    panel = window.file_list.parentWidget()
    margins = panel.layout().contentsMargins()
    assert margins.left() > 0 and margins.right() > 0

    from src.ui.theme import STYLESHEET

    assert "QLabel {" in STYLESHEET
    assert "background: transparent" in STYLESHEET


def test_dropdowns_and_spinners_draw_visible_arrows(window):
    from src.ui.controls import ArrowComboBox, ArrowSpinBox
    from src.ui.theme import apply_theme

    for combo in (window.preset_combo, window.engine_combo,
                  window.policy_combo, window.match_combo):
        assert isinstance(combo, ArrowComboBox)
    assert isinstance(window.padding_spin, ArrowSpinBox)

    entry, _region = _make_window_with_region(window)
    assert isinstance(window.region_table.cellWidget(0, 3), ArrowComboBox)
    assert isinstance(window.region_table.cellWidget(0, 4), ArrowSpinBox)

    apply_theme(QApplication.instance())
    QApplication.instance().processEvents()

    combo = window.preset_combo
    combo.resize(240, 32)
    image = combo.grab().toImage()
    cx, cy = combo.width() - 11, combo.height() // 2
    max_light = max(
        image.pixelColor(x, y).lightness()
        for x in range(cx - 5, cx + 6)
        for y in range(cy - 4, cy + 5)
    )
    assert max_light > 100, "dropdown chevron is not painted"

    spin = window.padding_spin
    spin.resize(120, 36)
    image = spin.grab().toImage()
    for quarter in (0.27, 0.73):
        cx = spin.width() - 11
        cy = int(spin.height() * quarter)
        max_light = max(
            image.pixelColor(x, y).lightness()
            for x in range(cx - 5, cx + 6)
            for y in range(cy - 4, cy + 5)
        )
        assert max_light > 100, f"spinbox chevron at {quarter} is not painted"


def test_keyword_placeholder_hides_on_focus_and_returns_when_empty(window):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    edit = window.keyword_edit
    hint = edit._hint
    assert hint
    edit.clear()

    edit.focusInEvent(QFocusEvent(QEvent.FocusIn))
    assert edit.placeholderText() == "", "placeholder must disappear while typing"

    edit.focusOutEvent(QFocusEvent(QEvent.FocusOut))
    assert edit.placeholderText() == hint, "empty field gets the hint back"

    edit.clear_ghost_guard()
    edit.setPlainText("VPN")
    edit.focusOutEvent(QFocusEvent(QEvent.FocusOut))
    assert edit.placeholderText() == ""


def test_cleared_inputs_restore_placeholder_after_focus_out(window):
    from PySide6.QtTest import QTest

    app = QApplication.instance()
    window.show()
    cases = [
        (window.keyword_edit, window.keyword_edit.toPlainText,
         window.keyword_edit.setPlainText, window.keyword_edit._hint),
        (window.output_edit, window.output_edit.text,
         window.output_edit.setText, window.output_edit.placeholderText()),
    ]
    for edit, get_text, set_text, hint in cases:
        assert not edit.testAttribute(Qt.WA_InputMethodEnabled)
        # First-flow: type and delete before ever leaving the field.
        edit.clear()
        edit.setFocus()
        app.processEvents()
        QTest.keyClicks(edit, "abcd")
        for _ in range(4):
            QTest.keyClick(edit, Qt.Key_Backspace)
        app.processEvents()

        window.add_files_btn.setFocus()
        app.processEvents()

        assert get_text() == ""
        assert edit.placeholderText() == hint

        # Second-flow: leave the field while non-empty, come back and clear.
        edit.setFocus()
        app.processEvents()
        QTest.keyClicks(edit, "abcd")
        window.add_files_btn.setFocus()
        app.processEvents()

        edit.setFocus()
        app.processEvents()
        for _ in range(4):
            QTest.keyClick(edit, Qt.Key_Backspace)
        window.add_files_btn.setFocus()
        app.processEvents()

        assert get_text() == ""
        assert edit.placeholderText() == hint

        # A single legitimate character typed into an empty field must stay.
        edit.clear()
        edit.setFocus()
        app.processEvents()
        QTest.keyClicks(edit, "a")
        window.add_files_btn.setFocus()
        app.processEvents()
        assert get_text() == "a"
        edit.clear()

        # While the post-clear guard is active, an IME commit is discarded.
        edit._blurred_empty = True
        set_text("a")
        assert get_text() == ""
        edit._blurred_empty = False
        assert edit.placeholderText() == hint

        # And a delayed commit that arrives while the timers are still active.
        edit.clear()
        edit.setFocus()
        app.processEvents()
        QTest.keyClicks(edit, "abcd")
        for _ in range(4):
            QTest.keyClick(edit, Qt.Key_Backspace)
        window.add_files_btn.setFocus()
        app.processEvents()
        QTest.qWait(50)
        set_text("a")
        QTest.qWait(400)
        assert get_text() == ""
        assert edit.placeholderText() == hint


def test_only_three_labels_carry_a_grey_info_icon(window):
    from src.ui.controls import HintLabel

    hints = {hint.text(): hint for hint in window.findChildren(HintLabel)}
    assert set(hints) == {"Keywords", "Engine", "Padding"}

    for hint in hints.values():
        assert hint.badge.text() == ""
        pixmap = hint.badge.pixmap()
        assert pixmap is not None and not pixmap.isNull()
        assert hint.badge.toolTip() == hint.hint()
        assert hint.hint()
        # The icon itself is monochrome grey...
        image = pixmap.toImage()
        greys = [
            image.pixelColor(x, y)
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 0
        ]
        assert greys
        assert all(max(c.red(), c.green(), c.blue())
                   - min(c.red(), c.green(), c.blue()) <= 2 for c in greys)
        # ...and the text label alone must not pop a tooltip.
        assert hint.label.toolTip() == ""

    # Other form labels have neither an icon nor a tooltip.
    labels = {label.text(): label for label in window.findChildren(QLabel)}
    for name in ("Output folder", "Keyword preset", "Erase scope", "Matching"):
        assert name in labels
        assert labels[name].toolTip() == ""


def test_info_badge_shows_hint_on_hover_and_click(window, monkeypatch):
    from PySide6.QtCore import QEvent

    from src.ui import controls

    calls: list[tuple] = []

    class _DummyToolTip:
        @staticmethod
        def showText(*args) -> None:
            calls.append(args)

    monkeypatch.setattr(controls, "QToolTip", _DummyToolTip)
    hint = next(h for h in window.findChildren(controls.HintLabel)
                if h.text() == "Engine")

    hint.eventFilter(hint.badge, QEvent(QEvent.Enter))
    assert calls and calls[0][1] == hint.hint()

    hint.eventFilter(hint.badge, QEvent(QEvent.MouseButtonPress))
    assert len(calls) == 2 and calls[1][1] == hint.hint()


def test_no_regions_table_empty(window):
    from pathlib import Path

    window._add_paths([Path(FIXTURE)])
    window._show_entry(window._all_entries()[0])
    assert window.region_table.rowCount() == 0


def test_input_folder_button_is_unambiguous(window):
    assert window.add_folder_btn.text() == "Image folder…"
    assert "input" in window.add_folder_btn.toolTip()


def test_disabled_analyze_button_explains_next_step(window):
    from pathlib import Path

    window.file_list.clear()
    window.keyword_edit.setPlainText("")
    window._update_action_state()
    assert not window.analyze_btn.isEnabled()
    assert "Select images" in window.task_label.text()
    assert "Analyze" in window.task_label.text()

    window._add_paths([Path(FIXTURE)])
    window.keyword_edit.setPlainText("VPN")
    window._update_action_state()
    assert window.analyze_btn.isEnabled()
    assert window.task_label.text() == "Ready"


def test_advanced_dropdowns_share_left_edge_and_width(window):
    window.advanced_panel.setVisible(True)
    window.show()
    QApplication.instance().processEvents()

    engine = window.engine_combo.geometry()
    match = window.match_combo.geometry()
    assert engine.x() == match.x()
    assert engine.width() == match.width()


def test_analyzed_image_defaults_to_result_segment(window, monkeypatch):
    window._start_worker = lambda *args, **kwargs: None
    entry, _region = _make_window_with_region(window, RegionKind.TEXT)
    entry.status = "analyzed"

    assert window.original_btn.objectName() == "segment_left"
    assert window.result_btn.objectName() == "segment_right"

    window._select_original()
    assert window.original_btn.isChecked()

    window._show_entry(entry)
    assert window.result_btn.isChecked()
    assert not window.original_btn.isChecked()

    # Choosing "Original" explicitly shows the untouched image...
    window.original_btn.setChecked(True)
    QApplication.instance().processEvents()
    assert window.original_btn.isChecked()
    assert not window.result_btn.isChecked()

    # ...but selecting an analyzed image again goes back to its result.
    window._show_entry(entry)
    assert window.result_btn.isChecked()


def test_uncertain_badge_and_filter(window):
    entry, _region = _make_window_with_region(
        window, RegionKind.UNCERTAIN, source="color"
    )
    text_region = Region(
        id=1, kind=RegionKind.TEXT, bbox=(50, 20, 30, 40), confidence=0.9,
        enabled=True, source="ocr", evidence={"reason": 1.0},
    )
    entry.result.regions.append(text_region)
    window._populate_regions_table(entry.result.regions)

    holder = window.region_table.cellWidget(0, 1)
    assert holder is not None
    badge = holder.findChild(QLabel, "uncertain_badge")
    assert badge is not None and badge.text() == "Needs review"
    assert window.region_table.cellWidget(1, 1) is None
    assert "need review" in window.regions_title.text()

    window.uncertain_filter.setChecked(True)
    QApplication.instance().processEvents()
    assert not window.region_table.isRowHidden(0)
    assert window.region_table.isRowHidden(1)

    window.uncertain_filter.setChecked(False)
    QApplication.instance().processEvents()
    assert not window.region_table.isRowHidden(1)

    # Approving the uncertain region removes the badge and shows its type.
    window.region_table.item(0, 0).setCheckState(Qt.Checked)
    QApplication.instance().processEvents()
    assert window.region_table.cellWidget(0, 1) is None
    assert window.region_table.item(0, 1).text() in ("Text", "Element")


def test_zoom_controls(window):
    from pathlib import Path

    assert not window.zoom_in_btn.isEnabled()
    assert window.zoom_fit_btn.text() == "Fit"
    assert window.zoom_100_btn.text() == "100%"

    window._add_paths([Path(FIXTURE)])
    window._show_entry(window._all_entries()[0])
    QApplication.instance().processEvents()
    assert window.zoom_in_btn.isEnabled()
    assert window.zoom_out_btn.isEnabled()

    start = window.preview.scale_factor()
    window.zoom_in_btn.click()
    assert window.preview.scale_factor() > start

    window.zoom_100_btn.click()
    assert abs(window.preview.scale_factor() - 1.0) < 1e-6

    window.zoom_fit_btn.click()
    assert window.preview._fit is True

    before = window.preview.scale_factor()
    window.zoom_out_btn.click()
    assert window.preview.scale_factor() < before


def test_preview_and_regions_have_gaps_from_splitter(window):
    work = window.work_panel.layout().contentsMargins()
    assert work.left() >= 6 and work.right() >= 6
    assert window.preview_wrap.layout().contentsMargins().bottom() >= 6
    assert window.regions_wrap.layout().contentsMargins().top() >= 6


def test_info_icon_hugs_its_label(window):
    window.advanced_toggle_btn.setChecked(True)
    QApplication.instance().processEvents()
    from src.ui.controls import HintLabel

    hints = {h.text(): h for h in window.findChildren(HintLabel)}
    for name in ("Padding", "Engine", "Keywords"):
        hint = hints[name]
        gap = hint.badge.geometry().left() - hint.label.geometry().right() - 1
        assert 0 <= gap <= 6, (name, gap)


def test_switching_analyzed_images_does_not_blink_result_off(window, monkeypatch):
    calls = []
    window._start_worker = lambda *args, **kwargs: calls.append(1)
    entry, _region = _make_window_with_region(window, RegionKind.TEXT)
    entry.status = "analyzed"

    window._show_entry(entry)
    assert window.result_btn.isChecked()
    before = len(calls)

    toggles: list[bool] = []
    window.result_btn.toggled.connect(toggles.append)
    window._show_entry(entry)

    assert toggles == [], "Result must not blink through Original when switching images"
    assert len(calls) == before + 1, "the new image still needs its result render"


def test_preview_render_does_not_touch_global_busy_ui(window):
    stage: list[str] = []
    window._start_preview_worker = lambda *a, **k: stage.append("preview")
    window._set_busy = lambda *a, **k: stage.append("busy")

    window._start_worker(lambda progress, cancelled: None,
                         lambda payload: None, "preview", quiet=True)

    assert stage == ["preview"], "preview must not enter the global busy state"
    assert not window._busy


def test_switching_analyzed_image_uses_quiet_preview(window, monkeypatch):
    calls: list[dict] = []
    window._start_worker = lambda *a, **k: calls.append(k)
    entry, _region = _make_window_with_region(window, RegionKind.TEXT)
    entry.status = "analyzed"

    window._show_entry(entry)

    assert calls and calls[-1].get("quiet") is True
    assert not window._busy


def test_result_button_text_stays_stable_while_rendering(window, monkeypatch):
    calls: list[dict] = []
    window._start_worker = lambda *a, **k: calls.append(k)
    entry, _region = _make_window_with_region(window, RegionKind.TEXT)
    entry.status = "analyzed"

    window._show_entry(entry)

    assert calls and calls[-1].get("quiet") is True
    assert window.result_btn.text() == "Result"
    assert "Building" not in window.result_btn.text()


def test_result_button_disabled_until_image_selected(window):
    from pathlib import Path

    assert not window.result_btn.isEnabled()
    assert window.original_btn.isEnabled()

    window._add_paths([Path(FIXTURE)])
    window._show_entry(window._all_entries()[0])
    assert window.result_btn.isEnabled()

    window._show_entry(None)
    assert not window.result_btn.isEnabled()
    assert window.original_btn.isChecked()

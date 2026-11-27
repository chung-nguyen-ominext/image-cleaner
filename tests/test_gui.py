"""GUI logic tests (skipped when PySide6 is not installed)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QLabel, QToolButton,
)

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
                 "region_list", "file_list", "keyword_edit", "progress"):
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

    card = window.region_list.cards()[0]
    # Regression: the card used to set a dynamic property literally named
    # "enabled", which disabled the widget and made review cards unusable.
    assert card.isEnabled()
    card.check.setChecked(True)

    assert region.enabled is True
    assert region.kind in (RegionKind.ELEMENT, RegionKind.TEXT)


def test_region_policy_combo_sets_per_region_override(window):
    entry, region = _make_window_with_region(window, RegionKind.ELEMENT, source="color")
    combo = window.region_list.cards()[0].mode
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
    spin = window.region_list.cards()[0].padding
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
    window.advanced_panel.show()
    QApplication.instance().processEvents()

    assert window.advanced_panel.objectName() == "advanced_dialog"
    # The output path is the one long line that uses the dialog width.
    assert window.output_edit.width() > window.preset_combo.width()

    labels = {
        label.text(): label
        for label in window.advanced_panel.findChildren(QLabel)
    }
    assert bool(labels["Output folder"].alignment() & Qt.AlignRight)

    from src.ui.controls import HintLabel

    for hint in window.advanced_panel.findChildren(HintLabel):
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

    color = window.keyword_edit._editor.palette().color(
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
    card = window.region_list.cards()[0]
    assert isinstance(card.mode, ArrowComboBox)
    assert isinstance(card.padding, ArrowSpinBox)

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

    # The region card owns its own controls; they must paint arrows too.
    window.region_list.select_region(card.region.id)
    QApplication.instance().processEvents()
    card.mode.resize(160, 30)
    image = card.mode.grab().toImage()
    cx, cy = card.mode.width() - 11, card.mode.height() // 2
    max_light = max(
        image.pixelColor(x, y).lightness()
        for x in range(cx - 5, cx + 6)
        for y in range(cy - 4, cy + 5)
    )
    assert max_light > 100, "region erase-mode chevron is not painted"

    card.padding.resize(100, 30)
    image = card.padding.grab().toImage()
    for quarter in (0.27, 0.73):
        cx = card.padding.width() - 11
        cy = int(card.padding.height() * quarter)
        max_light = max(
            image.pixelColor(x, y).lightness()
            for x in range(cx - 5, cx + 6)
            for y in range(cy - 4, cy + 5)
        )
        assert max_light > 100, (
            f"region padding chevron at {quarter} is not painted"
        )


def test_keyword_chip_input_commits_on_enter_and_comma(window):
    from PySide6.QtTest import QTest

    app = QApplication.instance()
    window.show()
    edit = window.keyword_edit
    hint = edit.placeholderText()
    assert hint
    edit.clear()
    editor = edit._editor
    editor.setFocus()
    app.processEvents()

    QTest.keyClicks(editor, "VPN")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert edit.keywords() == ["VPN"]
    assert editor.text() == ""
    assert editor.placeholderText() == hint

    QTest.keyClicks(editor, "Internal,")
    app.processEvents()
    assert edit.keywords() == ["VPN", "Internal"]
    assert editor.text() == ""

    # Backspace on an empty editor removes the last chip and nothing more.
    QTest.keyClick(editor, Qt.Key_Backspace)
    app.processEvents()
    assert edit.keywords() == ["VPN"]

    # A chip's close button removes exactly that keyword.
    chip = edit._chips_layout.itemAt(0).widget()
    close = chip.findChild(QToolButton, "chip_close")
    assert close is not None
    close.click()
    app.processEvents()
    assert edit.keywords() == []
    assert editor.placeholderText() == hint


def test_cleared_inputs_restore_placeholder_after_focus_out(window):
    from PySide6.QtTest import QTest

    app = QApplication.instance()
    window.show()

    editor = window.keyword_edit._editor
    hint = window.keyword_edit.placeholderText()
    editor.setFocus()
    app.processEvents()
    QTest.keyClicks(editor, "abcd")
    for _ in range(4):
        QTest.keyClick(editor, Qt.Key_Backspace)
    window.add_files_btn.setFocus()
    app.processEvents()
    assert editor.text() == ""
    assert editor.placeholderText() == hint

    output = window.output_edit
    output_hint = output.placeholderText()
    output.clear()
    output.setFocus()
    app.processEvents()
    QTest.keyClicks(output, "abcd")
    for _ in range(4):
        QTest.keyClick(output, Qt.Key_Backspace)
    window.add_files_btn.setFocus()
    app.processEvents()
    assert output.text() == ""
    assert output.placeholderText() == output_hint

    # A single legitimate character survives the focus change.
    output.setFocus()
    app.processEvents()
    QTest.keyClicks(output, "a")
    window.add_files_btn.setFocus()
    app.processEvents()
    assert output.text() == "a"
    output.clear()


def test_only_two_labels_carry_a_grey_info_icon(window):
    from src.ui.controls import HintLabel

    hints = {hint.text(): hint for hint in window.findChildren(HintLabel)}
    assert set(hints) == {"Engine", "Padding"}

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
            if image.pixelColor(x, y).alpha() > 32
        ]
        assert greys
        # Cool grey, close to neutral: no coloured icon in the form.
        assert all(max(c.red(), c.green(), c.blue())
                   - min(c.red(), c.green(), c.blue()) <= 40 for c in greys)
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
    assert window.region_list.cards() == []
    assert window.region_list.placeholder.isVisible()


def test_input_folder_button_is_unambiguous(window):
    assert window.add_folder_btn.text() == "Choose folder"
    assert "folder" in window.add_folder_btn.toolTip().casefold()
    assert not window.add_folder_btn.icon().isNull()
    assert window.add_folder_btn.accessibleName() == "Add image folder"


def test_disabled_analyze_button_explains_next_step(window):
    from pathlib import Path

    window.file_list.clear()
    window.keyword_edit.setPlainText("")
    window._update_action_state()
    assert not window.analyze_btn.isEnabled()
    assert window.analyze_btn.toolTip() == (
        "Add images and at least one keyword first"
    )

    window._add_paths([Path(FIXTURE)])
    window.keyword_edit.setPlainText("VPN")
    window._update_action_state()
    assert window.analyze_btn.isEnabled()
    assert window.analyze_btn.toolTip() == ""
    assert window.analyze_btn.text() == "Analyze 1 image"


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

    # ...and that choice sticks while switching images: only the picture
    # changes, the segment does not flip back to Result behind the user.
    window._show_entry(entry)
    assert window.original_btn.isChecked()
    assert not window.result_btn.isChecked()

    # Asking for Result again brings the analysed view back.
    window.result_btn.setChecked(True)
    QApplication.instance().processEvents()
    assert window.result_btn.isChecked()
    assert not window.original_btn.isChecked()


def test_uncertain_badge_and_filter(window):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    entry, _region = _make_window_with_region(
        window, RegionKind.UNCERTAIN, source="color"
    )
    text_region = Region(
        id=1, kind=RegionKind.TEXT, bbox=(50, 20, 30, 40), confidence=0.9,
        enabled=True, source="ocr", evidence={"reason": 1.0},
    )
    entry.result.regions.append(text_region)
    window._populate_regions_table(entry.result.regions)
    QApplication.instance().processEvents()

    cards = {card.region.id: card for card in window.region_list.cards()}
    assert cards[0].review_tag.isVisible()
    assert cards[0].review_tag.text() == "Needs review"
    assert not cards[1].review_tag.isVisible()
    assert window.regions_title.text() == "Detected regions 2"

    # Selecting another region first makes the click below meaningful.
    window.region_list.select_region(1, emit=True)
    QApplication.instance().processEvents()
    assert window.preview.selected_region_id() == 1

    # A review card must be clickable: it selects its box on the image and
    # expands its details, deselecting the other cards.
    assert cards[0].isEnabled()
    QTest.mouseClick(
        cards[0], Qt.LeftButton, Qt.NoModifier,
        QPoint(cards[0].width() // 2, cards[0].height() // 2),
    )
    QApplication.instance().processEvents()
    assert window.preview.selected_region_id() == 0
    assert cards[0].details.isVisible()
    assert not cards[1].details.isVisible()

    window.uncertain_filter.setChecked(True)
    QApplication.instance().processEvents()
    assert cards[0].isVisible()
    assert not cards[1].isVisible()

    window.uncertain_filter.setChecked(False)
    QApplication.instance().processEvents()
    assert cards[1].isVisible()

    # Approving the uncertain region clears the review badge and shows its type.
    cards[0].check.setChecked(True)
    QApplication.instance().processEvents()
    assert not cards[0].review_tag.isVisible()
    assert cards[0].kind_tag.text() in ("Text", "Button")


def test_zoom_controls(window):
    from pathlib import Path

    assert not window.zoom_in_btn.isEnabled()
    assert window.zoom_fit_btn.text() == "Fit"
    assert window.zoom_100_btn.text() == "1:1"

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
    # The three panes are separated by a wide splitter handle, and each panel
    # keeps its own inner padding instead of touching the preview canvas.
    assert window.body_splitter.count() == 3
    assert window.body_splitter.handleWidth() >= 8
    assert window.body_splitter.widget(1) is window.preview_panel
    assert window.preview_panel.objectName() == "preview_panel"
    assert window.right_panel.layout().contentsMargins().left() >= 8
    assert window.left_panel.layout().contentsMargins().right() >= 8


def test_info_icon_hugs_its_label(window):
    window.advanced_toggle_btn.setChecked(True)
    QApplication.instance().processEvents()
    from src.ui.controls import HintLabel

    hints = {h.text(): h for h in window.findChildren(HintLabel)}
    for name in ("Padding", "Engine"):
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
    # Selected but not analyzed: there is no Result to show yet.
    assert not window.result_btn.isEnabled()

    entry = window._all_entries()[0]
    entry.result = AnalysisResult(FIXTURE, 100, 100, regions=[])
    window._show_entry(entry)
    assert window.result_btn.isEnabled()

    window._show_entry(None)
    assert not window.result_btn.isEnabled()
    assert window.original_btn.isChecked()


def test_preview_fills_leftover_width_when_panels_toggle(window):
    app = QApplication.instance()
    window._set_panel_collapsed("left", False)
    window._set_panel_collapsed("right", False)
    window.resize(1200, 800)
    app.processEvents()

    def assert_fills(tag: str) -> None:
        app.processEvents()
        app.processEvents()
        sizes = window.body_splitter.sizes()
        total = (
            window.body_splitter.width()
            - 2 * window.body_splitter.handleWidth()
        )
        assert abs(sum(sizes) - total) <= 1, (tag, sizes, total)
        assert sizes[1] == total - sizes[0] - sizes[2], (tag, sizes)

    window.left_collapse_btn.click()
    assert_fills("images closed")
    window.right_collapse_btn.click()
    assert_fills("regions closed")

    # The bug only showed up after the window was resized while collapsed:
    # reopening then restored stale absolute widths and left a blank strip.
    window.resize(1440, 900)
    app.processEvents()
    app.processEvents()
    window.left_collapse_btn.click()
    assert_fills("images reopened after resize")
    window.right_collapse_btn.click()
    assert_fills("regions reopened after resize")

    window.left_collapse_btn.click()
    window.right_collapse_btn.click()
    assert_fills("both closed")
    window.right_collapse_btn.click()
    assert_fills("regions reopened from rails")

    # Leave both panes open for the following tests / next run.
    window._set_panel_collapsed("left", False)
    window._set_panel_collapsed("right", False)
    assert_fills("both reopened")
    # Reopening restores the remembered panel widths (not the splitter's
    # initial size hints), so the preview keeps the rest.
    assert window.body_splitter.sizes()[0] == window._left_width
    assert window.body_splitter.sizes()[2] == window._right_width


def test_collapsed_panel_arrow_is_not_clipped(window):
    window._set_panel_collapsed("left", True)
    window._set_panel_collapsed("right", True)
    QApplication.instance().processEvents()
    QApplication.instance().processEvents()

    for button, panel in (
        (window.left_collapse_btn, window.left_panel),
        (window.right_collapse_btn, window.right_panel),
    ):
        host = button.parentWidget()
        assert button.x() >= 0
        assert button.x() + button.width() <= host.width(), (
            button.x(), button.width(), host.width()
        )
        assert host.x() + button.x() + button.width() <= panel.width()


def test_analyze_this_image_only_keeps_the_current_selection(window, monkeypatch):
    from pathlib import Path

    window._add_paths([Path(FIXTURE), Path("tests/fixtures/no_match.png")])
    window.file_list.setCurrentRow(1)
    entry = window._all_entries()[1]

    captured = {}

    def fake_analyze(entries, **kwargs):
        captured["entries"] = list(entries)
        captured.update(kwargs)

    monkeypatch.setattr(window, "_analyze_entries", fake_analyze)
    window._analyze_current()
    assert [item.path for item in captured["entries"]] == [entry.path]
    assert captured["select_first"] is False

    # Completion must not jump back to the first image in "this image" mode.
    window._start_worker = lambda *args, **kwargs: None
    result = AnalysisResult(str(entry.path.resolve()), 100, 100, regions=[])
    window._on_analyze_done(
        [(str(entry.path.resolve()), result, "")], select_first=False
    )
    assert window.file_list.currentRow() == 1
    assert window._all_entries()[1].result is not None


def test_shortcuts_are_actions_and_trigger_their_slots(window):
    from PySide6.QtGui import QKeySequence

    actions = window._shortcut_actions
    assert actions, "shortcut actions must be kept alive on the window"
    portable = {
        sequence.toString(QKeySequence.PortableText)
        for action in actions
        for sequence in action.shortcuts()
    }
    for expected in ("Ctrl+O", "Ctrl+Return", "Ctrl+S", "Ctrl+B", "Ctrl+0", "Ctrl+1"):
        assert expected in portable, (expected, portable)

    toggle = next(a for a in actions if a.text() == "Toggle panels")
    window._set_panel_collapsed("left", False)
    window._set_panel_collapsed("right", False)
    toggle.trigger()
    QApplication.instance().processEvents()
    assert window._left_collapsed and window._right_collapsed
    toggle.trigger()
    QApplication.instance().processEvents()
    assert not window._left_collapsed and not window._right_collapsed


def test_shortcut_labels_use_native_text(window):
    import sys

    from src.ui.main_window import _shortcut_label

    native_o = "⌘O" if sys.platform == "darwin" else "Ctrl+O"
    native_b = "⌘B" if sys.platform == "darwin" else "Ctrl+B"
    assert _shortcut_label("Ctrl+O") == native_o
    assert window.add_files_btn.toolTip() == f"Add images ({native_o})"
    assert window.left_collapse_btn.toolTip() == f"Hide images panel ({native_b})"


def test_clicking_region_card_switches_to_original(window):
    import numpy as np

    window._start_worker = lambda *args, **kwargs: None
    entry, region = _make_window_with_region(window, RegionKind.TEXT)
    app = QApplication.instance()
    result_bgr = np.zeros((20, 20, 3), dtype="uint8")

    # Mimic the finished Result render: result pixmap shown, boxes hidden.
    window.preview.set_result_image(result_bgr)
    window.preview.set_show_region_boxes(False)
    window.result_btn.setChecked(True)
    app.processEvents()
    assert window.result_btn.isChecked()
    assert not window.preview.show_region_boxes

    window.region_list._on_card_selected(region.id)
    app.processEvents()
    assert window.original_btn.isChecked()
    assert not window.result_btn.isChecked()
    assert window.preview.show_region_boxes
    assert window.preview.selected_region_id() == region.id
    assert window._prefer_result is False

    # Clicking the already-selected card from Result also returns to Original.
    window.preview.set_result_image(result_bgr)
    window.preview.set_show_region_boxes(False)
    window.result_btn.setChecked(True)
    app.processEvents()
    window.region_list._on_card_selected(region.id)
    app.processEvents()
    assert window.original_btn.isChecked()
    assert window.preview.show_region_boxes


def test_region_padding_arrows_are_fully_clickable(window):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    entry, region = _make_window_with_region(window, RegionKind.TEXT)
    card = window.region_list.cards()[0]
    window.region_list.select_region(region.id)
    QApplication.instance().processEvents()

    spin = card.padding
    # The spin box's line-edit child must stop before the up/down buttons.
    # If it covers them, hovering shows an I-beam and clicks are swallowed.
    for y in (4, spin.height() - 4):
        for x in range(spin.width() - 22, spin.width()):
            assert spin.childAt(QPoint(x, y)) is None, (x, y)

    spin.setValue(0)
    QTest.mouseClick(
        spin, Qt.LeftButton, Qt.NoModifier,
        QPoint(spin.width() - 11, int(spin.height() * 0.27)),
    )
    QApplication.instance().processEvents()
    assert spin.value() == 1
    assert region.margin_px == 1

    # The left half of the same chevron works too.
    QTest.mouseClick(
        spin, Qt.LeftButton, Qt.NoModifier,
        QPoint(spin.width() - 15, int(spin.height() * 0.27)),
    )
    QApplication.instance().processEvents()
    assert spin.value() == 2
    assert region.margin_px == 2

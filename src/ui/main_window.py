"""Main window: keyword input, batch queue, preview, region review, save."""

from __future__ import annotations

import copy
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QSpacerItem, QSplitter, QStatusBar, QToolButton, QVBoxLayout, QWidget,
)

from src.config.paths import presets_dir, settings_path
from src.config.settings import (
    AppSettings, ProcessOptions, load_keyword_preset, normalize_keyword_list,
    save_keyword_preset,
)
from src.core.batch_processor import collect_images, summarize
from src.core.mask_generator import build_region_mask, region_is_element
from src.core.mask_refinement import refine_element_mask
from src.core.models import AnalysisResult, ImageStatus, ProcessReport, Region, RegionKind
from src.core.pipeline import Pipeline
from src.ui import icons
from src.ui.controls import ArrowComboBox, ArrowSpinBox, HintLabel, HintLineEdit
from src.ui.preview import PreviewCanvas
from src.ui.theme import (
    MUTED, ON_PRIMARY, ON_SUCCESS, RED, REGION, WARNING,
    dim_placeholder, repolish,
)
from src.ui.widgets import (
    DropZone, ElidedLabel, EmptyState, ImageRow, KeywordChipInput, RegionCardList,
)
from src.ui.workers import FunctionWorker
from src.utils.image_utils import LoadedImage, SUPPORTED_EXTS, load_image

log = logging.getLogger("imagecleaner.ui")


def _shortcut_label(sequence: str) -> str:
    """Shortcut text in the platform's native form (⌘O on macOS)."""
    return QKeySequence(sequence).toString(QKeySequence.NativeText)


BUILTIN_PRESETS: dict[str, list[str]] = {
    "VPN / network": ["VPN", "Proxy", "WireGuard", "OpenVPN"],
    "Security labels": ["Confidential", "Internal Use Only", "Do Not Share", "Restricted"],
    "Account": ["Username", "Password", "Email", "API key"],
}

def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


MASK_RGB = {
    RegionKind.ELEMENT: _rgb(REGION),
    RegionKind.TEXT: _rgb(REGION),
    RegionKind.UNCERTAIN: _rgb(WARNING),
}


class QtLogHandler(logging.Handler, QObject):
    message = Signal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self, logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.message.emit(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never crash the app
            pass


@dataclass
class ImageEntry:
    path: Path
    result: AnalysisResult | None = None
    status: str = "pending"
    error: str = ""


@dataclass
class SaveOutcome:
    reports: list[ProcessReport] = field(default_factory=list)
    analyzed: dict[str, AnalysisResult] = field(default_factory=dict)
    elapsed_s: float = 0.0


def _snapshot(result: AnalysisResult) -> AnalysisResult:
    """Copy the analysis (not the full-size masks) for use in a worker thread."""
    clone = copy.copy(result)
    clone.regions = []
    for region in result.regions:
        rc = copy.copy(region)
        rc.mask = None
        clone.regions.append(rc)
    # Mask refinement needs the other OCR lines to protect unrelated text.
    # Dropping them made the preview delete text the saved render preserved.
    clone.lines = copy.deepcopy(result.lines)
    return clone


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Image Cleaner")
        self.resize(1440, 900)
        self.setAcceptDrops(True)

        self._entries: dict[str, ImageEntry] = {}
        self._row_widgets: dict[str, ImageRow] = {}
        self._loaded: LoadedImage | None = None
        self._current_path: str | None = None
        self._worker: FunctionWorker | None = None
        self._preview_worker: FunctionWorker | None = None
        self._save_worker: FunctionWorker | None = None
        self._active_save_button: QPushButton | None = None
        self._save_button_state: tuple[str, QIcon, str, bool] | None = None
        self._busy = False
        self._updating_table = False
        self._prereq_hint = ""
        self._show_result_on_idle = False
        self._compare_after_render = False
        # User intent: keep showing the Result view when switching images.
        # Starts True so the first analyzed image opens on its result; picking
        # "Original" sets it False until the user asks for Result again.
        self._prefer_result = True
        self._left_collapsed = False
        self._right_collapsed = False
        self._left_width = 184
        self._right_width = 308
        self._restoring = False
        self._settings = AppSettings.load(settings_path())
        self._qt_settings = QSettings()

        self._log_handler = QtLogHandler()
        logger = logging.getLogger("imagecleaner")
        logger.setLevel(logging.INFO)
        logger.addHandler(self._log_handler)
        self._log_handler.message.connect(self._append_log)

        self._build_ui()
        self._set_zoom_controls_enabled(False)
        self._update_view_buttons()
        self._apply_settings()
        self._sync_segment_icons()
        self._install_shortcuts()
        self._restore_window_state()
        self._update_action_state()
        QTimer.singleShot(0, self.centralWidget().setFocus)

    # --------------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("app_root")
        central.setFocusPolicy(Qt.StrongFocus)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        root.addWidget(self._build_top_bar())

        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.setObjectName("body_splitter")
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(12)
        self.left_panel = self._build_left_panel()
        self.preview_panel = self._build_preview_panel()
        self.right_panel = self._build_right_panel()
        self.body_splitter.addWidget(self.left_panel)
        self.body_splitter.addWidget(self.preview_panel)
        self.body_splitter.addWidget(self.right_panel)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setStretchFactor(2, 0)
        self.body_splitter.splitterMoved.connect(self._on_splitter_moved)
        root.addWidget(self.body_splitter, 1)

        self._build_status_bar()
        self._build_advanced_dialog()
        self._build_log_dialog()
        self._build_tab_order()

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("top_bar")
        bar.setFixedHeight(56)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.add_files_btn = QPushButton("Add images")
        self.add_files_btn.setObjectName("add_files_btn")
        self.add_files_btn.setProperty("sizeClass", "toolbar")
        self.add_files_btn.setIcon(icons.icon("images"))
        self.add_files_btn.setAccessibleName("Add images")
        self.add_files_btn.setToolTip(f"Add images ({_shortcut_label('Ctrl+O')})")
        self.add_files_btn.clicked.connect(self._pick_files)
        row.addWidget(self.add_files_btn)

        self.add_folder_btn = QPushButton("Choose folder")
        self.add_folder_btn.setObjectName("add_folder_btn")
        self.add_folder_btn.setProperty("sizeClass", "toolbar")
        self.add_folder_btn.setIcon(icons.icon("folder"))
        self.add_folder_btn.setToolTip("Add every image in a folder")
        self.add_folder_btn.setAccessibleName("Add image folder")
        self.add_folder_btn.clicked.connect(self._pick_folder)
        row.addWidget(self.add_folder_btn)

        self.keyword_edit = KeywordChipInput()
        self.keyword_edit.textChanged.connect(self._on_inputs_changed)
        row.addWidget(self.keyword_edit, 3)

        row.addStretch(1)

        self.advanced_toggle_btn = QPushButton("Advanced")
        self.advanced_toggle_btn.setObjectName("advanced_toggle_btn")
        self.advanced_toggle_btn.setProperty("variant", "ghost")
        self.advanced_toggle_btn.setProperty("sizeClass", "toolbar")
        self.advanced_toggle_btn.setIcon(icons.icon("sliders"))
        self.advanced_toggle_btn.setCheckable(True)
        self.advanced_toggle_btn.setToolTip("Advanced options")
        self.advanced_toggle_btn.setAccessibleName("Advanced options")
        self.advanced_toggle_btn.toggled.connect(self._toggle_advanced)
        row.addWidget(self.advanced_toggle_btn)

        self.analyze_btn = QToolButton()
        self.analyze_btn.setObjectName("analyze_btn")
        self.analyze_btn.setProperty("variant", "primary")
        self.analyze_btn.setProperty("sizeClass", "toolbar")
        self.analyze_btn.setText("Analyze")
        self.analyze_btn.setIcon(icons.icon("analyze", ON_PRIMARY))
        self.analyze_btn.setIconSize(QSize(14, 14))
        self.analyze_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.analyze_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.analyze_btn.setAccessibleName("Analyze images")
        self.analyze_btn.clicked.connect(self._on_primary_clicked)

        self.analyze_menu = QMenu(self.analyze_btn)
        self.analyze_all_action = self.analyze_menu.addAction("Analyze all images")
        self.analyze_current_action = self.analyze_menu.addAction("Analyze this image only")
        self.analyze_all_action.triggered.connect(self._analyze_all)
        self.analyze_current_action.triggered.connect(self._analyze_current)
        self.analyze_btn.setMenu(self.analyze_menu)
        row.addWidget(self.analyze_btn)
        return bar

    def _build_advanced_dialog(self) -> None:
        """The rarely-used options live in a dialog so they never squeeze the preview."""
        dialog = QDialog(self)
        dialog.setObjectName("advanced_dialog")
        dialog.setWindowTitle("Advanced options")
        dialog.setModal(False)
        dialog.resize(820, 430)
        self.advanced_panel = dialog

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(14)

        title = QLabel("Advanced options", dialog)
        title.setObjectName("dialog_title")
        outer.addWidget(title)

        def field_label(text: str) -> QLabel:
            label = QLabel(text, dialog)
            label.setObjectName("field_label")
            label.setMinimumWidth(112)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return label

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        grid.addWidget(field_label("Output folder"), 0, 0)
        self.output_edit = HintLineEdit(dialog)
        self.output_edit.setPlaceholderText("Default: ~/ImageCleaner")
        dim_placeholder(self.output_edit)
        self.output_edit.textChanged.connect(self._on_output_changed)
        grid.addWidget(self.output_edit, 0, 1, 1, 3)
        self.output_browse_btn = QPushButton("Browse…", dialog)
        self.output_browse_btn.setIcon(icons.icon("folder"))
        self.output_browse_btn.clicked.connect(self._pick_output_dir)
        grid.addWidget(self.output_browse_btn, 0, 4)

        grid.addWidget(field_label("Keyword preset"), 1, 0)
        self.preset_combo = ArrowComboBox(dialog)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        grid.addWidget(self.preset_combo, 1, 1)
        self.save_preset_btn = QPushButton("Save preset…", dialog)
        self.save_preset_btn.setIcon(icons.icon("save"))
        self.save_preset_btn.setProperty("sizeClass", "compact")
        self.save_preset_btn.clicked.connect(self._save_preset)
        grid.addWidget(self.save_preset_btn, 1, 2)

        engine_hint = HintLabel(
            "Engine",
            "Picks the removal algorithm automatically. Telea/NS are fast; "
            "LaMa is slower but smoother.",
            dialog,
        )
        engine_hint.setMinimumWidth(112)
        grid.addWidget(engine_hint, 1, 3, Qt.AlignRight | Qt.AlignVCenter)
        self.engine_combo = ArrowComboBox(dialog)
        for label, value in [
            ("Auto", "auto"),
            ("Telea (fast)", "telea"),
            ("NS (fast)", "ns"),
            ("LaMa (slow)", "lama"),
        ]:
            self.engine_combo.addItem(label, value)
        grid.addWidget(self.engine_combo, 1, 4)

        grid.addWidget(field_label("Erase scope"), 2, 0)
        self.policy_combo = ArrowComboBox(dialog)
        self.policy_combo.addItem("Auto (text/element)", "auto")
        self.policy_combo.addItem("Text only", "text_only")
        grid.addWidget(self.policy_combo, 2, 1)

        grid.addWidget(field_label("Matching"), 2, 3, Qt.AlignRight | Qt.AlignVCenter)
        self.match_combo = ArrowComboBox(dialog)
        self.match_combo.addItem("OCR tolerant", "fuzzy")
        self.match_combo.addItem("Exact", "exact")
        grid.addWidget(self.match_combo, 2, 4)

        padding_hint = HintLabel(
            "Padding", "Expand every erase region by a few pixels.", dialog
        )
        padding_hint.setMinimumWidth(112)
        grid.addWidget(padding_hint, 3, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.padding_spin = ArrowSpinBox(dialog)
        self.padding_spin.setRange(0, 6)
        self.padding_spin.setSuffix(" px")
        self.padding_spin.setMaximumWidth(120)
        self.padding_spin.valueChanged.connect(self._on_padding_changed)
        grid.addWidget(self.padding_spin, 3, 1)

        self.show_mask_check = QCheckBox("Show erase mask", dialog)
        self.show_mask_check.setChecked(True)
        self.show_mask_check.toggled.connect(self._on_show_mask)
        grid.addWidget(self.show_mask_check, 3, 3)

        self.log_toggle_btn = QPushButton("View log", dialog)
        self.log_toggle_btn.setIcon(icons.icon("log"))
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setProperty("sizeClass", "compact")
        self.log_toggle_btn.toggled.connect(self._toggle_log)
        grid.addWidget(self.log_toggle_btn, 3, 4)

        outer.addLayout(grid)
        outer.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        done = QPushButton("Done", dialog)
        done.setProperty("variant", "primary")
        done.clicked.connect(dialog.accept)
        buttons.addWidget(done)
        outer.addLayout(buttons)

        dialog.finished.connect(self._on_advanced_closed)

    def _build_log_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("log_dialog")
        dialog.setWindowTitle("Log")
        dialog.setModal(False)
        dialog.resize(780, 300)
        self.log_box = dialog
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        self.log_view = QPlainTextEdit(dialog)
        self.log_view.setObjectName("log_view")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(800)
        layout.addWidget(self.log_view, 1)
        close = QPushButton("Close", dialog)
        close.clicked.connect(dialog.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        dialog.finished.connect(self._on_log_closed)

    def _build_left_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("left_panel")
        panel.setMinimumWidth(184)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # Wrap the header so it keeps its 28 px slot at the very top even when
        # the panel is collapsed (a bare layout would stretch and centre the
        # lone arrow button instead of leaving it at the top).
        header_host = QWidget(panel)
        header_host.setObjectName("panel_header")
        header_host.setFixedHeight(28)
        header_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header = QHBoxLayout(header_host)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.files_title = QLabel("Images 0")
        self.files_title.setObjectName("panel_title")
        header.addWidget(self.files_title)
        header.addStretch(1)
        self.clear_files_btn = QToolButton(header_host)
        self.clear_files_btn.setObjectName("panel_toggle")
        self.clear_files_btn.setIcon(icons.icon("remove"))
        self.clear_files_btn.setIconSize(QSize(14, 14))
        self.clear_files_btn.setToolTip("Clear all images from the list")
        self.clear_files_btn.setAccessibleName("Clear all images")
        self.clear_files_btn.setCursor(Qt.PointingHandCursor)
        self.clear_files_btn.clicked.connect(self._clear_all_files)
        header.addWidget(self.clear_files_btn)
        self.left_collapse_btn = QToolButton(header_host)
        self.left_collapse_btn.setObjectName("panel_toggle")
        self.left_collapse_btn.setIcon(icons.icon("chevron_left"))
        self.left_collapse_btn.setIconSize(QSize(14, 14))
        self.left_collapse_btn.setToolTip(
            f"Hide images panel ({_shortcut_label('Ctrl+B')})"
        )
        self.left_collapse_btn.setAccessibleName("Hide images panel")
        self.left_collapse_btn.setCursor(Qt.PointingHandCursor)
        header.addWidget(self.left_collapse_btn)
        layout.addWidget(header_host)

        self.file_list = QListWidget(panel)
        self.file_list.setObjectName("file_list")
        self.file_list.setFrameShape(QListWidget.NoFrame)
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.file_list.setUniformItemSizes(True)
        self.file_list.setSpacing(0)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_file_menu)
        self.file_list.installEventFilter(self)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        self.file_list.setAccessibleName("Images")
        layout.addWidget(self.file_list, 1)

        self.drop_zone = DropZone(panel)
        layout.addWidget(self.drop_zone)
        # Keeps the header pinned at the top when every content widget is
        # hidden (collapsed rail); has no effect while the list is visible.
        layout.addItem(
            QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )

        # Kept for compatibility with older smoke scripts; not shown.
        self.summary_label = QLabel("", panel)
        self.summary_label.setVisible(False)

        self._left_content = [self.file_list, self.drop_zone]
        self.left_collapse_btn.clicked.connect(
            lambda: self._set_panel_collapsed("left", not self._left_collapsed)
        )
        return panel

    def _build_preview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("preview_panel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.preview = PreviewCanvas(panel)
        self.preview.setObjectName("preview_canvas")
        self.preview.setMinimumSize(320, 200)
        self.preview.regionSelected.connect(self._on_canvas_region_selected)
        self.preview.regionEdited.connect(self._on_region_edited)
        self.preview.zoomChanged.connect(
            lambda scale: self.zoom_label.setText(f"{int(round(scale * 100))}%")
        )
        layout.addWidget(self.preview)

        self.preview_toolbar = QFrame(self.preview)
        self.preview_toolbar.setObjectName("preview_toolbar")
        self.preview_toolbar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        toolbar = QHBoxLayout(self.preview_toolbar)
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(4)

        self.original_btn = QPushButton("Original")
        self.original_btn.setObjectName("segment_left")
        self.original_btn.setIcon(icons.icon("original"))
        self.original_btn.setCheckable(True)
        self.original_btn.setChecked(True)
        self.result_btn = QPushButton("Result")
        self.result_btn.setObjectName("segment_right")
        self.result_btn.setIcon(icons.icon("result"))
        self.result_btn.setCheckable(True)
        for button in (self.original_btn, self.result_btn):
            button.setAutoExclusive(True)
        self.original_btn.toggled.connect(self._on_view_mode)
        self.result_btn.toggled.connect(self._on_view_mode)
        # Backwards-compatible alias for external smoke scripts.
        self.preview_btn = self.result_btn

        segment = QWidget(self.preview_toolbar)
        segment_row = QHBoxLayout(segment)
        segment_row.setContentsMargins(0, 0, 0, 0)
        segment_row.setSpacing(0)
        segment_row.addWidget(self.original_btn)
        segment_row.addWidget(self.result_btn)
        toolbar.addWidget(segment)

        toolbar.addWidget(self._toolbar_separator())

        self.zoom_fit_btn = QPushButton("Fit")
        self.zoom_fit_btn.setObjectName("zoom_btn")
        self.zoom_fit_btn.setIcon(icons.icon("fit"))
        self.zoom_fit_btn.setToolTip(
            f"Fit image to the preview ({_shortcut_label('Ctrl+0')})"
        )
        self.zoom_fit_btn.clicked.connect(lambda: self.preview.fit_to_view())
        toolbar.addWidget(self.zoom_fit_btn)

        self.zoom_100_btn = QPushButton("1:1")
        self.zoom_100_btn.setObjectName("zoom_btn")
        self.zoom_100_btn.setToolTip(f"View at 1:1 ({_shortcut_label('Ctrl+1')})")
        self.zoom_100_btn.clicked.connect(lambda: self.preview.zoom_to(1.0))
        toolbar.addWidget(self.zoom_100_btn)

        toolbar.addWidget(self._toolbar_separator())

        self.zoom_out_btn = QToolButton(self.preview_toolbar)
        self.zoom_out_btn.setObjectName("zoom_square")
        self.zoom_out_btn.setIcon(icons.icon("minus"))
        self.zoom_out_btn.setToolTip("Zoom out")
        self.zoom_out_btn.setAccessibleName("Zoom out")
        self.zoom_out_btn.clicked.connect(
            lambda: self.preview.zoom_to(self.preview.scale_factor() / 1.25)
        )
        toolbar.addWidget(self.zoom_out_btn)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("zoom_label")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        toolbar.addWidget(self.zoom_label)

        self.zoom_in_btn = QToolButton(self.preview_toolbar)
        self.zoom_in_btn.setObjectName("zoom_square")
        self.zoom_in_btn.setIcon(icons.icon("plus"))
        self.zoom_in_btn.setToolTip("Zoom in")
        self.zoom_in_btn.setAccessibleName("Zoom in")
        self.zoom_in_btn.clicked.connect(
            lambda: self.preview.zoom_to(self.preview.scale_factor() * 1.25)
        )
        toolbar.addWidget(self.zoom_in_btn)

        toolbar.addWidget(self._toolbar_separator())

        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setObjectName("compare_btn")
        self.compare_btn.setIcon(icons.icon("compare"))
        self.compare_btn.setCheckable(True)
        self.compare_btn.setToolTip("Wipe between the original and the result")
        self.compare_btn.toggled.connect(self._on_compare_toggled)
        toolbar.addWidget(self.compare_btn)

        self.help_btn = QToolButton(self.preview_toolbar)
        self.help_btn.setObjectName("help_btn")
        self.help_btn.setIcon(icons.icon("help"))
        self.help_btn.setToolTip(
            "Click a region to select it.\n"
            "Drag to move it; drag a corner to resize.\n"
            "Scroll to zoom · Shift+drag to pan.\n"
            "Compare (or right-drag) wipes between the original and the result."
        )
        self.help_btn.setAccessibleName("Preview help")
        self.help_btn.setCursor(Qt.WhatsThisCursor)
        toolbar.addWidget(self.help_btn)

        self.preview.set_overlay_toolbar(self.preview_toolbar)

        self.preview_caption = QLabel("", self.preview)
        self.preview_caption.setObjectName("preview_caption")
        self.preview_caption.hide()
        self.preview.set_caption_label(self.preview_caption)

        self.empty_state = EmptyState(self.preview)
        self.empty_state.add_btn.clicked.connect(self._pick_files)
        self.empty_state.folder_btn.clicked.connect(self._pick_folder)
        self.preview.set_empty_state(self.empty_state)
        return panel

    def _build_right_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("right_panel")
        panel.setMinimumWidth(280)
        panel.setMaximumWidth(460)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(9)

        header_host = QWidget(panel)
        header_host.setObjectName("panel_header")
        header_host.setFixedHeight(28)
        header_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header = QHBoxLayout(header_host)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        # On the right panel the toggle sits on the LEFT, i.e. on the edge
        # closest to the preview (mirrors the images panel on the far left).
        self.right_collapse_btn = QToolButton(header_host)
        self.right_collapse_btn.setObjectName("panel_toggle")
        self.right_collapse_btn.setIcon(icons.icon("chevron_right"))
        self.right_collapse_btn.setIconSize(QSize(14, 14))
        self.right_collapse_btn.setToolTip(
            f"Hide regions panel ({_shortcut_label('Ctrl+B')})"
        )
        self.right_collapse_btn.setAccessibleName("Hide regions panel")
        self.right_collapse_btn.setCursor(Qt.PointingHandCursor)
        header.addWidget(self.right_collapse_btn)
        self.regions_title = QLabel("Detected regions 0")
        self.regions_title.setObjectName("panel_title")
        header.addWidget(self.regions_title)
        header.addStretch(1)
        layout.addWidget(header_host)

        self.uncertain_filter = QCheckBox("Show only regions needing review")
        self.uncertain_filter.setObjectName("uncertain_filter")
        self.uncertain_filter.setToolTip(
            "Hide confident regions; keep only the ones needing review."
        )
        self.uncertain_filter.toggled.connect(self._apply_region_filter)
        layout.addWidget(self.uncertain_filter)

        self.region_list = RegionCardList(panel)
        self.region_list.regionSelected.connect(self._on_region_list_selected)
        self.region_list.enabledChanged.connect(self._on_region_enabled_changed)
        self.region_list.eraseModeChanged.connect(self._on_region_policy_changed)
        self.region_list.paddingChanged.connect(self._on_region_margin_changed)
        layout.addWidget(self.region_list, 1)

        footer = QFrame(panel)
        footer.setObjectName("regions_footer")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(8)
        self.regions_footer_label = QLabel("Nothing to save yet", footer)
        self.regions_footer_label.setObjectName("footer_hint")
        self.regions_footer_label.setWordWrap(True)
        footer_layout.addWidget(self.regions_footer_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_btn = QPushButton("This image", footer)
        self.save_btn.setProperty("sizeClass", "save")
        self.save_btn.setIcon(icons.icon("save"))
        self.save_btn.setToolTip("Save this image (analyzed regions)")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_current)
        buttons.addWidget(self.save_btn, 1)

        self.save_all_btn = QPushButton("Save all", footer)
        self.save_all_btn.setProperty("variant", "success")
        self.save_all_btn.setProperty("sizeClass", "save")
        self.save_all_btn.setIcon(icons.icon("save", ON_SUCCESS))
        self.save_all_btn.setToolTip("Save every analyzed image")
        self.save_all_btn.setEnabled(False)
        self.save_all_btn.clicked.connect(self._save_all)
        buttons.addWidget(self.save_all_btn, 1)
        footer_layout.addLayout(buttons)
        layout.addWidget(footer)
        layout.addItem(
            QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )

        self._right_content = [self.uncertain_filter, self.region_list, footer]
        self.right_collapse_btn.clicked.connect(
            lambda: self._set_panel_collapsed("right", not self._right_collapsed)
        )
        return panel

    @staticmethod
    def _toolbar_separator() -> QFrame:
        line = QFrame()
        line.setObjectName("toolbar_sep")
        line.setFixedSize(1, 18)
        return line

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        bar.setObjectName("app_status")
        bar.setSizeGripEnabled(False)
        bar.setFixedHeight(28)
        self.setStatusBar(bar)
        self.statusBar().setContentsMargins(0, 0, 0, 0)

        left = QWidget(bar)
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(8, 0, 0, 0)
        left_row.setSpacing(6)
        lock = QLabel(left)
        lock.setPixmap(icons.pixmap("lock", 13, MUTED))
        left_row.addWidget(lock)
        self.status_hint = QLabel(
            "Offline · images never leave your device · CPU", left
        )
        self.status_hint.setObjectName("status_hint")
        left_row.addWidget(self.status_hint)
        bar.addWidget(left)

        center = QWidget(bar)
        center_row = QHBoxLayout(center)
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(6)
        center_row.addStretch(1)
        self.output_label = ElidedLabel("Saving to ~/ImageCleaner", center)
        self.output_label.setObjectName("output_label")
        self.output_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.output_label.setMinimumWidth(160)
        self.output_label.setMaximumWidth(560)
        center_row.addWidget(self.output_label)
        self.change_output_btn = QToolButton(center)
        self.change_output_btn.setObjectName("link_btn")
        self.change_output_btn.setText("Change")
        self.change_output_btn.setCursor(Qt.PointingHandCursor)
        self.change_output_btn.setAccessibleName("Change output folder")
        self.change_output_btn.clicked.connect(self._pick_output_dir)
        center_row.addWidget(self.change_output_btn)
        center_row.addStretch(1)
        bar.addWidget(center, 1)

        right = QWidget(bar)
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 12, 0)
        right_row.setSpacing(8)
        self.banner_label = QLabel("", right)
        self.banner_label.setObjectName("banner")
        self.banner_label.setVisible(False)
        right_row.addWidget(self.banner_label)
        self.task_label = QLabel("Ready", right)
        self.task_label.setObjectName("status_state")
        right_row.addWidget(self.task_label)
        self.progress = QProgressBar(right)
        self.progress.setObjectName("status_progress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedWidth(190)
        self.progress.setVisible(False)
        right_row.addWidget(self.progress)
        bar.addPermanentWidget(right)

    def _build_tab_order(self) -> None:
        self.keyword_edit.setFocusProxy(self.keyword_edit._editor)
        chain = [
            self.add_files_btn,
            self.add_folder_btn,
            self.keyword_edit,
            self.advanced_toggle_btn,
            self.analyze_btn,
            self.clear_files_btn,
            self.file_list,
            self.original_btn,
            self.result_btn,
            self.zoom_fit_btn,
            self.zoom_100_btn,
            self.zoom_out_btn,
            self.zoom_in_btn,
            self.compare_btn,
            self.help_btn,
        ]
        for current, following in zip(chain, chain[1:]):
            QWidget.setTabOrder(current, following)

    # ---------------------------------------------------------- side panels

    def _set_panel_collapsed(self, side: str, collapsed: bool) -> None:
        if side == "left":
            panel, content, title = (
                self.left_panel, self._left_content, self.files_title
            )
            toggle = self.left_collapse_btn
            expanded_width, max_width = 184, 340
            outward, inward = "chevron_left", "chevron_right"
            label = "images"
            current = self._left_collapsed
        else:
            panel, content, title = (
                self.right_panel, self._right_content, self.regions_title
            )
            toggle = self.right_collapse_btn
            expanded_width, max_width = 280, 460
            outward, inward = "chevron_right", "chevron_left"
            label = "regions"
            current = self._right_collapsed

        if current == collapsed:
            return

        # Remember the width the user currently gives an expanded panel, so
        # reopening it restores that width. The preview never gets a stored
        # width: it always takes every pixel the side panels leave free.
        sizes = self.body_splitter.sizes()
        if len(sizes) == 3 and not current and not self._restoring:
            if side == "left" and sizes[0] > 48:
                self._left_width = sizes[0]
            elif side == "right" and sizes[2] > 48:
                self._right_width = sizes[2]

        for widget in content:
            widget.setVisible(not collapsed)
        title.setVisible(not collapsed)
        if side == "left":
            self.clear_files_btn.setVisible(not collapsed)
        toggle.setIcon(icons.icon(inward if collapsed else outward))
        toggle.setToolTip(
            f"Show {label} panel ({_shortcut_label('Ctrl+B')})"
            if collapsed
            else f"Hide {label} panel ({_shortcut_label('Ctrl+B')})"
        )
        if collapsed:
            panel.setMinimumWidth(48)
            panel.setMaximumWidth(48)
            # The panel frame + border already eats a few pixels, so keep the
            # rail padding small enough for the 26 px arrow button to render
            # fully instead of being clipped on one side.
            panel.layout().setContentsMargins(8, 8, 8, 8)
        else:
            panel.setMinimumWidth(expanded_width)
            panel.setMaximumWidth(max_width)
            panel.layout().setContentsMargins(12, 10, 12, 10)
        if side == "left":
            self._left_collapsed = collapsed
        else:
            self._right_collapsed = collapsed
        self._apply_panel_sizes()

    def _splitter_total(self) -> int:
        total = self.body_splitter.width() - 2 * self.body_splitter.handleWidth()
        if total <= 0:
            total = sum(self.body_splitter.sizes())
        return max(total, 1)

    def _apply_panel_sizes(self) -> None:
        """Lay out the panes so the preview always fills the leftover width.

        Never rely on absolute sizes captured earlier: after the window is
        resized those become stale and leave an empty strip to the right of
        the side panel (or between the list and the preview).
        """
        total = self._splitter_total()
        left = 48 if self._left_collapsed else self._left_width
        right = 48 if self._right_collapsed else self._right_width
        preview = max(320, total - left - right)
        sizes = [left, preview, right]
        self.body_splitter.setSizes(sizes)
        QTimer.singleShot(
            0, lambda values=list(sizes): self.body_splitter.setSizes(values)
        )

    def _on_splitter_moved(self, *_args) -> None:
        if self._restoring:
            return
        sizes = self.body_splitter.sizes()
        if len(sizes) != 3:
            return
        if not self._left_collapsed and sizes[0] > 48:
            self._left_width = sizes[0]
        if not self._right_collapsed and sizes[2] > 48:
            self._right_width = sizes[2]

    def _toggle_panels(self) -> None:
        expand = self._left_collapsed or self._right_collapsed
        self._set_panel_collapsed("left", not expand)
        self._set_panel_collapsed("right", not expand)

    def _restore_window_state(self) -> None:
        sizes = self._qt_settings.value("ui/panel_sizes", [184, 900, 308])
        if isinstance(sizes, str):
            try:
                sizes = [int(part) for part in sizes.strip("[]").split(",") if part.strip()]
            except ValueError:
                sizes = [184, 900, 308]
        try:
            sizes = [int(value) for value in sizes][:3]
        except (TypeError, ValueError):
            sizes = [184, 900, 308]
        if len(sizes) != 3 or min(sizes) <= 0:
            sizes = [184, 900, 308]
        if 48 < sizes[0] <= 340:
            self._left_width = sizes[0]
        if 48 < sizes[2] <= 460:
            self._right_width = sizes[2]

        left = self._qt_settings.value("ui/left_collapsed", False, type=bool)
        right = self._qt_settings.value("ui/right_collapsed", False, type=bool)

        self._restoring = True

        def restore() -> None:
            try:
                if left:
                    self._set_panel_collapsed("left", True)
                if right:
                    self._set_panel_collapsed("right", True)
                self._apply_panel_sizes()
            finally:
                self._restoring = False

        QTimer.singleShot(0, restore)

    def _save_window_state(self) -> None:
        sizes = self.body_splitter.sizes()
        if len(sizes) == 3:
            if not self._left_collapsed and sizes[0] > 48:
                self._left_width = sizes[0]
            if not self._right_collapsed and sizes[2] > 48:
                self._right_width = sizes[2]
        preview = max(0, self._splitter_total() - self._left_width - self._right_width)
        self._qt_settings.setValue(
            "ui/panel_sizes",
            [int(self._left_width), int(preview), int(self._right_width)],
        )
        self._qt_settings.setValue("ui/left_collapsed", self._left_collapsed)
        self._qt_settings.setValue("ui/right_collapsed", self._right_collapsed)

    # -------------------------------------------------------------- dialogs

    def _toggle_advanced(self, show: bool) -> None:
        if show:
            self.advanced_panel.show()
            self.advanced_panel.raise_()
            self.advanced_panel.activateWindow()
        else:
            self.advanced_panel.hide()

    def _on_advanced_closed(self, _result: int) -> None:
        self.advanced_toggle_btn.blockSignals(True)
        self.advanced_toggle_btn.setChecked(False)
        self.advanced_toggle_btn.blockSignals(False)

    def _toggle_log(self, show: bool) -> None:
        if show:
            self.log_box.show()
            self.log_box.raise_()
            self.log_box.activateWindow()
        else:
            self.log_box.hide()

    def _on_log_closed(self, _result: int) -> None:
        self.log_toggle_btn.blockSignals(True)
        self.log_toggle_btn.setChecked(False)
        self.log_toggle_btn.blockSignals(False)

    def _show_log(self) -> None:
        self.log_toggle_btn.setChecked(True)

    def _set_banner(self, text: str) -> None:
        self.banner_label.setText(text)
        self.banner_label.setVisible(bool(text))
        if text:
            self.task_label.setVisible(False)
        elif not self._busy:
            self.task_label.setVisible(True)

    def _on_inputs_changed(self, *_args) -> None:
        self._set_banner("")
        self._update_action_state()

    def _on_output_changed(self) -> None:
        text = self.output_edit.text().strip() or str(Path.home() / "ImageCleaner")
        self.output_label.setText(f"Saving to {text}")

    def _analyze_label(self) -> str:
        count = len(self._all_entries())
        if count == 0:
            return "Analyze"
        if count == 1:
            return "Analyze 1 image"
        return f"Analyze {count} images"

    def _update_action_state(self) -> None:
        """Validation via disabled states and tooltips, never pop-up dialogs."""
        entries = self._all_entries()
        has_images = bool(entries)
        has_keywords = bool(normalize_keyword_list(self.keyword_edit.toPlainText()))
        current = self._selected_entry()
        current_regions = current.result.regions if current and current.result else []
        current_has_enabled = any(region.enabled for region in current_regions)
        analyzed = [entry for entry in entries if entry.result is not None]
        any_enabled = any(
            region.enabled
            for entry in analyzed
            for region in entry.result.regions
        )

        if not self._busy:
            self.analyze_btn.setText(self._analyze_label())
            self.analyze_btn.setProperty("variant", "primary")
            self.analyze_btn.setIcon(icons.icon("analyze", ON_PRIMARY))
            ready = has_images and has_keywords
            self.analyze_btn.setEnabled(ready)
            self.analyze_btn.setToolTip(
                "" if ready else "Add images and at least one keyword first"
            )
            self.analyze_all_action.setEnabled(ready)
            self.analyze_current_action.setEnabled(
                current is not None and has_keywords
            )
            repolish(self.analyze_btn)

        self.save_btn.setEnabled(not self._busy and current_has_enabled)
        self.save_all_btn.setEnabled(not self._busy and any_enabled)
        self.clear_files_btn.setEnabled(has_images and not self._busy)
        self.save_all_btn.setText(
            f"Save all ({len(analyzed)})" if analyzed else "Save all"
        )
        self._update_regions_footer()
        self._update_files_title()

    def _install_shortcuts(self) -> None:
        """Register window shortcuts in a way that also works on macOS.

        QAction is used instead of QShortcut: on macOS Command-key equivalents
        are delivered through the native menu bar, so the actions are also
        placed in a small "Actions" menu there.  Keeping them in a list stops
        Python from garbage-collecting them.
        """
        specs = (
            ("Add images", ("Ctrl+O",), self._pick_files),
            (
                "Analyze",
                ("Ctrl+Return", "Ctrl+Enter", "Ctrl+R"),
                self._analyze_all,
            ),
            ("Save all", ("Ctrl+S", "Ctrl+Shift+S"), self._save_all),
            ("Toggle panels", ("Ctrl+B",), self._toggle_panels),
            ("Fit to window", ("Ctrl+0",), lambda: self.preview.fit_to_view()),
            ("Actual size", ("Ctrl+1",), lambda: self.preview.zoom_to(1.0)),
        )
        is_mac = sys.platform == "darwin"
        menu = self.menuBar().addMenu("Actions") if is_mac else None
        self._shortcut_actions: list[QAction] = []
        for title, keys, slot in specs:
            action = QAction(title, self)
            sequences: list[QKeySequence] = []
            seen: set[str] = set()
            for key in keys:
                candidates = [key]
                if is_mac and key.startswith("Ctrl+"):
                    # Qt maps Ctrl to Command on macOS.  Also accept the
                    # physical Control key so Windows-style chords work too.
                    candidates.append("Meta+" + key[len("Ctrl+"):])
                for candidate in candidates:
                    sequence = QKeySequence(candidate)
                    portable = sequence.toString(QKeySequence.PortableText)
                    if portable and portable not in seen:
                        seen.add(portable)
                        sequences.append(sequence)
            action.setShortcuts(sequences)
            action.setShortcutContext(Qt.WindowShortcut)
            action.triggered.connect(slot)
            if menu is not None:
                menu.addAction(action)
            else:
                self.addAction(action)
            self._shortcut_actions.append(action)

    # ---------------------------------------------------------------- settings

    def _apply_settings(self) -> None:
        opts = self._settings.options
        self.keyword_edit.clear_ghost_guard()
        self.keyword_edit.setPlainText("\n".join(opts.keywords))
        self.output_edit.clear_ghost_guard()
        self.output_edit.setText(
            self._settings.last_output_dir or str(Path.home() / "ImageCleaner")
        )
        self._set_combo(self.engine_combo, opts.inpaint_engine)
        self._set_combo(self.policy_combo, opts.element_policy)
        self._set_combo(self.match_combo, opts.match_mode)
        self.padding_spin.setValue(int(opts.padding))
        self._on_output_changed()
        self._refresh_presets()
        self._update_action_state()

    @staticmethod
    def _set_combo(combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _collect_options(self) -> ProcessOptions:
        options = copy.copy(self._settings.options)
        options.keywords = normalize_keyword_list(self.keyword_edit.toPlainText())
        options.inpaint_engine = self.engine_combo.currentData()
        options.element_policy = self.policy_combo.currentData()
        options.match_mode = self.match_combo.currentData()
        options.padding = int(self.padding_spin.value())
        return options

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            if QMessageBox.question(self, "Processing", "A task is running. Quit anyway?") \
                    != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.requestInterruption()
            self._worker.wait(3000)
        if self._save_worker is not None and self._save_worker.isRunning():
            self._save_worker.requestInterruption()
            self._save_worker.wait(3000)
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.requestInterruption()
            self._preview_worker.wait(3000)
        self._settings.options = self._collect_options()
        self._settings.last_output_dir = self.output_edit.text().strip()
        try:
            self._settings.save(settings_path())
        except OSError as exc:
            log.warning("could not save settings: %s", exc)
        self._save_window_state()
        self._qt_settings.sync()
        self.advanced_panel.close()
        self.log_box.close()
        super().closeEvent(event)

    # ------------------------------------------------------------------ presets

    def _refresh_presets(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("— Select preset —", None)
        for name, keywords in BUILTIN_PRESETS.items():
            self.preset_combo.addItem(name, keywords)
        try:
            files = sorted(presets_dir().glob("*.json"), key=lambda p: p.name.lower())
        except OSError:
            files = []
        for path in files:
            self.preset_combo.addItem(f"{path.stem}  (mine)", str(path))
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self, index: int) -> None:
        data = self.preset_combo.itemData(index)
        if not data:
            return
        if isinstance(data, str):
            try:
                keywords = load_keyword_preset(Path(data))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Keyword preset error", str(exc))
                return
        else:
            keywords = list(data)
        self.keyword_edit.clear_ghost_guard()
        self.keyword_edit.setPlainText("\n".join(keywords))

    def _save_preset(self) -> None:
        keywords = normalize_keyword_list(self.keyword_edit.toPlainText())
        if not keywords:
            QMessageBox.information(self, "No keywords", "Enter keywords before saving a preset.")
            return
        name, ok = QInputDialog.getText(self, "Save keyword preset", "Preset name:")
        if not ok or not name.strip():
            return
        safe = re.sub(r"[^A-Za-z0-9_.\- ]", "_", name.strip())[:60]
        try:
            save_keyword_preset(presets_dir() / f"{safe}.json", keywords)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save keyword preset", str(exc))
            return
        self._refresh_presets()
        idx = self.preset_combo.findText(f"{safe}  (mine)")
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        log.info("Saved keyword preset '%s' (%d keywords)", safe, len(keywords))

    # -------------------------------------------------------------------- files

    def _pick_files(self) -> None:
        start = self._start_dir()
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose images", start,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;All files (*)",
        )
        if files:
            self._settings.last_input_dir = str(Path(files[0]).parent)
            self._add_paths([Path(f) for f in files])

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose image folder", self._start_dir())
        if not folder:
            return
        self._settings.last_input_dir = folder
        try:
            files = collect_images(folder, recursive=True)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot read folder", str(exc))
            return
        if not files:
            QMessageBox.information(self, "Empty folder", "No supported images found in this folder.")
            return
        self._add_paths(files)

    def _start_dir(self) -> str:
        return self._settings.last_input_dir or str(Path.home())

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_edit.text() or self._start_dir())
        if folder:
            self.output_edit.clear_ghost_guard()
            self.output_edit.setText(folder)
            self._settings.last_output_dir = folder

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt name
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt name
        paths: list[Path] = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            path = Path(local)
            if path.is_dir():
                try:
                    paths.extend(collect_images(path, recursive=True))
                except OSError:
                    continue
            elif path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
                paths.append(path)
        if paths:
            self._add_paths(paths)
            event.acceptProposedAction()

    def _add_paths(self, paths: list[Path]) -> None:
        added = 0
        first_new: QListWidgetItem | None = None
        for path in paths:
            key = str(path.resolve())
            if key in self._entries:
                continue
            entry = ImageEntry(path=path)
            self._entries[key] = entry
            item = QListWidgetItem()
            item.setData(Qt.UserRole, key)
            item.setToolTip(key)
            item.setSizeHint(QSize(0, 58))
            self.file_list.addItem(item)
            row = ImageRow(path)
            self._row_widgets[key] = row
            self.file_list.setItemWidget(item, row)
            self._update_list_item(entry)
            if first_new is None:
                first_new = item
            added += 1
        self._update_files_title()
        if added and self.file_list.currentRow() < 0 and first_new is not None:
            self.file_list.setCurrentItem(first_new)
        self._update_action_state()
        log.info("Added %d images (%d in the list)", added, len(self._entries))

    def _remove_selected_files(self) -> None:
        items = self.file_list.selectedItems()
        if not items:
            return
        for item in items:
            key = item.data(Qt.UserRole)
            self._entries.pop(key, None)
            self._row_widgets.pop(key, None)
            self.file_list.takeItem(self.file_list.row(item))
        self._update_files_title()
        if self.file_list.count() == 0:
            self._show_entry(None)
        self._update_action_state()

    def _clear_all_files(self) -> None:
        if self._busy or self._save_worker is not None:
            return
        if not self._entries:
            return
        self.file_list.clear()
        self._entries.clear()
        self._row_widgets.clear()
        self._show_entry(None)
        self._update_files_title()
        self._update_action_state()
        log.info("Cleared image list")

    def _update_files_title(self) -> None:
        self.files_title.setText(f"Images {len(self._entries)}")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt name
        if (obj is self.file_list and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Delete, Qt.Key_Backspace)):
            self._remove_selected_files()
            return True
        return super().eventFilter(obj, event)

    def _show_file_menu(self, pos) -> None:
        if self.file_list.count() == 0:
            return
        menu = QMenu(self)
        remove = menu.addAction("Remove image from list")
        remove.triggered.connect(self._remove_selected_files)
        menu.exec(self.file_list.mapToGlobal(pos))

    def _selected_entry(self) -> ImageEntry | None:
        item = self.file_list.currentItem()
        if item is None:
            return None
        return self._entries.get(item.data(Qt.UserRole))

    def _all_entries(self) -> list[ImageEntry]:
        out: list[ImageEntry] = []
        for i in range(self.file_list.count()):
            key = self.file_list.item(i).data(Qt.UserRole)
            entry = self._entries.get(key)
            if entry is not None:
                out.append(entry)
        return out

    def _on_file_selected(self, current: QListWidgetItem | None, previous) -> None:
        entry = self._selected_entry()
        self._show_entry(entry)

    # ----------------------------------------------------------------- preview

    def _show_entry(self, entry: ImageEntry | None) -> None:
        if entry is None:
            self._select_original()
            self._loaded = None
            self._current_path = None
            self.preview.set_image(None)
            self.preview.set_regions([])
            self.preview.set_mask_overlay(None)
            self.preview.set_caption("")
            self._populate_region_cards([])
            self._set_zoom_controls_enabled(False)
            self._update_view_buttons()
            return

        if self._current_path != str(entry.path):
            try:
                self._loaded = load_image(entry.path)
                self._current_path = str(entry.path)
            except Exception as exc:  # noqa: BLE001 - unreadable image is a normal case
                self._loaded = None
                self._current_path = str(entry.path)
                log.warning("Could not read %s: %s", entry.path.name, exc)
        self.preview.set_image(self._loaded)
        self._set_zoom_controls_enabled(self._loaded is not None)
        regions = entry.result.regions if entry.result else []
        self.preview.set_regions(regions)
        self._populate_region_cards(regions)
        self._rebuild_mask_overlay()
        self._update_status_label(entry)
        self.uncertain_filter.blockSignals(True)
        self.uncertain_filter.setChecked(False)
        self.uncertain_filter.blockSignals(False)
        self.region_list.set_review_filter(False)
        # Keep the current view mode: switching images must only swap the
        # picture, not flip the Original/Result segment back to Original.
        if not (self._prefer_result and self._auto_show_result(entry)):
            self._select_original()
        self._update_view_buttons()

    def _on_canvas_region_selected(self, region_id: int) -> None:
        self._updating_table = True
        try:
            self.region_list.select_region(region_id)
        finally:
            self._updating_table = False

    def _on_region_list_selected(self, region_id: int) -> None:
        if self._updating_table:
            return
        # Region boxes are only painted on the original image.  Selecting a
        # card while Result/Compare is showing switches back so the box the
        # user just clicked is actually visible.  (-1 means the review filter
        # hid the selected card, not a user click.)
        if region_id >= 0 and not self.original_btn.isChecked():
            self.original_btn.setChecked(True)
        self.preview.set_selected(region_id)

    def _on_region_edited(self, region_id: int, bbox) -> None:
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            return
        for region in entry.result.regions:
            if region.id == region_id:
                region.bbox = tuple(int(v) for v in bbox)
                region.mask = None
                log.info("Adjusted region #%d: %s", region_id, region.bbox)
                break
        self._invalidate_result_preview()
        self._rebuild_mask_overlay()
        self._update_status_label(entry)

    def _on_padding_changed(self, value: int) -> None:
        entry = self._selected_entry()
        if entry is not None and entry.result is not None:
            for region in entry.result.regions:
                region.mask = None
        self._invalidate_result_preview()
        self._rebuild_mask_overlay()

    def _on_show_mask(self, show: bool) -> None:
        self.preview.set_show_mask(show)

    # ------------------------------------------------------------- region table

    def _populate_region_cards(self, regions: list[Region]) -> None:
        self._updating_table = True
        try:
            self.region_list.set_busy(self._busy)
            self.region_list.set_regions(regions)
        finally:
            self._updating_table = False
        selected = self.preview.selected_region_id()
        if selected < 0 and regions:
            selected = regions[0].id
            self.preview.set_selected(selected)
        if any(region.id == selected for region in regions):
            self.region_list.select_region(selected)
        self._set_regions_title(regions)
        self._apply_region_filter()
        self._update_regions_footer()

    # Backwards-compatible name for older callers.
    _populate_regions_table = _populate_region_cards

    def _set_regions_title(self, regions: list[Region]) -> None:
        self.regions_title.setText(f"Detected regions {len(regions)}")

    def _apply_region_filter(self, *_checked) -> None:
        if not hasattr(self, "uncertain_filter"):
            return
        self.region_list.set_review_filter(self.uncertain_filter.isChecked())

    def _update_regions_footer(self) -> None:
        if not hasattr(self, "regions_footer_label"):
            return
        entry = self._selected_entry()
        regions = entry.result.regions if entry and entry.result else []
        enabled = sum(1 for region in regions if region.enabled)
        total = len(regions)
        if total == 0:
            self.regions_footer_label.setText("Nothing to save yet")
        else:
            noun = "region will" if total == 1 else "regions will"
            self.regions_footer_label.setText(
                f"{enabled} of {total} {noun} be erased"
            )

    def _on_region_enabled_changed(self, region_id: int, enabled: bool) -> None:
        if self._updating_table:
            return
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            return
        region = next(
            (item for item in entry.result.regions if item.id == region_id), None
        )
        if region is None:
            return

        if enabled and region.kind is RegionKind.UNCERTAIN:
            # Explicit user approval. The region is re-classified so the mask
            # matches what the box actually covers.
            region.kind = (
                RegionKind.ELEMENT if self._looks_like_element(region)
                else RegionKind.TEXT
            )
            log.info("User approved region #%d → %s", region.id, region.kind.value)
        region.enabled = enabled
        region.mask = None
        self._invalidate_result_preview()
        self._rebuild_mask_overlay()
        self.preview.set_regions(entry.result.regions)
        self.region_list.refresh_region(region)
        self._set_regions_title(entry.result.regions)
        self._update_list_item(entry)
        self._update_status_label(entry)
        self._update_action_state()

    def _on_region_margin_changed(self, region_id: int, value: int) -> None:
        if self._updating_table:
            return
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            return
        region = next((r for r in entry.result.regions if r.id == region_id), None)
        if region is None:
            return
        value = int(value)
        if int(getattr(region, "margin_px", 0) or 0) == value:
            return
        region.margin_px = value
        region.mask = None
        log.info("Region #%d: padding %+d px", region_id, value)
        self._invalidate_result_preview()
        self._rebuild_mask_overlay()
        self.preview.set_regions(entry.result.regions)
        self.region_list.refresh_region(region)
        self._update_status_label(entry)

    def _on_region_policy_changed(self, region_id: int, mode: str | None) -> None:
        if self._updating_table:
            return
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            return
        region = next((r for r in entry.result.regions if r.id == region_id), None)
        if region is None:
            return
        mode = mode or None
        if getattr(region, "mask_mode", None) == mode:
            return
        region.mask_mode = mode
        region.mask = None
        log.info("Region #%d: erase mode = %s", region_id, mode or "auto")
        self._invalidate_result_preview()
        self._rebuild_mask_overlay()
        self.preview.set_regions(entry.result.regions)
        self.region_list.refresh_region(region)
        self._update_status_label(entry)

    @staticmethod
    def _looks_like_element(region: Region) -> bool:
        return (
            float(region.evidence.get("element_conf", 0.0)) >= 0.45
            or region.source in {"color", "contour", "flood", "snap"}
        )

    def _set_all_enabled(self, enabled: bool) -> None:
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            return
        for region in entry.result.regions:
            if region.kind is RegionKind.UNCERTAIN:
                continue  # never bulk-approve an uncertain region
            region.enabled = enabled
            region.mask = None
        self._invalidate_result_preview()
        self._rebuild_mask_overlay()
        self.preview.set_regions(entry.result.regions)
        self._populate_regions_table(entry.result.regions)
        self._update_status_label(entry)
        self._update_action_state()

    # ------------------------------------------------------------------- masks

    def _rebuild_mask_overlay(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.result is None or self._loaded is None:
            self.preview.set_mask_overlay(None)
            return
        h, w = self._loaded.bgr.shape[:2]
        rgba = np.zeros((h, w, 4), np.uint8)
        options = self._collect_options()
        for region in entry.result.regions:
            if (getattr(options, "refine_mask", True)
                    and region_is_element(region, options)):
                refined = refine_element_mask(
                    self._loaded.bgr, region, entry.result.lines, options
                )
                mask = refined.mask.astype(np.uint8) * 255
            else:
                mask = build_region_mask(self._loaded.bgr, region, options)
            if mask.max() == 0:
                continue
            color = MASK_RGB.get(region.kind, (192, 132, 252))
            sel = mask > 0
            rgba[sel, 0] = color[0]
            rgba[sel, 1] = color[1]
            rgba[sel, 2] = color[2]
            rgba[sel, 3] = 190 if region.enabled else 110
        self.preview.set_mask_overlay(rgba)

    # ---------------------------------------------------------------- analyzing

    def _on_primary_clicked(self) -> None:
        if self._busy:
            self._cancel_worker()
        else:
            self._analyze_all()

    def _analyze_all(self) -> None:
        if self._busy:
            return
        self._analyze_entries(self._all_entries())

    def _analyze_current(self) -> None:
        if self._busy:
            return
        entry = self._selected_entry()
        if entry is None:
            self._set_banner("Select an image first.")
            return
        self._analyze_entries([entry], select_first=False)

    def _analyze_entries(
        self, entries: list[ImageEntry], *, select_first: bool = True
    ) -> None:
        if not entries:
            QMessageBox.information(
                self, "No images", "Add images or a folder before analyzing."
            )
            return
        options = self._collect_options()
        if not options.keywords:
            QMessageBox.information(self, "No keywords", "Enter at least one keyword.")
            self.keyword_edit.setFocus()
            return

        paths = [entry.path for entry in entries]

        def job(progress, cancelled):
            pipeline = Pipeline(options)
            results: list[tuple[str, AnalysisResult | None, str]] = []
            for i, path in enumerate(paths):
                if cancelled():
                    break
                progress(i, len(paths), Path(path).name)
                try:
                    results.append((str(path), pipeline.analyze(path), ""))
                except Exception as exc:  # noqa: BLE001 - report per image
                    log.exception("analyze failed: %s", path)
                    results.append((str(path), None, f"{type(exc).__name__}: {exc}"))
            return results

        self._start_worker(
            job,
            on_success=lambda results: self._on_analyze_done(
                results, select_first=select_first
            ),
            label="Analyzing",
            busy_total=len(paths),
        )

    def _on_analyze_done(self, results, *, select_first: bool = True) -> None:
        analyzed: dict[str, AnalysisResult] = {}
        failures: list[str] = []
        for path, result, error in results:
            entry = self._entries.get(str(path))
            if entry is None:
                continue
            if result is None:
                entry.status = "failed"
                entry.error = error
                failures.append(f"{entry.path.name}: {error}")
                self._update_list_item(entry)
                continue
            entry.result = result
            entry.error = ""
            entry.status = "no_match" if not result.regions else "analyzed"
            analyzed[str(path)] = result
            self._update_list_item(entry)

        self._update_summary()
        self._prefer_result = True
        if self.file_list.count():
            if select_first and self.file_list.currentRow() != 0:
                self.file_list.setCurrentRow(0)
            else:
                # "Analyze this image only" must keep the current selection.
                self._show_entry(self._selected_entry())
            self._auto_show_result(self._selected_entry())
        if failures:
            QMessageBox.warning(
                self, "Some images failed",
                "Could not analyze:\n\n" + "\n".join(failures[:8]),
            )
        total_regions = sum(len(r.regions) for r in analyzed.values())
        log.info("Analyzed %d/%d images, %d regions", len(analyzed), len(results), total_regions)
        self.task_label.setText(
            f"Analyzed {len(analyzed)} "
            f"{'image' if len(analyzed) == 1 else 'images'} · "
            f"{total_regions} {'region' if total_regions == 1 else 'regions'}"
        )
        self.task_label.setVisible(True)
        self._set_banner("")
        self._update_action_state()

    def _entry_status_text(self, entry: ImageEntry) -> tuple[str, str]:
        if entry.status == "failed":
            return "Error", "error"
        if entry.status == "success":
            return "Saved", "ok"
        if entry.status == "skipped":
            return "Skipped · review", "warn"
        if entry.result is None:
            return "Not analyzed", "muted"
        regions = entry.result.regions
        if not regions:
            return "No match", "muted"
        uncertain = sum(
            1 for region in regions if region.kind is RegionKind.UNCERTAIN
        )
        if uncertain:
            return f"{len(regions)} · review", "warn"
        count = len(regions)
        return f"{count} region" + ("" if count == 1 else "s"), "ok"

    def _update_list_item(self, entry: ImageEntry) -> None:
        key = str(entry.path.resolve())
        row = self._row_widgets.get(key)
        if row is None:
            return
        text, state = self._entry_status_text(entry)
        row.set_status(text, state)
        row.set_error(entry.error)
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.data(Qt.UserRole) == key:
                item.setToolTip(
                    f"{entry.path}\n{text}" + (f"\n{entry.error}" if entry.error else "")
                )
                break

    def _update_summary(self) -> None:
        entries = self._all_entries()
        analyzed = [e for e in entries if e.result is not None]
        regions = sum(len(e.result.regions) for e in analyzed if e.result)
        uncertain = sum(
            1 for e in analyzed if e.result for r in e.result.regions
            if r.kind is RegionKind.UNCERTAIN
        )
        self.summary_label.setText(
            f"{len(entries)} images · {len(analyzed)} analyzed · {regions} regions"
            + (f" · {uncertain} need review" if uncertain else "")
        )
        self._update_files_title()

    # ------------------------------------------------------------------ preview

    def _select_original(self) -> None:
        for button in (self.original_btn, self.result_btn):
            button.blockSignals(True)
        self.original_btn.setChecked(True)
        self.result_btn.setChecked(False)
        self.result_btn.setText("Result")
        for button in (self.original_btn, self.result_btn):
            button.blockSignals(False)
        self._sync_segment_icons()
        self._compare_after_render = False
        self._set_compare_checked(False)
        self.preview.set_result_image(None)
        self.preview.set_show_region_boxes(True)

    def _update_view_buttons(self) -> None:
        """Result/Compare only make sense once the selected image has results."""
        entry = self._selected_entry()
        available = (
            not self._busy
            and entry is not None
            and entry.result is not None
            and self._loaded is not None
        )
        self.result_btn.setEnabled(available)
        self.compare_btn.setEnabled(available)
        if not available:
            self._set_compare_checked(False)

    def _set_zoom_controls_enabled(self, enabled: bool) -> None:
        for button in (self.zoom_fit_btn, self.zoom_out_btn,
                       self.zoom_in_btn, self.zoom_100_btn):
            button.setEnabled(enabled)

    def _sync_segment_icons(self) -> None:
        active = ON_PRIMARY
        self.original_btn.setIcon(icons.icon(
            "original", active if self.original_btn.isChecked() else MUTED
        ))
        self.result_btn.setIcon(icons.icon(
            "result", active if self.result_btn.isChecked() else MUTED
        ))

    def _set_compare_checked(self, checked: bool) -> None:
        if hasattr(self, "compare_btn"):
            self.compare_btn.blockSignals(True)
            self.compare_btn.setChecked(bool(checked))
            self.compare_btn.blockSignals(False)
        if hasattr(self, "preview"):
            self.preview.set_compare(bool(checked))

    def _on_compare_toggled(self, checked: bool) -> None:
        if not checked:
            self._compare_after_render = False
            self.preview.set_compare(False)
            return
        entry = self._selected_entry()
        if entry is None or entry.result is None or self._loaded is None:
            self._set_compare_checked(False)
            return
        if self.preview._result_pixmap is not None:
            self.preview.set_compare(True)
            return
        # Build the result first, then turn the wipe on.
        self._compare_after_render = True
        if not self.result_btn.isChecked():
            self.result_btn.setChecked(True)
        else:
            self._on_view_mode(True)

    def _auto_show_result(self, entry: ImageEntry | None) -> bool:
        """Default to the rendered result when an image has removable regions.

        Returns True when the result view was requested, so the caller knows
        not to force the Original view afterwards.
        """
        if entry is None or entry.result is None:
            return False
        if entry.status not in ("analyzed", "no_match", "success"):
            return False
        if not any(r.enabled for r in entry.result.regions):
            return False
        if self._busy:
            self._show_result_on_idle = True
            return True
        if not self.result_btn.isChecked():
            self.result_btn.setChecked(True)
        else:
            # Already showing Result: re-render for the newly selected image
            # without toggling Original on and off (that caused a flash).
            self._on_view_mode(True)
        return True

    # Backwards-compatible name used elsewhere in the class.
    _reset_preview_button = _select_original

    def _invalidate_result_preview(self) -> None:
        if self.result_btn.isChecked() or self.compare_btn.isChecked():
            self._select_original()
            self._set_banner("Region changed — click “Result” to refresh.")

    def _on_view_mode(self, checked: bool) -> None:
        if not checked:
            return
        self._sync_segment_icons()
        if self.original_btn.isChecked():
            self._prefer_result = False
            self._select_original()
            self._set_banner("")
            return
        self._prefer_result = True
        if self._busy:
            self._select_original()
            return
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            self._select_original()
            self._set_banner("Click “Analyze” before viewing results.")
            return
        regions = [r for r in entry.result.regions if r.enabled]
        if not regions:
            self._select_original()
            self._set_banner(
                "Tick at least one region in the list before viewing the result."
            )
            return

        options = self._collect_options()
        result = _snapshot(entry.result)
        enabled_ids = {r.id for r in regions}
        regions_snapshot = [r for r in result.regions if r.id in enabled_ids]
        path = entry.path

        def job(progress, cancelled):
            if cancelled():
                return None
            pipeline = Pipeline(options)
            loaded = load_image(path)
            out, _mask, engine = pipeline.render(loaded, result, regions_snapshot)
            if cancelled():
                return None
            return str(path), out, engine

        self._set_banner("")
        # Preview is lightweight: do not put the whole window into the global
        # busy state (that made every list click flash buttons/progress).
        self._start_worker(
            job, on_success=self._on_preview_rendered,
            label="Building preview", quiet=True,
        )

    def _on_preview_rendered(self, payload) -> None:
        if payload is None:
            self._compare_after_render = False
            return
        path, out, _engine = payload
        if self._current_path != path or not self.result_btn.isChecked():
            return  # selection/view changed while the preview was rendering
        self.preview.set_result_image(out)
        self.preview.set_show_region_boxes(False)
        if self._compare_after_render:
            self._compare_after_render = False
            self._set_compare_checked(True)
        elif self.compare_btn.isChecked():
            self.preview.set_compare(True)

    # -------------------------------------------------------------------- saving

    def _save_current(self) -> None:
        if self._busy or self._save_worker is not None:
            return
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "No image selected", "Select an image from the list.")
            return
        if entry.result is None:
            QMessageBox.information(self, "Not analyzed", "Click “Analyze” before saving.")
            return
        self._save_entries([entry], self.save_btn)

    def _save_all(self) -> None:
        if self._busy or self._save_worker is not None:
            return
        entries = self._all_entries()
        if not entries:
            QMessageBox.information(self, "No images", "Add images before saving.")
            return
        self._save_entries(entries, self.save_all_btn)

    def _save_entries(self, entries: list[ImageEntry], button: QPushButton) -> None:
        if self._busy or self._save_worker is not None:
            return
        options = self._collect_options()
        if not options.keywords:
            QMessageBox.information(self, "No keywords", "Enter at least one keyword.")
            return
        out_dir = Path(self.output_edit.text().strip() or (Path.home() / "ImageCleaner"))
        jobs: list[tuple[Path, AnalysisResult | None]] = [
            (entry.path, _snapshot(entry.result) if entry.result else None) for entry in entries
        ]
        pending = [path.name for path, res in jobs if res is None]
        if pending:
            if QMessageBox.question(
                self, "Images not analyzed",
                f"{len(pending)} images have not been analyzed. They will be analyzed and saved. Continue?",
            ) != QMessageBox.Yes:
                return

        def job(progress, cancelled):
            pipeline = Pipeline(options)
            reports: list[ProcessReport] = []
            analyzed: dict[str, AnalysisResult] = {}
            started = time.perf_counter()
            for i, (path, result) in enumerate(jobs):
                if cancelled():
                    break
                progress(i, len(jobs), path.name)
                try:
                    loaded = load_image(path)
                    if result is None:
                        result = pipeline.analyze(path, loaded)
                        analyzed[str(path)] = result
                    enabled = [r for r in result.regions if r.enabled]
                    reports.append(
                        pipeline.write_result(loaded, result, out_dir, regions=enabled)
                    )
                except Exception as exc:  # noqa: BLE001 - keep batch going
                    log.exception("save failed: %s", path)
                    reports.append(ProcessReport(
                        str(path), ImageStatus.FAILED,
                        error=f"{type(exc).__name__}: {exc}",
                    ))
            elapsed = time.perf_counter() - started
            return SaveOutcome(reports=reports, analyzed=analyzed, elapsed_s=elapsed)

        self._set_save_loading(button)
        self._start_save_worker(job, on_success=self._on_save_done)

    def _on_save_done(self, outcome: SaveOutcome) -> None:
        for key, result in outcome.analyzed.items():
            entry = self._entries.get(key)
            if entry is not None and entry.result is None:
                entry.result = result
                entry.status = "analyzed" if result.regions else "no_match"
                self._update_list_item(entry)

        status_map = {
            ImageStatus.SUCCESS: "success",
            ImageStatus.NOT_FOUND: "no_match",
            ImageStatus.SKIPPED_UNCERTAIN: "skipped",
            ImageStatus.FAILED: "failed",
        }
        for report in outcome.reports:
            entry = self._entries.get(report.image_path)
            if entry is None:
                continue
            entry.status = status_map.get(report.status, entry.status)
            entry.error = report.error or ""
            self._update_list_item(entry)

        counts = summarize(outcome.reports)
        lines = [
            f"Succeeded: {counts['success']}",
            f"Not found: {counts['not_found']}",
            f"Skipped (needs review): {counts['skipped_uncertain']}",
            f"Failed: {counts['failed']}",
            "",
            f"Output folder: {self.output_edit.text().strip()}",
            f"Time: {outcome.elapsed_s:.1f} s",
        ]
        self._update_summary()
        self.task_label.setText(
            f"Saved {counts['success']}/{counts['total']} images ({outcome.elapsed_s:.1f}s)"
        )
        self._set_banner(" · ".join(lines[:4]))
        self._update_action_state()

    # ------------------------------------------------------------------- worker

    def _set_save_loading(self, button: QPushButton) -> None:
        self._active_save_button = button
        self._save_button_state = (
            button.text(), button.icon(), button.toolTip(), button.isEnabled()
        )
        color = ON_SUCCESS if button is self.save_all_btn else MUTED
        button.setIcon(icons.spinning_icon(button, color))
        button.setToolTip("Saving…")
        self.save_btn.setEnabled(False)
        self.save_all_btn.setEnabled(False)
        self.clear_files_btn.setEnabled(False)

    def _restore_save_button(self) -> None:
        button = self._active_save_button
        state = self._save_button_state
        if button is None or state is None:
            return
        text, icon, tooltip, enabled = state
        button.setText(text)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setEnabled(enabled)
        setattr(button, "_loading_spin", None)
        self._active_save_button = None
        self._save_button_state = None

    def _start_save_worker(self, job, on_success) -> None:
        """Save in the background without switching the whole window to busy."""
        if self._save_worker is not None and self._save_worker.isRunning():
            return
        self._busy = True
        worker = FunctionWorker(job, self)
        worker.succeeded.connect(
            lambda result: self._on_save_succeeded(result, on_success)
        )
        worker.failed.connect(self._on_save_failed)
        worker.finished.connect(lambda w=worker: self._on_save_finished(w))
        self._save_worker = worker
        worker.start()

    def _on_save_succeeded(self, result, on_success) -> None:
        self._busy = False
        self._restore_save_button()
        on_success(result)

    def _on_save_failed(self, traceback_text: str) -> None:
        self._busy = False
        self._restore_save_button()
        self._update_action_state()
        self._on_worker_failed(traceback_text)

    def _on_save_finished(self, worker) -> None:
        self._restore_save_button()
        if self._save_worker is worker:
            self._save_worker = None
        worker.deleteLater()

    def _start_worker(self, job, on_success, label: str, busy_total: int = 0,
                      quiet: bool = False) -> None:
        if quiet:
            self._start_preview_worker(job, on_success)
            return
        self._stop_preview_worker()
        self._set_busy(True, label)
        self.progress.setRange(0, max(1, busy_total))
        self.progress.setValue(0)
        worker = FunctionWorker(job, self)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(on_success)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.setFormat(f"{done}/{total}  {name}")

    def _on_worker_failed(self, traceback_text: str) -> None:
        log.error("Task failed:\n%s", traceback_text)
        self._show_log()
        QMessageBox.critical(self, "Task failed", traceback_text.splitlines()[-1])
        self.task_label.setText("Task failed")
        self.task_label.setVisible(True)

    def _start_preview_worker(self, job, on_success) -> None:
        """Render a preview without touching the global busy/progress UI."""
        old = self._preview_worker
        if old is not None and old.isRunning():
            old.requestInterruption()
        worker = FunctionWorker(job, self)
        worker.succeeded.connect(on_success)
        worker.failed.connect(self._on_preview_failed)
        worker.finished.connect(lambda w=worker: self._on_preview_finished(w))
        self._preview_worker = worker
        worker.start()

    def _stop_preview_worker(self) -> None:
        worker = self._preview_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()

    def _on_preview_finished(self, worker) -> None:
        if self._preview_worker is worker:
            self._preview_worker = None
        worker.deleteLater()

    def _on_preview_failed(self, traceback_text: str) -> None:
        log.error("Preview failed:\n%s", traceback_text)
        self._compare_after_render = False
        self._set_compare_checked(False)
        if self.result_btn.isChecked():
            self._select_original()
            self._set_banner("Could not build the preview result.")

    def _on_worker_finished(self) -> None:
        self._set_busy(False)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self.progress.setFormat("%v/%m")
        self.progress.setValue(0)
        if self._show_result_on_idle:
            self._show_result_on_idle = False
            entry = self._selected_entry()
            if entry is not None and entry.result is not None:
                self._auto_show_result(entry)

    def _cancel_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self.progress.setFormat("Cancelling…")

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        if busy:
            self.analyze_btn.setText("Cancel")
            self.analyze_btn.setProperty("variant", "danger")
            self.analyze_btn.setIcon(icons.icon("cancel", RED))
            self.analyze_btn.setToolTip("Cancel the running task")
            self.analyze_btn.setEnabled(True)
            self.analyze_all_action.setEnabled(False)
            self.analyze_current_action.setEnabled(False)
            repolish(self.analyze_btn)
            for button in (self.save_btn, self.save_all_btn, self.add_files_btn,
                           self.add_folder_btn, self.advanced_toggle_btn):
                button.setEnabled(False)
            self.region_list.set_busy(True)
            self.task_label.setVisible(False)
            self.banner_label.setVisible(False)
            self.progress.setVisible(True)
            if label:
                self.progress.setFormat(f"{label}…")
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            self.analyze_btn.setText(self._analyze_label())
            self.analyze_btn.setProperty("variant", "primary")
            self.analyze_btn.setIcon(icons.icon("analyze", ON_PRIMARY))
            self.analyze_btn.setToolTip("")
            repolish(self.analyze_btn)
            for button in (self.add_files_btn, self.add_folder_btn,
                           self.advanced_toggle_btn):
                button.setEnabled(True)
            self.region_list.set_busy(False)
            self.progress.setVisible(False)
            self.progress.setValue(0)
            self.progress.setFormat("%v/%m")
            self.task_label.setVisible(not self.banner_label.isVisible())
            QApplication.restoreOverrideCursor()
            self._update_view_buttons()
        self._update_action_state()

    # ------------------------------------------------------------------ helpers

    def _update_status_label(self, entry: ImageEntry) -> None:
        if entry is None:
            self.preview.set_caption("")
            return
        if entry.result is not None:
            width, height = entry.result.width, entry.result.height
        elif self._loaded is not None:
            width, height = self._loaded.width, self._loaded.height
        else:
            self.preview.set_caption("")
            return
        self.preview.set_caption(
            f"{entry.path.name} · {width} × {height}"
        )

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

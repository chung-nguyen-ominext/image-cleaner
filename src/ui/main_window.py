"""Main window: keyword input, batch queue, preview, region review, save."""

from __future__ import annotations

import copy
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QSpacerItem, QStyle, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from src.config.paths import presets_dir, settings_path
from src.config.settings import (
    AppSettings, ProcessOptions, load_keyword_preset, normalize_keyword_list,
    save_keyword_preset,
)
from src.core.batch_processor import collect_images, summarize, write_reports
from src.core.mask_generator import build_region_mask, region_is_element
from src.core.mask_refinement import refine_element_mask
from src.core.models import AnalysisResult, ImageStatus, ProcessReport, Region, RegionKind
from src.core.pipeline import Pipeline
from src.ui import icons
from src.ui.controls import (
    ArrowComboBox, ArrowSpinBox, DotsSplitter, HintLabel, HintLineEdit,
    HintPlainTextEdit,
)
from src.ui.preview import COLORS, PreviewCanvas
from src.ui.theme import MUTED, RED, dim_placeholder
from src.ui.workers import FunctionWorker
from src.utils.image_utils import LoadedImage, SUPPORTED_EXTS, load_image

log = logging.getLogger("imagecleaner.ui")

BUILTIN_PRESETS: dict[str, list[str]] = {
    "VPN / network": ["VPN", "Proxy", "WireGuard", "OpenVPN"],
    "Security labels": ["Confidential", "Internal Use Only", "Do Not Share", "Restricted"],
    "Account": ["Username", "Password", "Email", "API key"],
}

REGION_LABELS = {
    RegionKind.ELEMENT: "Element",
    RegionKind.TEXT: "Text",
    RegionKind.UNCERTAIN: "Needs review",
}

REGION_COLORS = {
    RegionKind.ELEMENT: QColor("#3ecf8e"),
    RegionKind.TEXT: QColor("#f0a03c"),
    RegionKind.UNCERTAIN: QColor("#f0616d"),
}

MASK_RGB = {
    RegionKind.ELEMENT: (62, 207, 142),
    RegionKind.TEXT: (240, 160, 60),
    RegionKind.UNCERTAIN: (240, 97, 109),
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
        self.resize(1380, 900)
        self.setAcceptDrops(True)

        self._entries: dict[str, ImageEntry] = {}
        self._loaded: LoadedImage | None = None
        self._current_path: str | None = None
        self._worker: FunctionWorker | None = None
        self._preview_worker: FunctionWorker | None = None
        self._busy = False
        self._updating_table = False
        self._prereq_hint = ""
        self._show_result_on_idle = False
        self._settings = AppSettings.load(settings_path())

        self._log_handler = QtLogHandler()
        logger = logging.getLogger("imagecleaner")
        logger.setLevel(logging.INFO)
        logger.addHandler(self._log_handler)
        self._log_handler.message.connect(self._append_log)

        self._build_ui()
        self._set_zoom_controls_enabled(False)
        self._update_view_buttons()
        for button in self.findChildren(QPushButton):
            button.setMinimumHeight(max(32, button.minimumHeight()))
            if not button.icon().isNull():
                button.setIconSize(QSize(16, 16))
        self._apply_settings()
        self._sync_segment_icons()
        self._install_shortcuts()

    # --------------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(5)

        root.addWidget(self._build_input_group())
        root.addWidget(self._build_advanced_panel())

        mid = DotsSplitter(Qt.Horizontal)
        mid.addWidget(self._build_file_panel())
        self.work_panel = self._build_work_panel()
        mid.addWidget(self.work_panel)
        mid.setStretchFactor(0, 0)
        mid.setStretchFactor(1, 1)
        root.addWidget(mid, 1)

        log_box = self._build_log_group()
        root.addWidget(self._build_action_bar())
        root.addWidget(log_box)

        self.statusBar().showMessage("Offline · images never leave your device · CPU")
        self.statusBar().setContentsMargins(8, 0, 8, 0)
        self._update_action_state()

    def _build_input_group(self) -> QWidget:
        """Two rows only: pick images, then type keywords."""
        box = QGroupBox("1 · Select images    2 · Enter keywords")
        grid = QGridLayout(box)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)

        self.add_files_btn = QPushButton("Choose images…")
        self.add_files_btn.setIcon(icons.icon("images"))
        self.add_folder_btn = QPushButton("Image folder…")
        self.add_folder_btn.setIcon(icons.icon("folder"))
        self.add_folder_btn.setToolTip(
            "Choose the input image folder (originals are kept)."
        )
        self.add_files_btn.clicked.connect(self._pick_files)
        self.add_folder_btn.clicked.connect(self._pick_folder)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setIcon(icons.icon("remove"))
        self.remove_btn.clicked.connect(self._remove_selected_files)
        self.remove_btn.setVisible(False)  # each thumbnail is removed via Del

        self.advanced_toggle_btn = QPushButton("Advanced options")
        self.advanced_toggle_btn.setIcon(icons.icon("settings"))
        self.advanced_toggle_btn.setObjectName("advanced_toggle_btn")
        self.advanced_toggle_btn.setCheckable(True)
        self.advanced_toggle_btn.setChecked(False)
        self.advanced_toggle_btn.toggled.connect(self._toggle_advanced)

        self.output_label = QLabel()
        self.output_label.setStyleSheet("color:#9aa1b1;")
        self.output_label.setMinimumWidth(180)

        self.input_toggle_btn = QToolButton()
        self.input_toggle_btn.setObjectName("input_toggle_btn")
        self.input_toggle_btn.setAutoRaise(True)
        self.input_toggle_btn.setCheckable(True)
        self.input_toggle_btn.setChecked(True)
        self.input_toggle_btn.setFixedSize(24, 24)
        self.input_toggle_btn.setIconSize(QSize(14, 14))
        self.input_toggle_btn.setIcon(icons.icon("chevron_up"))
        self.input_toggle_btn.setToolTip("Collapse section")
        self.input_toggle_btn.setCursor(Qt.PointingHandCursor)

        self.images_label = QLabel("Images")
        grid.addWidget(self.images_label, 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self.add_files_btn, 0, 1)
        grid.addWidget(self.add_folder_btn, 0, 2)
        grid.addWidget(self.output_label, 0, 3)
        grid.setColumnStretch(4, 1)
        grid.addWidget(self.advanced_toggle_btn, 0, 5)
        grid.addWidget(self.input_toggle_btn, 0, 6, Qt.AlignRight | Qt.AlignVCenter)

        self.keywords_hint = HintLabel(
            "Keywords",
            "One keyword per line, or comma-separated. "
            "Case-insensitive.",
        )
        grid.addWidget(self.keywords_hint, 1, 0, Qt.AlignRight | Qt.AlignTop)
        self.keyword_edit = HintPlainTextEdit()
        self.keyword_edit.setPlaceholderText(
            "Example: VPN, Internal Use Only, Confidential"
        )
        dim_placeholder(self.keyword_edit)
        self.keyword_edit.setFixedHeight(52)
        self.keyword_edit.textChanged.connect(self._on_inputs_changed)
        grid.addWidget(self.keyword_edit, 1, 1, 1, 5)

        self._input_collapsible = [
            self.images_label,
            self.add_files_btn,
            self.add_folder_btn,
            self.output_label,
            self.advanced_toggle_btn,
            self.keywords_hint,
            self.keyword_edit,
        ]
        self.input_toggle_btn.toggled.connect(self._toggle_input_group)
        return box

    def _build_advanced_panel(self) -> QWidget:
        """Everything rarely used lives here, collapsed by default."""
        box = QGroupBox("Advanced options")
        self.advanced_panel = box
        box.setObjectName("advanced_panel")
        box.setVisible(False)
        outer = QVBoxLayout(box)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        def field_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setMinimumWidth(96)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return label

        def field_hint(text: str, hint: str) -> HintLabel:
            label = HintLabel(text, hint)
            label.setMinimumWidth(96)
            return label

        def group_row() -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(8)
            return row

        # Row 0: the output folder stays on one line and takes all spare width.
        row = group_row()
        row.addWidget(field_label("Output folder"))
        self.output_edit = HintLineEdit()
        self.output_edit.setPlaceholderText("Default: ~/ImageCleaner")
        dim_placeholder(self.output_edit)
        self.output_edit.textChanged.connect(self._on_output_changed)
        row.addWidget(self.output_edit, 1)
        self.output_browse_btn = QPushButton("Browse…")
        self.output_browse_btn.setIcon(icons.icon("folder"))
        self.output_browse_btn.clicked.connect(self._pick_output_dir)
        row.addWidget(self.output_browse_btn)
        outer.addLayout(row)

        # Rows 1-2 share one grid so the label columns and the two trailing
        # dropdowns (Engine / Matching) have the same width and left edge.
        # The two leading labels use the same 96 px column as "Output folder"
        # and "Padding", so their dropdowns start at the same left edge.
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)

        preset_label = field_label("Keyword preset")
        preset_label.setFixedWidth(96)
        # The global QLabel padding leaves exactly 88 px for text; "Keyword
        # preset" needs that much and can clip the leading K on some fonts.
        preset_label.setStyleSheet("padding-left: 1px; padding-right: 1px;")
        grid.addWidget(preset_label, 0, 0)
        self.preset_combo = ArrowComboBox()
        self.preset_combo.setMaximumWidth(420)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        grid.addWidget(self.preset_combo, 0, 1)
        # Absorbs the space freed by the narrower label column so the fields
        # on the right keep their existing positions.
        grid.addItem(QSpacerItem(2, 0, QSizePolicy.Fixed, QSizePolicy.Minimum), 0, 2)
        self.save_preset_btn = QPushButton("Save preset…")
        self.save_preset_btn.setIcon(icons.icon("save"))
        self.save_preset_btn.setFixedWidth(160)
        self.save_preset_btn.clicked.connect(self._save_preset)
        grid.addWidget(self.save_preset_btn, 0, 3)
        # Keeps "Engine"/"Matching" and their dropdowns at the same x after
        # moving the save button closer to the preset dropdown.
        grid.addItem(QSpacerItem(61, 0, QSizePolicy.Fixed, QSizePolicy.Minimum), 0, 4)

        grid.addWidget(
            field_hint(
                "Engine",
                "Picks the removal algorithm automatically. Telea/NS are fast; LaMa is slower but smoother.",
            ),
            0, 5, Qt.AlignRight | Qt.AlignVCenter,
        )
        self.engine_combo = ArrowComboBox()
        self.engine_combo.setMaximumWidth(420)
        for label, value in [
            ("Auto", "auto"),
            ("Telea (fast)", "telea"),
            ("NS (fast)", "ns"),
            ("LaMa (slow)", "lama"),
        ]:
            self.engine_combo.addItem(label, value)
        grid.addWidget(self.engine_combo, 0, 6)

        policy_label = field_label("Erase scope")
        policy_label.setFixedWidth(96)
        grid.addWidget(policy_label, 1, 0)
        self.policy_combo = ArrowComboBox()
        self.policy_combo.setMaximumWidth(420)
        self.policy_combo.addItem("Auto (text/element)", "auto")
        self.policy_combo.addItem("Text only", "text_only")
        grid.addWidget(self.policy_combo, 1, 1)

        grid.addWidget(field_label("Matching"), 1, 5)
        self.match_combo = ArrowComboBox()
        self.match_combo.setMaximumWidth(420)
        self.match_combo.addItem("OCR tolerant", "fuzzy")
        self.match_combo.addItem("Exact", "exact")
        grid.addWidget(self.match_combo, 1, 6)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(6, 1)
        outer.addLayout(grid)

        # Row 3: padding, mask overlay, log toggle.
        row = group_row()
        row.addWidget(field_hint(
            "Padding",
            "Expand every erase region by a few pixels.",
        ))
        self.padding_spin = ArrowSpinBox()
        self.padding_spin.setRange(0, 6)
        self.padding_spin.setSuffix(" px")
        self.padding_spin.setMinimumWidth(90)
        self.padding_spin.setMaximumWidth(110)
        self.padding_spin.setToolTip(
            "Expand every erase region by a few pixels."
        )
        self.padding_spin.valueChanged.connect(self._on_padding_changed)
        row.addWidget(self.padding_spin)
        row.addSpacing(20)
        self.show_mask_check = QCheckBox("Show erase mask")
        self.show_mask_check.setChecked(True)
        self.show_mask_check.toggled.connect(self._on_show_mask)
        row.addWidget(self.show_mask_check)
        row.addStretch(1)
        self.log_toggle_btn = QPushButton("Log")
        self.log_toggle_btn.setIcon(icons.icon("log"))
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.toggled.connect(self._toggle_log)
        row.addWidget(self.log_toggle_btn)
        outer.addLayout(row)
        return box

    def _build_file_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self.files_title = QLabel("Image list (0)")
        self.files_title.setStyleSheet("color:#9aa1b1; font-weight:600;")
        layout.addWidget(self.files_title)

        self.drop_hint = QLabel("Drag and drop images or a folder here")
        self.drop_hint.setStyleSheet("color:#6f7789; font-size:12px;")
        self.drop_hint.setWordWrap(True)
        layout.addWidget(self.drop_hint)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_file_menu)
        self.file_list.installEventFilter(self)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        layout.addWidget(self.file_list, 1)

        self.summary_label = QLabel("No images analyzed yet.")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("color:#9aa1b1;")
        layout.addWidget(self.summary_label)
        return panel

    def _build_work_panel(self) -> QWidget:
        preview_box = QGroupBox("Preview")
        pv = QVBoxLayout(preview_box)
        pv.setContentsMargins(8, 8, 8, 8)
        pv.setSpacing(6)

        toolbar = QHBoxLayout()
        self.original_btn = QPushButton("Original")
        self.original_btn.setIcon(icons.icon("original"))
        self.original_btn.setObjectName("segment_left")
        self.original_btn.setCheckable(True)
        self.original_btn.setChecked(True)
        self.result_btn = QPushButton("Result")
        self.result_btn.setIcon(icons.icon("result"))
        self.result_btn.setObjectName("segment_right")
        self.result_btn.setCheckable(True)
        for button in (self.original_btn, self.result_btn):
            button.setAutoExclusive(True)
        self.original_btn.toggled.connect(self._on_view_mode)
        self.result_btn.toggled.connect(self._on_view_mode)
        # Backwards-compatible alias for external smoke scripts.
        self.preview_btn = self.result_btn
        segment = QHBoxLayout()
        segment.setSpacing(0)
        segment.addWidget(self.original_btn)
        segment.addWidget(self.result_btn)
        toolbar.addLayout(segment)
        toolbar.addSpacing(12)

        self.zoom_fit_btn = QPushButton("Fit")
        self.zoom_fit_btn.setObjectName("zoom_btn")
        self.zoom_fit_btn.setIcon(icons.icon("fit"))
        self.zoom_fit_btn.setToolTip("Fit image to the preview")
        self.zoom_fit_btn.clicked.connect(lambda: self.preview.fit_to_view())

        self.zoom_out_btn = QPushButton()
        self.zoom_out_btn.setObjectName("zoom_square")
        self.zoom_out_btn.setIcon(icons.icon("minus"))
        self.zoom_out_btn.setToolTip("Zoom out")
        self.zoom_out_btn.setFixedWidth(32)
        self.zoom_out_btn.clicked.connect(
            lambda: self.preview.zoom_to(self.preview.scale_factor() / 1.25)
        )

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("zoom_label")
        self.zoom_label.setMinimumWidth(46)
        self.zoom_label.setAlignment(Qt.AlignCenter)

        self.zoom_in_btn = QPushButton()
        self.zoom_in_btn.setObjectName("zoom_square")
        self.zoom_in_btn.setIcon(icons.icon("plus"))
        self.zoom_in_btn.setToolTip("Zoom in")
        self.zoom_in_btn.setFixedWidth(32)
        self.zoom_in_btn.clicked.connect(
            lambda: self.preview.zoom_to(self.preview.scale_factor() * 1.25)
        )

        self.zoom_100_btn = QPushButton("100%")
        self.zoom_100_btn.setObjectName("zoom_btn")
        self.zoom_100_btn.setToolTip("View at 1:1")
        self.zoom_100_btn.clicked.connect(lambda: self.preview.zoom_to(1.0))

        for widget in (self.zoom_fit_btn, self.zoom_out_btn, self.zoom_label,
                       self.zoom_in_btn, self.zoom_100_btn):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        pv.addLayout(toolbar)

        help_row = QLabel(
            "Click a region to select · drag to move · drag a corner to resize · "
            "Scroll to zoom · Shift+drag to pan · right-drag to compare"
        )
        help_row.setObjectName("help_row")
        help_row.setStyleSheet("color:#9aa1b1;")
        help_row.setWordWrap(True)
        pv.addWidget(help_row)

        self.preview = PreviewCanvas()
        self.preview.regionSelected.connect(self._on_canvas_region_selected)
        self.preview.regionEdited.connect(self._on_region_edited)
        self.preview.zoomChanged.connect(lambda s: self.zoom_label.setText(f"{int(round(s * 100))}%"))
        pv.addWidget(self.preview, 1)

        regions_box = QGroupBox("Detected regions")
        rv = QVBoxLayout(regions_box)
        rv.setContentsMargins(8, 8, 8, 8)
        rv.setSpacing(6)

        header = QHBoxLayout()
        self.regions_title = QLabel("0 regions")
        self.regions_title.setStyleSheet("color:#9aa1b1;")
        header.addWidget(self.regions_title)
        header.addStretch(1)
        self.uncertain_filter = QCheckBox("Show only regions needing review")
        self.uncertain_filter.setObjectName("uncertain_filter")
        self.uncertain_filter.setToolTip(
            "Hide confident regions; keep only the ones needing review."
        )
        self.uncertain_filter.toggled.connect(self._apply_region_filter)
        header.addWidget(self.uncertain_filter)
        self.enable_all_btn = QPushButton("Enable all")
        self.enable_all_btn.clicked.connect(lambda: self._set_all_enabled(True))
        self.disable_all_btn = QPushButton("Disable all")
        self.disable_all_btn.clicked.connect(lambda: self._set_all_enabled(False))
        self.enable_all_btn.setVisible(False)   # per-region tick only (D8)
        self.disable_all_btn.setVisible(False)
        rv.addLayout(header)

        self.region_table = QTableWidget(0, 7)
        self.region_table.setHorizontalHeaderLabels(
            ["Erase", "Kind", "Keyword / text", "Erase mode", "Padding", "Confidence", "Source"]
        )
        self.region_table.verticalHeader().setVisible(False)
        self.region_table.verticalHeader().setDefaultSectionSize(34)
        self.region_table.setAlternatingRowColors(True)
        self.region_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.region_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.region_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header_view = self.region_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.region_table.setColumnWidth(1, 122)
        self.region_table.itemChanged.connect(self._on_table_item_changed)
        self.region_table.itemSelectionChanged.connect(self._on_table_selection)
        self.region_table.setMinimumHeight(70)
        rv.addWidget(self.region_table, 1)

        self.preview_wrap = QWidget()
        preview_layout = QVBoxLayout(self.preview_wrap)
        preview_layout.setContentsMargins(0, 0, 0, 6)
        preview_layout.setSpacing(0)
        preview_layout.addWidget(preview_box)
        self.regions_wrap = QWidget()
        regions_layout = QVBoxLayout(self.regions_wrap)
        regions_layout.setContentsMargins(0, 6, 0, 0)
        regions_layout.setSpacing(0)
        regions_layout.addWidget(regions_box)

        split = DotsSplitter(Qt.Vertical)
        split.addWidget(self.preview_wrap)
        split.addWidget(self.regions_wrap)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([560, 300])

        work = QWidget()
        work_layout = QVBoxLayout(work)
        work_layout.setContentsMargins(6, 2, 6, 2)
        work_layout.setSpacing(0)
        work_layout.addWidget(split)
        return work

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setIcon(icons.icon("analyze", "#0d1220"))
        self.analyze_btn.clicked.connect(self._analyze_all)
        self.save_btn = QPushButton("Save this image")  # kept for the library API
        self.save_btn.setIcon(icons.icon("save"))
        self.save_btn.clicked.connect(self._save_current)
        self.save_btn.setVisible(False)
        self.save_all_btn = QPushButton("Save all")
        self.save_all_btn.setIcon(icons.icon("save"))
        self.save_all_btn.clicked.connect(self._save_all)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setIcon(icons.icon("cancel", RED))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_worker)

        layout.addWidget(self.analyze_btn)
        layout.addWidget(self.save_btn)
        layout.addWidget(self.save_all_btn)
        layout.addWidget(self.cancel_btn)
        layout.addSpacing(6)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.progress.setMaximumHeight(20)
        layout.addWidget(self.progress, 1)

        self.task_label = QLabel("Ready")
        self.task_label.setMinimumWidth(180)
        self.task_label.setStyleSheet("color:#9aa1b1;")
        layout.addWidget(self.task_label)

        self.banner_label = QLabel("")
        self.banner_label.setObjectName("banner")
        self.banner_label.setStyleSheet(f"color:{RED};")
        self.banner_label.setVisible(False)
        layout.addWidget(self.banner_label)
        return bar

    def _build_log_group(self) -> QWidget:
        box = QGroupBox("Log")
        self.log_box = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 6, 8, 6)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(800)
        self.log_view.setFixedHeight(58)
        self.log_view.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self.log_view)
        box.setVisible(False)
        return box

    def _toggle_log(self, show: bool) -> None:
        self.log_box.setVisible(show)
        self.log_toggle_btn.setText("Hide log" if show else "Log")

    def _toggle_input_group(self, expanded: bool) -> None:
        for widget in self._input_collapsible:
            widget.setVisible(expanded)
        self.input_toggle_btn.setIcon(
            icons.icon("chevron_up" if expanded else "chevron_down")
        )
        self.input_toggle_btn.setToolTip(
            "Collapse section" if expanded else "Expand section"
        )

    def _toggle_advanced(self, show: bool) -> None:
        self.advanced_panel.setVisible(show)
        self.advanced_toggle_btn.setText("Hide advanced" if show else "Advanced options")

    def _set_banner(self, text: str) -> None:
        self.banner_label.setText(text)
        self.banner_label.setVisible(bool(text))

    def _on_inputs_changed(self) -> None:
        self._set_banner("")
        self._update_action_state()

    def _on_output_changed(self) -> None:
        text = self.output_edit.text().strip() or str(Path.home() / "ImageCleaner")
        self.output_label.setText(f"Saving to: {text}")

    def _update_action_state(self) -> None:
        """Validation via disabled states, never pop-up dialogs."""
        if self._busy:
            self._prereq_hint = ""
            self.analyze_btn.setEnabled(False)
            self.save_all_btn.setEnabled(False)
            self.save_btn.setEnabled(False)
            return
        has_images = self.file_list.count() > 0
        has_keywords = bool(normalize_keyword_list(self.keyword_edit.toPlainText()))
        self.analyze_btn.setEnabled(has_images and has_keywords)
        hint = ""
        if not has_images and not has_keywords:
            hint = ("Select images, enter keywords, then click Analyze to start.")
        elif not has_images:
            hint = "Select images to start."
        elif not has_keywords:
            hint = "Enter keywords, then click Analyze."
        if hint:
            self._prereq_hint = hint
            self.task_label.setStyleSheet("color:#f0a03c;")
            self.task_label.setText(hint)
        elif self._prereq_hint:
            self._prereq_hint = ""
            self.task_label.setStyleSheet("color:#9aa1b1;")
            self.task_label.setText("Ready")
        has_regions = any(
            entry.result is not None and any(r.enabled for r in entry.result.regions)
            for entry in self._entries.values()
        )
        self.save_all_btn.setEnabled(has_regions)
        self.save_btn.setEnabled(has_regions)

    def _install_shortcuts(self) -> None:
        for keys, slot in [
            ("Ctrl+O", self._pick_files),
            ("Ctrl+R", self._analyze_all),
            ("Ctrl+S", self._save_current),
            ("Ctrl+Shift+S", self._save_all),
            ("Ctrl+F", lambda: self.preview.fit_to_view()),
        ]:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)

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
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.requestInterruption()
            self._preview_worker.wait(3000)
        self._settings.options = self._collect_options()
        self._settings.last_output_dir = self.output_edit.text().strip()
        try:
            self._settings.save(settings_path())
        except OSError as exc:
            log.warning("could not save settings: %s", exc)
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
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, key)
            item.setToolTip(key)
            self.file_list.addItem(item)
            if first_new is None:
                first_new = item
            added += 1
        self.files_title.setText(f"Image list ({len(self._entries)})")
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
            self.file_list.takeItem(self.file_list.row(item))
        self.files_title.setText(f"Image list ({len(self._entries)})")
        if self.file_list.count() == 0:
            self._show_entry(None)
        self._update_action_state()

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
            self._populate_regions_table([])
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
        # Swap the canvas to the new image first. Resetting to "Original" before
        # loading used to show the previous result -> previous original -> new
        # original -> new result, which looked like a flash.
        self.preview.set_image(self._loaded)
        self._set_zoom_controls_enabled(self._loaded is not None)
        regions = entry.result.regions if entry.result else []
        self.preview.set_regions(regions)
        self._populate_regions_table(regions)
        self._rebuild_mask_overlay()
        self._update_status_label(entry)
        if not self._auto_show_result(entry):
            self._select_original()
        self._update_view_buttons()

    def _on_canvas_region_selected(self, region_id: int) -> None:
        self._updating_table = True
        try:
            self.region_table.clearSelection()
            for row in range(self.region_table.rowCount()):
                item = self.region_table.item(row, 1)
                if item is not None and item.data(Qt.UserRole) == region_id:
                    self.region_table.selectRow(row)
                    break
        finally:
            self._updating_table = False

    def _on_table_selection(self) -> None:
        if self._updating_table:
            return
        rows = {idx.row() for idx in self.region_table.selectionModel().selectedRows()}
        if not rows:
            return
        region_id = self.region_table.item(min(rows), 1).data(Qt.UserRole)
        self.preview.set_selected(-1 if region_id is None else int(region_id))

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

    def _populate_regions_table(self, regions: list[Region]) -> None:
        self._updating_table = True
        try:
            self.region_table.setRowCount(0)
            self.region_table.setRowCount(len(regions))
            for row, region in enumerate(regions):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                check.setCheckState(Qt.Checked if region.enabled else Qt.Unchecked)
                check.setData(Qt.UserRole, region.id)
                self.region_table.setItem(row, 0, check)

                kind_item = QTableWidgetItem(
                    "" if region.kind is RegionKind.UNCERTAIN
                    else REGION_LABELS.get(region.kind, region.kind.value)
                )
                kind_item.setData(Qt.UserRole, region.id)
                kind_item.setForeground(REGION_COLORS.get(region.kind, QColor("#c084fc")))
                self.region_table.setItem(row, 1, kind_item)
                if region.kind is RegionKind.UNCERTAIN:
                    badge = QLabel("Needs review")
                    badge.setObjectName("uncertain_badge")
                    badge.setAlignment(Qt.AlignCenter)
                    badge.setToolTip(
                        "This region is not confident enough. Tick Erase to approve and remove it."
                    )
                    holder = QWidget()
                    holder.setStyleSheet("background: transparent;")
                    badge_row = QHBoxLayout(holder)
                    badge_row.setContentsMargins(2, 2, 2, 2)
                    badge_row.setSpacing(0)
                    badge_row.addWidget(badge, 0, Qt.AlignLeft | Qt.AlignVCenter)
                    badge_row.addStretch(1)
                    self.region_table.setCellWidget(row, 1, holder)

                text = region.match.text if region.match else ""
                keyword = region.match.keyword if region.match else ""
                label = f"{keyword}  →  {text}" if keyword and keyword.casefold() not in text.casefold() else (text or keyword)
                text_item = QTableWidgetItem(label)
                text_item.setToolTip(
                    f"bbox={region.bbox}\nsource={region.source}\n"
                    f"evidence={region.evidence}"
                )
                self.region_table.setItem(row, 2, text_item)

                margin_spin = ArrowSpinBox()
                margin_spin.setRange(-12, 12)
                margin_spin.setSuffix(" px")
                margin_spin.setFixedWidth(84)
                margin_spin.setValue(int(getattr(region, "margin_px", 0) or 0))
                margin_spin.valueChanged.connect(
                    lambda value, rid=region.id:
                    self._on_region_margin_changed(rid, value)
                )
                self.region_table.setCellWidget(row, 4, margin_spin)

                conf = QTableWidgetItem(f"{region.confidence:.2f}")
                conf.setTextAlignment(Qt.AlignCenter)
                self.region_table.setItem(row, 5, conf)

                src = QTableWidgetItem(region.source)
                src.setTextAlignment(Qt.AlignCenter)
                self.region_table.setItem(row, 6, src)

                policy = ArrowComboBox()
                policy.addItem("Auto", None)
                policy.addItem("Text only", "text")
                policy.addItem("Element", "element")
                idx = policy.findData(getattr(region, "mask_mode", None))
                policy.setCurrentIndex(max(0, idx))
                policy.currentIndexChanged.connect(
                    lambda _i, rid=region.id, combo=policy:
                    self._on_region_policy_changed(rid, combo.currentData())
                )
                self.region_table.setCellWidget(row, 3, policy)

                self.region_table.setRowHeight(row, 26)
        finally:
            self._updating_table = False
        self._set_regions_title(regions)
        self._apply_region_filter()

    def _set_regions_title(self, regions: list[Region]) -> None:
        uncertain = sum(1 for r in regions if r.kind is RegionKind.UNCERTAIN)
        text = (
            f"{len(regions)} regions · {sum(1 for r in regions if r.enabled)} enabled"
        )
        if uncertain:
            text += f" · {uncertain} need review"
        self.regions_title.setText(text)

    def _apply_region_filter(self, *_checked) -> None:
        if not hasattr(self, "uncertain_filter"):
            return
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            return
        active = self.uncertain_filter.isChecked()
        for row, region in enumerate(entry.result.regions):
            hide = active and region.kind is not RegionKind.UNCERTAIN
            self.region_table.setRowHidden(row, hide)
        selected = {
            idx.row() for idx in self.region_table.selectionModel().selectedRows()
        }
        if any(self.region_table.isRowHidden(row) for row in selected):
            self.region_table.clearSelection()
            self.preview.set_selected(-1)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table or item.column() != 0:
            return
        entry = self._selected_entry()
        if entry is None or entry.result is None:
            return
        row = item.row()
        if row >= len(entry.result.regions):
            return
        region = entry.result.regions[row]
        enabled = item.checkState() == Qt.Checked

        if enabled and region.kind is RegionKind.UNCERTAIN:
            # Explicit user approval. The region is re-classified so the mask
            # matches what the box actually covers.
            region.kind = RegionKind.ELEMENT if self._looks_like_element(region) else RegionKind.TEXT
            log.info("User approved region #%d → %s", region.id, region.kind.value)
        region.enabled = enabled
        region.mask = None
        self._invalidate_result_preview()
        self._rebuild_mask_overlay()
        self.preview.set_regions(entry.result.regions)

        # Update cells in place: rebuilding the table inside itemChanged is unsafe.
        self._updating_table = True
        try:
            if self.region_table.cellWidget(row, 1) is not None:
                self.region_table.removeCellWidget(row, 1)
            kind_item = self.region_table.item(row, 1)
            if kind_item is not None:
                kind_item.setText(REGION_LABELS.get(region.kind, region.kind.value))
                kind_item.setForeground(REGION_COLORS.get(region.kind, QColor("#c084fc")))
        finally:
            self._updating_table = False
        self._set_regions_title(entry.result.regions)
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

    def _analyze_all(self) -> None:
        if self._busy:
            return
        entries = self._all_entries()
        if not entries:
            QMessageBox.information(self, "No images", "Add images or a folder before analyzing.")
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
            on_success=self._on_analyze_done,
            label="Analyzing",
            busy_total=len(paths),
        )

    def _on_analyze_done(self, results) -> None:
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
        entry = self._selected_entry()
        if entry is not None:
            self._show_entry(entry)
        if failures:
            QMessageBox.warning(
                self, "Some images failed",
                "Could not analyze:\n\n" + "\n".join(failures[:8]),
            )
        total_regions = sum(len(r.regions) for r in analyzed.values())
        log.info("Analyzed %d/%d images, %d regions", len(analyzed), len(results), total_regions)
        self.task_label.setText(f"Analyzed {len(analyzed)} images · {total_regions} regions")
        self._update_action_state()

    def _update_list_item(self, entry: ImageEntry) -> None:
        key = str(entry.path.resolve())
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.data(Qt.UserRole) != key:
                continue
            suffix = ""
            if entry.status == "analyzed" and entry.result is not None:
                suffix = f"  ·  {len(entry.result.regions)} regions"
            elif entry.status == "no_match":
                suffix = "  ·  no match"
            elif entry.status == "failed":
                suffix = "  ·  error"
            elif entry.status == "success":
                suffix = "  ·  saved"
            elif entry.status == "skipped":
                suffix = "  ·  skipped"
            item.setText(entry.path.name + suffix)
            if entry.error:
                item.setToolTip(f"{entry.path}\n{entry.error}")
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
        self.preview.set_result_image(None)
        self.preview.set_show_region_boxes(True)

    def _update_view_buttons(self) -> None:
        """Result view only makes sense once an image is selected."""
        self.result_btn.setEnabled(
            not self._busy and self._selected_entry() is not None
            and self._loaded is not None
        )

    def _set_zoom_controls_enabled(self, enabled: bool) -> None:
        for button in (self.zoom_fit_btn, self.zoom_out_btn,
                       self.zoom_in_btn, self.zoom_100_btn):
            button.setEnabled(enabled)

    def _sync_segment_icons(self) -> None:
        active = "#0d1220"
        self.original_btn.setIcon(icons.icon(
            "original", active if self.original_btn.isChecked() else MUTED
        ))
        self.result_btn.setIcon(icons.icon(
            "result", active if self.result_btn.isChecked() else MUTED
        ))

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
        if self.result_btn.isChecked():
            self._select_original()
            self._set_banner("Region changed — click “Result” to refresh.")

    def _on_view_mode(self, checked: bool) -> None:
        if not checked:
            return
        self._sync_segment_icons()
        if self.original_btn.isChecked():
            self._select_original()
            self._set_banner("")
            return
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
            self._set_banner("Enable at least one region in the table before viewing results.")
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
            return
        path, out, engine = payload
        if self._current_path != path or not self.result_btn.isChecked():
            return  # selection/view changed while the preview was rendering
        self.preview.set_result_image(out)
        self.preview.set_show_region_boxes(False)
        self.task_label.setText(f"Result: {engine}")

    # -------------------------------------------------------------------- saving

    def _save_current(self) -> None:
        if self._busy:
            return
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "No image selected", "Select an image from the list.")
            return
        if entry.result is None:
            QMessageBox.information(self, "Not analyzed", "Click “Analyze” before saving.")
            return
        self._save_entries([entry], label="Saving image")

    def _save_all(self) -> None:
        if self._busy:
            return
        entries = self._all_entries()
        if not entries:
            QMessageBox.information(self, "No images", "Add images before saving.")
            return
        self._save_entries(entries, label="Saving all")

    def _save_entries(self, entries: list[ImageEntry], label: str) -> None:
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
            if reports:
                try:
                    write_reports(reports, out_dir, elapsed)
                except OSError as exc:
                    log.error("Could not write report: %s", exc)
            return SaveOutcome(reports=reports, analyzed=analyzed, elapsed_s=elapsed)

        self._start_worker(job, on_success=self._on_save_done, label=label,
                           busy_total=len(jobs))

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
        if not self.log_toggle_btn.isChecked():
            self.log_toggle_btn.setChecked(True)
        QMessageBox.critical(self, "Task failed", traceback_text.splitlines()[-1])
        self.task_label.setText("Task failed")

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
        if self.result_btn.isChecked():
            self._select_original()
            self._set_banner("Could not build the preview result.")

    def _on_worker_finished(self) -> None:
        self._set_busy(False)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self.progress.setFormat("%v/%m")
        self.progress.setValue(self.progress.maximum())
        if self._show_result_on_idle:
            self._show_result_on_idle = False
            self._auto_show_result(self._selected_entry())

    def _cancel_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self.task_label.setText("Cancelling…")

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        for button in (self.analyze_btn, self.save_btn, self.save_all_btn, self.preview_btn,
                       self.add_files_btn, self.remove_btn, self.enable_all_btn,
                       self.disable_all_btn):
            button.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.cancel_btn.setVisible(busy)
        self.progress.setVisible(busy)
        self._update_action_state()
        if busy:
            self.task_label.setText(f"{label}…")
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            QApplication.restoreOverrideCursor()
            if not label:
                self.progress.setValue(0)
                self.progress.setFormat("%v/%m")
            self._update_view_buttons()

    # ------------------------------------------------------------------ helpers

    def _update_status_label(self, entry: ImageEntry) -> None:
        if entry is None or entry.result is None:
            return
        result = entry.result
        parts = [f"{entry.path.name} · {result.width}×{result.height}",
                 f"{len(result.lines)} OCR lines", f"{len(result.regions)} regions"]
        uncertain = len(result.uncertain_regions)
        if uncertain:
            parts.append(f"{uncertain} need review")
        if result.timings:
            parts.append(f"{result.timings.get('ocr', 0) + result.timings.get('load', 0):.1f}s OCR")
        self.task_label.setText(" · ".join(parts))

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

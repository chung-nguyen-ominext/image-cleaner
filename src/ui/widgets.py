"""Reusable UI widgets for the redesigned main window.

Everything here is presentation-only: widgets emit signals and let
``MainWindow`` mutate the analysis results.  Colours come from the external
stylesheet or from ``styles/tokens.json`` where a widget has to paint.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from src.core.models import Region, RegionKind
from src.ui import icons
from src.ui.controls import ArrowComboBox, ArrowSpinBox
from src.ui.theme import ACCENT, MUTED, ON_PRIMARY, dim_placeholder, repolish


class ElidedLabel(QLabel):
    """A QLabel that elides its text instead of forcing the layout wider."""

    def __init__(self, text: str = "", parent=None, mode: Qt.TextElideMode = Qt.ElideRight):
        super().__init__(parent)
        self._full_text = text
        self._elide_mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        super().setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._apply_elide()
        QTimer.singleShot(0, self._apply_elide)

    def fullText(self) -> str:
        return self._full_text

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(self._full_text) + 2
        width = min(width, self.maximumWidth())
        return QSize(max(width, 0), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply_elide()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_elide)

    def _apply_elide(self) -> None:
        if not hasattr(self, "_full_text"):
            return
        width = max(0, self.width() - 2)
        text = self.fontMetrics().elidedText(self._full_text, self._elide_mode, width)
        super().setText(text)


class KeywordChipInput(QFrame):
    """Single-line chip editor for keywords.

    Enter or comma commits the current word; Backspace on an empty editor
    removes the last chip.  The public API mirrors the old QPlainTextEdit
    (``toPlainText``/``setPlainText``/``textChanged``) so the processing code
    does not need to care which widget holds the keywords.
    """

    textChanged = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("keyword_chip_input")
        self.setFixedHeight(36)
        self.setMinimumWidth(280)
        self.setMaximumWidth(640)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._keywords: list[str] = []
        self._hint = ""
        self._emitting = False

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 3, 8, 3)
        row.setSpacing(8)

        self.prefix = QLabel("Keywords")
        self.prefix.setObjectName("keyword_prefix")
        row.addWidget(self.prefix)

        # Chips + editor live inside a horizontally-scrollable strip. When the
        # keywords no longer fit, the editor keeps its width and the strip
        # scrolls instead of squeezing every chip.
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("keyword_scroll")
        self._scroll.setWidgetResizable(False)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.NoFocus)
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._scroll.viewport().setAutoFillBackground(False)

        self._scroll_content = QWidget(self._scroll)
        self._scroll_content.setObjectName("keyword_scroll_content")
        content_row = QHBoxLayout(self._scroll_content)
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(6)

        self._chips_host = QWidget(self._scroll_content)
        self._chips_host.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self._chips_layout = QHBoxLayout(self._chips_host)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(6)
        content_row.addWidget(self._chips_host)

        self._editor = QLineEdit(self._scroll_content)
        self._editor.setObjectName("keyword_editor")
        self._editor.setMinimumWidth(150)
        self._editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._editor.setAccessibleName("Keywords")
        self._editor.installEventFilter(self)
        self._editor.textEdited.connect(self._on_editor_edited)
        self._editor.returnPressed.connect(self._commit_editor)
        content_row.addWidget(self._editor, 1)

        self._scroll.setWidget(self._scroll_content)
        row.addWidget(self._scroll, 1)

        self._help_btn = QToolButton(self)
        self._help_btn.setObjectName("help_btn")
        self._help_btn.setIcon(icons.icon("help", MUTED))
        self._help_btn.setIconSize(QSize(14, 14))
        self._help_btn.setToolTip(
            "Press Enter or comma to add. Matching ignores case."
        )
        self._help_btn.setAccessibleName("Keyword help")
        self._help_btn.setCursor(Qt.WhatsThisCursor)
        row.addWidget(self._help_btn)

        self._scroll.installEventFilter(self)
        self._scroll.viewport().installEventFilter(self)
        self._scroll_content.installEventFilter(self)
        self._chips_host.installEventFilter(self)

        self.setPlaceholderText("Add keyword, press Enter")
        dim_placeholder(self._editor)
        QTimer.singleShot(0, self._sync_content_width)

    # ------------------------------------------------------------ public API

    def keywords(self) -> list[str]:
        return list(self._keywords)

    def set_keywords(self, keywords: list[str]) -> None:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in keywords:
            word = " ".join(str(raw).split()).strip()
            key = word.casefold()
            if word and key not in seen:
                seen.add(key)
                normalized.append(word)
        self._keywords = normalized
        self._rebuild_chips()
        self._emit_changed()

    def toPlainText(self) -> str:  # noqa: N802 - old widget API
        return "\n".join(self._keywords)

    def setPlainText(self, text: str) -> None:  # noqa: N802 - old widget API
        items: list[str] = []
        for line in str(text).replace("\n", ",").split(","):
            word = " ".join(line.split()).strip()
            if word:
                items.append(word)
        self.set_keywords(items)

    def clear(self) -> None:
        self.set_keywords([])

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - old widget API
        self._hint = text
        self._editor.setPlaceholderText(text)

    def placeholderText(self) -> str:  # noqa: N802 - old widget API
        return self._hint

    def clear_ghost_guard(self) -> None:
        """Kept for compatibility with the old text widgets."""

    def setReadOnly(self, read_only: bool) -> None:  # noqa: N802
        self._editor.setReadOnly(read_only)

    def setFocus(self, reason: Qt.FocusReason = Qt.OtherFocusReason) -> None:  # noqa: N802
        self._editor.setFocus(reason)

    def hasFocus(self) -> bool:  # noqa: N802
        return self._editor.hasFocus()

    # ----------------------------------------------------------- internals

    def _emit_changed(self) -> None:
        self._emitting = True
        try:
            self.textChanged.emit("\n".join(self._keywords))
        finally:
            self._emitting = False

    def _add_word(self, raw: str) -> bool:
        word = " ".join(str(raw).split()).strip()
        if not word:
            return False
        if any(word.casefold() == existing.casefold() for existing in self._keywords):
            return False
        self._keywords.append(word)
        self._rebuild_chips()
        return True

    def _commit_editor(self) -> None:
        if self._add_word(self._editor.text()):
            self._editor.clear()
            self._emit_changed()
        else:
            self._editor.clear()
        QTimer.singleShot(0, self._scroll_to_end)

    def _on_editor_edited(self, text: str) -> None:
        if "," not in text:
            self._scroll_to_end()
            return
        parts = text.split(",")
        changed = False
        for chunk in parts[:-1]:
            changed = self._add_word(chunk) or changed
        self._editor.setText(parts[-1])
        if changed:
            self._emit_changed()
        QTimer.singleShot(0, self._scroll_to_end)

    def _remove_keyword(self, index: int) -> None:
        if 0 <= index < len(self._keywords):
            del self._keywords[index]
            self._rebuild_chips()
            self._emit_changed()

    def _rebuild_chips(self) -> None:
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, word in enumerate(self._keywords):
            self._chips_layout.addWidget(self._make_chip(word, index))
        self._chips_host.setVisible(bool(self._keywords))
        QTimer.singleShot(0, self._scroll_to_end)

    def _make_chip(self, word: str, index: int) -> QWidget:
        chip = QFrame(self._chips_host)
        chip.setObjectName("keyword_chip")
        row = QHBoxLayout(chip)
        row.setContentsMargins(7, 1, 3, 1)
        row.setSpacing(4)

        label = QLabel(word, chip)
        label.setObjectName("chip_text")
        label.setMaximumWidth(150)
        label.setToolTip(word)
        row.addWidget(label, 1)

        close = QToolButton(chip)
        close.setObjectName("chip_close")
        close.setText("×")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(18, 18)
        close.setToolTip(f"Remove “{word}”")
        close.setAccessibleName(f"Remove keyword {word}")
        close.clicked.connect(lambda _checked=False, i=index: self._remove_keyword(i))
        row.addWidget(close)
        return chip

    def _sync_content_width(self) -> None:
        if not hasattr(self, "_scroll"):
            return
        viewport = max(1, self._scroll.viewport().width())
        chips = self._chips_host.sizeHint().width() if self._keywords else 0
        editor_hint = self._editor.sizeHint().width()
        editor_width = max(150, min(280, editor_hint))
        desired = (chips + 6 if chips else 0) + editor_width
        width = max(viewport, desired)
        self._scroll_content.setFixedWidth(width)
        self._scroll_content.setFixedHeight(max(24, self._scroll.viewport().height()))
        if width <= viewport:
            self._scroll.horizontalScrollBar().setValue(0)

    def _scroll_to_end(self) -> None:
        if not hasattr(self, "_scroll"):
            return
        self._sync_content_width()
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._sync_content_width()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_content_width)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Wheel and obj in (
            self._scroll, self._scroll.viewport(), self._scroll_content,
            self._chips_host, self._editor,
        ):
            bar = self._scroll.horizontalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            return True
        if obj is self._editor:
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Backspace:
                if not self._editor.text() and self._keywords:
                    self._remove_keyword(len(self._keywords) - 1)
                    return True
            elif event.type() == QEvent.FocusIn:
                self.setProperty("focused", True)
                repolish(self)
                QTimer.singleShot(0, self._scroll_to_end)
            elif event.type() == QEvent.FocusOut:
                self._commit_editor()
                self.setProperty("focused", False)
                repolish(self)
        return super().eventFilter(obj, event)


def load_thumbnail(path: Path, width: int = 30, height: int = 40) -> QPixmap:
    """Read only a small thumbnail, not the full screenshot."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        scaled = size.scaled(
            QSize(width * 2, height * 2), Qt.KeepAspectRatioByExpanding
        )
        reader.setScaledSize(scaled)
    image = reader.read()
    if image.isNull():
        return QPixmap()
    pixmap = QPixmap.fromImage(image)
    pixmap = pixmap.scaled(
        width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
    )
    x = max(0, (pixmap.width() - width) // 2)
    y = max(0, (pixmap.height() - height) // 2)
    return pixmap.copy(x, y, width, height)


class ImageRow(QWidget):
    """One image-list row: thumbnail, elided name and a text status."""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("image_row")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(56)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(10)

        self.thumb = QLabel(self)
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(30, 40)
        self.thumb.setAlignment(Qt.AlignCenter)
        pixmap = load_thumbnail(path)
        if not pixmap.isNull():
            self.thumb.setPixmap(pixmap)
        row.addWidget(self.thumb)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.name = ElidedLabel(path.name, self)
        self.name.setObjectName("image_name")
        self.name.setToolTip(str(path))
        text_col.addWidget(self.name)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(5)
        self.dot = QLabel("●", self)
        self.dot.setObjectName("status_dot")
        self.dot.setFixedWidth(10)
        status_row.addWidget(self.dot)
        self.status = ElidedLabel("Not analyzed", self)
        self.status.setObjectName("status_text")
        status_row.addWidget(self.status, 1)
        text_col.addLayout(status_row)

        row.addLayout(text_col, 1)

    def set_status(self, text: str, state: str) -> None:
        self.status.setText(text)
        for widget in (self.status, self.dot):
            widget.setProperty("state", state)
            repolish(widget)

    def set_error(self, error: str) -> None:
        self.setToolTip(f"{self.name.toolTip()}\n{error}" if error else self.name.toolTip())


class DropZone(QFrame):
    """Dashed hint at the bottom of the image list."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("drop_zone")
        self.setFixedHeight(64)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        icon = QLabel(self)
        icon.setPixmap(icons.pixmap("upload", 16, MUTED))
        row.addWidget(icon)
        label = QLabel("Drop images or a folder here", self)
        label.setWordWrap(True)
        row.addWidget(label, 1)


class EmptyState(QWidget):
    """First-launch empty state shown instead of the preview image."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("empty_state")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        center = QHBoxLayout()
        center.addStretch(1)
        self.card = QFrame(self)
        self.card.setObjectName("empty_card")
        self.card.setMinimumWidth(620)
        self.card.setMaximumWidth(760)
        self.card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        center.addWidget(self.card)
        center.addStretch(1)
        outer.addLayout(center)
        outer.addStretch(1)

        card = QVBoxLayout(self.card)
        card.setContentsMargins(36, 30, 36, 26)
        card.setSpacing(12)
        card.setAlignment(Qt.AlignHCenter)

        icon = QLabel(self.card)
        icon.setPixmap(icons.pixmap("image", 48, ACCENT))
        icon.setAlignment(Qt.AlignCenter)
        card.addWidget(icon, 0, Qt.AlignHCenter)

        title = QLabel("Drop images or a folder here", self.card)
        title.setObjectName("empty_title")
        title.setAlignment(Qt.AlignCenter)
        card.addWidget(title)

        subtitle = QLabel(
            "Everything runs on your computer. Nothing is uploaded.", self.card
        )
        subtitle.setObjectName("empty_subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        card.addWidget(subtitle)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.add_btn = QPushButton("Add images", self.card)
        self.add_btn.setObjectName("empty_add_btn")
        self.add_btn.setProperty("variant", "primary")
        self.add_btn.setProperty("sizeClass", "save")
        self.add_btn.setIcon(icons.icon("images", ON_PRIMARY))
        actions.addWidget(self.add_btn)
        self.folder_btn = QPushButton("Choose folder", self.card)
        self.folder_btn.setObjectName("empty_folder_btn")
        self.folder_btn.setProperty("sizeClass", "save")
        self.folder_btn.setIcon(icons.icon("folder"))
        actions.addWidget(self.folder_btn)
        actions.addStretch(1)
        card.addLayout(actions)

        divider = QFrame(self.card)
        divider.setObjectName("empty_divider")
        divider.setFixedHeight(1)
        card.addSpacing(6)
        card.addWidget(divider)
        card.addSpacing(2)

        steps = QHBoxLayout()
        steps.setSpacing(26)
        steps.addStretch(1)
        for number, text in enumerate(
            ("Add images", "Type keywords", "Analyze", "Review & save"), start=1
        ):
            step = QHBoxLayout()
            step.setSpacing(7)
            badge = QLabel(str(number), self.card)
            badge.setObjectName("step_badge")
            badge.setAlignment(Qt.AlignCenter)
            step.addWidget(badge)
            label = QLabel(text, self.card)
            label.setObjectName("step_label")
            step.addWidget(label)
            steps.addLayout(step)
        steps.addStretch(1)
        card.addLayout(steps)


def _tag(text: str, tone: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("tag")
    if tone:
        label.setProperty("tone", tone)
    return label


ERASE_MODES = (
    ("Blend background", None),
    ("Solid fill", "element"),
    ("Blur", "text"),
)


class RegionCard(QFrame):
    """One detected region, expandable when selected."""

    selected = Signal(int)
    enabledChanged = Signal(int, bool)
    eraseModeChanged = Signal(int, object)
    paddingChanged = Signal(int, int)

    def __init__(self, region: Region, index: int, parent=None) -> None:
        super().__init__(parent)
        self.region = region
        self.index = index
        self.setObjectName("region_card")
        self.setProperty("selected", False)
        self.setProperty("review", region.kind is RegionKind.UNCERTAIN)
        # NOTE: never name this property "enabled": QWidget already has an
        # "enabled" Qt property, so setProperty would disable the whole card
        # (which made "Needs review" cards unclickable and uneditable).
        self.setProperty("eraseOn", bool(region.enabled))
        self.setCursor(Qt.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 9, 10, 9)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.check = QCheckBox(self)
        self.check.setObjectName("region_check")
        self.check.setChecked(bool(region.enabled))
        self.check.setAccessibleName(f"Erase region {index}")
        self.check.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.check, 0, Qt.AlignTop)

        self.name = ElidedLabel(self._title(region), self)
        self.name.setObjectName("region_name")
        top.addWidget(self.name, 1, Qt.AlignVCenter)

        self.confidence = QLabel(self)
        self.confidence.setObjectName("confidence")
        top.addWidget(self.confidence, 0, Qt.AlignTop)
        outer.addLayout(top)

        tags = QHBoxLayout()
        tags.setSpacing(6)
        self.kind_tag = _tag("")
        self.source_tag = _tag("")
        self.review_tag = _tag("Needs review", "warn")
        tags.addWidget(self.kind_tag)
        tags.addWidget(self.source_tag)
        tags.addWidget(self.review_tag)
        tags.addStretch(1)
        outer.addLayout(tags)

        self.review_note = QLabel(
            "Won't be erased until you tick it.", self
        )
        self.review_note.setObjectName("review_note")
        self.review_note.setWordWrap(True)
        outer.addWidget(self.review_note)

        self.details = QWidget(self)
        self.details.setObjectName("region_details")
        details = QHBoxLayout(self.details)
        details.setContentsMargins(0, 8, 0, 0)
        details.setSpacing(10)

        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        mode_label = QLabel("Erase mode", self.details)
        mode_label.setObjectName("field_label_small")
        mode_col.addWidget(mode_label)
        self.mode = ArrowComboBox(self.details)
        self.mode.setObjectName("erase_mode_combo")
        self.mode.setAccessibleName("Erase mode")
        for label, value in ERASE_MODES:
            self.mode.addItem(label, value)
        idx = self.mode.findData(getattr(region, "mask_mode", None))
        self.mode.setCurrentIndex(max(0, idx))
        mode_col.addWidget(self.mode)
        details.addLayout(mode_col, 1)

        padding_col = QVBoxLayout()
        padding_col.setSpacing(4)
        padding_label = QLabel("Padding (px)", self.details)
        padding_label.setObjectName("field_label_small")
        padding_col.addWidget(padding_label)
        self.padding = ArrowSpinBox(self.details)
        self.padding.setObjectName("region_padding")
        self.padding.setAccessibleName("Padding in pixels")
        # The 28px right padding (needed so the line edit never covers the
        # arrows) inflates the size hint; cap it so the card still fits the
        # regions column without clipping.
        self.padding.setMinimumWidth(72)
        self.padding.setMaximumWidth(84)
        self.padding.setRange(-12, 12)
        self.padding.setValue(int(getattr(region, "margin_px", 0) or 0))
        padding_col.addWidget(self.padding)
        details.addLayout(padding_col, 0)

        outer.addWidget(self.details)

        self.check.toggled.connect(self._on_check_toggled)
        self.mode.currentIndexChanged.connect(
            lambda _index: self.eraseModeChanged.emit(
                self.region.id, self.mode.currentData()
            )
        )
        self.padding.valueChanged.connect(
            lambda value: self.paddingChanged.emit(self.region.id, int(value))
        )

        self.refresh(region)
        self.set_selected(False)

    @staticmethod
    def _title(region: Region) -> str:
        if region.match is not None:
            if region.match.text:
                return region.match.text
            if region.match.keyword:
                return region.match.keyword
        return "Detected region"

    def refresh(self, region: Region) -> None:
        self.region = region
        self.check.blockSignals(True)
        self.check.setChecked(bool(region.enabled))
        self.check.blockSignals(False)
        self.name.setText(self._title(region))
        self.confidence.setText(f"{int(round(region.confidence * 100))}%")

        review = region.kind is RegionKind.UNCERTAIN
        self.setProperty("review", review)
        self.setProperty("eraseOn", bool(region.enabled))
        self.confidence.setProperty("review", review)
        self.kind_tag.setText(
            "Button" if region.kind is RegionKind.ELEMENT else "Text"
        )
        self.source_tag.setText(
            "Found by OCR" if region.source == "ocr" else "Manual"
        )
        self.review_tag.setVisible(review)
        self.review_note.setVisible(review)

        self.mode.blockSignals(True)
        idx = self.mode.findData(getattr(region, "mask_mode", None))
        self.mode.setCurrentIndex(max(0, idx))
        self.mode.blockSignals(False)
        self.padding.blockSignals(True)
        self.padding.setValue(int(getattr(region, "margin_px", 0) or 0))
        self.padding.blockSignals(False)
        for widget in (self, self.confidence):
            repolish(widget)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self.details.setVisible(bool(selected))
        repolish(self)
        self.updateGeometry()

    def set_busy(self, busy: bool) -> None:
        self.check.setEnabled(not busy)
        self.mode.setEnabled(not busy)
        self.padding.setEnabled(not busy)

    def _on_check_toggled(self, checked: bool) -> None:
        self.setProperty("eraseOn", bool(checked))
        repolish(self)
        self.enabledChanged.emit(self.region.id, bool(checked))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.region.id)
        super().mousePressEvent(event)


class RegionCardList(QWidget):
    """Scrollable list of region cards with a placeholder for empty states."""

    regionSelected = Signal(int)
    enabledChanged = Signal(int, bool)
    eraseModeChanged = Signal(int, object)
    paddingChanged = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: dict[int, RegionCard] = {}
        self._selected_id = -1
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.placeholder = QLabel(
            "Matches appear here after you analyze.\n"
            "You can tick, untick or adjust each one before saving.",
            self,
        )
        self.placeholder.setObjectName("regions_placeholder")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        outer.addWidget(self.placeholder, 1)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("region_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.canvas = QWidget()
        self.canvas.setObjectName("region_canvas")
        self.canvas_layout = QVBoxLayout(self.canvas)
        self.canvas_layout.setContentsMargins(0, 0, 6, 0)
        self.canvas_layout.setSpacing(8)
        self.canvas_layout.addStretch(1)
        self.scroll.setWidget(self.canvas)
        outer.addWidget(self.scroll, 1)
        self.scroll.setVisible(False)

    def set_regions(self, regions: list[Region]) -> None:
        previous = self._selected_id
        self.clear()
        for index, region in enumerate(regions, start=1):
            card = RegionCard(region, index, self.canvas)
            card.selected.connect(self._on_card_selected)
            card.enabledChanged.connect(self.enabledChanged)
            card.eraseModeChanged.connect(self.eraseModeChanged)
            card.paddingChanged.connect(self.paddingChanged)
            card.set_busy(self._busy)
            self.canvas_layout.insertWidget(self.canvas_layout.count() - 1, card)
            self._cards[region.id] = card
        has_regions = bool(regions)
        self.scroll.setVisible(has_regions)
        self.placeholder.setVisible(not has_regions)
        if previous in self._cards:
            self.select_region(previous)
        else:
            self._selected_id = -1
            for card in self._cards.values():
                card.set_selected(False)

    def clear(self) -> None:
        for card in list(self._cards.values()):
            self.canvas_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._selected_id = -1

    def refresh_region(self, region: Region) -> None:
        card = self._cards.get(region.id)
        if card is not None:
            card.refresh(region)

    def select_region(self, region_id: int, *, emit: bool = False) -> None:
        previous = self._selected_id
        self._selected_id = region_id if region_id in self._cards else -1
        for rid, card in self._cards.items():
            card.set_selected(rid == self._selected_id)
        if self._selected_id in self._cards:
            self.scroll.ensureWidgetVisible(self._cards[self._selected_id], 0, 8)
        if emit and previous != self._selected_id:
            self.regionSelected.emit(self._selected_id)

    def selected_region_id(self) -> int:
        return self._selected_id

    def set_review_filter(self, active: bool) -> None:
        hidden_selected = False
        for card in self._cards.values():
            hide = active and card.region.kind is not RegionKind.UNCERTAIN
            card.setVisible(not hide)
            if hide and card.region.id == self._selected_id:
                hidden_selected = True
        if hidden_selected:
            self.select_region(-1, emit=True)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for card in self._cards.values():
            card.set_busy(busy)

    def _on_card_selected(self, region_id: int) -> None:
        # Emit even when the card was already selected: the window uses this
        # to bring the Original view back after the user inspected Result.
        self.select_region(region_id)
        self.regionSelected.emit(region_id)

    def cards(self) -> list[RegionCard]:
        return list(self._cards.values())

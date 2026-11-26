"""Widget subclasses that use QtAwesome icons for their affordances.

The dark stylesheet customises QComboBox/QSpinBox sub-controls, which makes Qt
omit the native arrows. These subclasses draw a Font Awesome chevron instead of
hand-coded lines, so every control uses the same offline icon library.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSizePolicy,
    QSpinBox, QSplitter, QToolTip, QWidget,
)

from src.ui import icons
from src.ui.theme import MUTED, TEXT


class ArrowComboBox(QComboBox):
    """QComboBox with a visible chevron in the drop-down area."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        color = TEXT if self.underMouse() else MUTED
        pix = icons.pixmap("chevron_down", 14, color)
        painter = QPainter(self)
        painter.drawPixmap(
            self.width() - 11 - pix.width() // 2,
            (self.height() - pix.height()) // 2,
            pix,
        )
        painter.end()


class ArrowSpinBox(QSpinBox):
    """QSpinBox with visible up/down chevrons next to the value."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        painter = QPainter(self)
        cx = self.width() - 11
        for name, quarter in (("chevron_up", 0.27), ("chevron_down", 0.73)):
            pix = icons.pixmap(name, 12, MUTED)
            painter.drawPixmap(
                cx - pix.width() // 2,
                int(self.height() * quarter) - pix.height() // 2,
                pix,
            )
        painter.end()


class DotsSplitter(QSplitter):
    """Thin splitter with a visible library-icon grip centered on each handle.

    The grips are siblings of the splitter, not its children: adding widgets
    directly to a QSplitter would make Qt treat them as extra panes and create
    new handles indefinitely. A 3 px bar stays thin while a 14 px icon floats
    above it.
    """

    GRIP = 12

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(3)
        self._grips: list[QLabel] = []
        self._overlay_parent: QWidget | None = None
        self.splitterMoved.connect(self._update_grips)

    def _ensure_grips(self) -> QWidget | None:
        parent = self.parentWidget()
        if parent is None:
            return None
        if parent is not self._overlay_parent:
            for grip in self._grips:
                grip.deleteLater()
            self._grips.clear()
            self._overlay_parent = parent
        needed = max(0, self.count() - 1)
        name = "grip_vertical" if self.orientation() == Qt.Horizontal else "grip_horizontal"
        while len(self._grips) < needed:
            grip = QLabel(parent)
            grip.setPixmap(icons.pixmap(name, self.GRIP, MUTED))
            grip.setFixedSize(self.GRIP, self.GRIP)
            grip.setObjectName("splitter_grip")
            grip.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._grips.append(grip)
        return parent

    def _update_grips(self, *_args) -> None:
        parent = self._ensure_grips()
        if parent is None:
            return
        for index, grip in enumerate(self._grips):
            if index >= self.count() - 1:
                grip.hide()
                continue
            handle = self.handle(index + 1)
            centre = handle.mapTo(parent, handle.rect().center())
            grip.move(centre.x() - self.GRIP // 2, centre.y() - self.GRIP // 2)
            grip.show()
            grip.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._update_grips()

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().moveEvent(event)
        self._update_grips()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._update_grips()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        for grip in self._grips:
            grip.hide()


class HintLabel(QWidget):
    """Form label with a grey info icon; only the icon carries the tooltip.

    Hovering or clicking the icon shows the hint immediately. Hovering the text
    itself stays quiet so labels do not pop up tooltips unexpectedly.
    """

    def __init__(self, text: str, hint: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("hint_label")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self._hint = hint
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.label = QLabel(text, self)
        self.label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.badge = QLabel(self)
        self.badge.setObjectName("hint_badge")
        self.badge.setPixmap(icons.pixmap("info", 16, MUTED))
        self.badge.setFixedSize(16, 16)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setAccessibleName("Information")
        self.badge.setToolTip(hint)
        self.badge.setCursor(Qt.WhatsThisCursor)
        self.badge.installEventFilter(self)
        # Keep the label + info badge together and hug the right edge so the
        # tip icon sits next to its field instead of floating far to the left.
        row.addStretch(1)
        row.addWidget(self.label)
        row.addWidget(self.badge)

    def text(self) -> str:
        return self.label.text()

    def hint(self) -> str:
        return self._hint

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt naming
        if obj is self.badge:
            if event.type() == QEvent.Enter:
                QToolTip.showText(
                    self.badge.mapToGlobal(QPoint(0, self.badge.height())),
                    self._hint, self.badge,
                )
            elif event.type() == QEvent.MouseButtonPress:
                QToolTip.showText(QCursor.pos(), self._hint, self.badge)
        return super().eventFilter(obj, event)


class HintPlainTextEdit(QPlainTextEdit):
    """Placeholder disappears as soon as the field is focused.

    The hint should never compete with what the user is typing, so it is hidden
    on focus and only restored when the field loses focus while still empty.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._hint = ""
        self._focused = False
        self._blurred_empty = False
        self._discarding = False
        # This is an English-only field. Disable IME composition so a pending
        # preedit cannot be committed as a stale character on focus-out.
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self.textChanged.connect(self._sync_hint)
        self.textChanged.connect(self._discard_unfocused_text)

    def _discard_unfocused_text(self) -> None:
        if self._discarding:
            return
        if self._blurred_empty and self.toPlainText():
            self._discarding = True
            self.setPlainText("")
            self._discarding = False
            self._sync_hint()
            self.viewport().repaint()

    def clear_ghost_guard(self) -> None:
        """Allow the app to set text programmatically while unfocused."""
        self._blurred_empty = False

    def _sync_hint(self) -> None:
        show = not self._focused and not self.toPlainText().strip()
        super().setPlaceholderText(self._hint if show else "")
        # Qt can keep the last cleared glyph in the viewport when the
        # placeholder is toggled during an edit; invalidate the whole viewport.
        self.viewport().update()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._hint = text
        self._sync_hint()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._blurred_empty and self.toPlainText():
            self.setPlainText("")
        self._blurred_empty = False
        self._focused = True
        super().focusInEvent(event)
        self._sync_hint()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._focused = False
        was_empty = not self.toPlainText()
        # Drop any uncommitted IME composition before Qt commits it on blur.
        QGuiApplication.inputMethod().reset()
        super().focusOutEvent(event)
        # If the field was empty before blur, any text that appeared is the
        # stale IME commit reported after clearing the field.
        if was_empty and self.toPlainText():
            self.setPlainText("")
        self._sync_hint()
        self.viewport().repaint()
        self._blurred_empty = was_empty
        if was_empty:
            for delay in (0, 50, 150, 300, 600):
                QTimer.singleShot(delay, self._discard_late_commit)

    def _discard_late_commit(self) -> None:
        if self._blurred_empty and self.toPlainText():
            self.setPlainText("")
            self._sync_hint()
            self.viewport().repaint()


class HintLineEdit(QLineEdit):
    """QLineEdit that discards text committed after an empty field loses focus."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._blurred_empty = False
        self._discarding = False
        # Keep IME composition from leaving a character behind on focus-out.
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self.textChanged.connect(self._refresh_if_empty)
        self.textChanged.connect(self._discard_unfocused_text)

    def _discard_unfocused_text(self, text: str) -> None:
        if self._discarding:
            return
        if self._blurred_empty and text:
            self._discarding = True
            self.setText("")
            self._discarding = False
            self.repaint()

    def clear_ghost_guard(self) -> None:
        """Allow the app to set text programmatically while unfocused."""
        self._blurred_empty = False

    def _refresh_if_empty(self, text: str) -> None:
        if not text:
            self.repaint()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._blurred_empty and self.text():
            self.setText("")
        self._blurred_empty = False
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        was_empty = not self.text()
        QGuiApplication.inputMethod().reset()
        super().focusOutEvent(event)
        if was_empty and self.text():
            self.setText("")
        self.repaint()
        self._blurred_empty = was_empty
        if was_empty:
            for delay in (0, 50, 150, 300, 600):
                QTimer.singleShot(delay, self._discard_late_commit)

    def _discard_late_commit(self) -> None:
        if self._blurred_empty and self.text():
            self.setText("")
            self.repaint()

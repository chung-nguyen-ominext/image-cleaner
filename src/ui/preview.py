"""Zoomable image preview with region overlays and box editing."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from src.core.models import Region, RegionKind
from src.utils.image_utils import LoadedImage

COLORS = {
    RegionKind.ELEMENT: QColor("#3ecf8e"),
    RegionKind.TEXT: QColor("#f0a03c"),
    RegionKind.UNCERTAIN: QColor("#f0616d"),
}

HANDLES = {
    "tl": (0.0, 0.0), "t": (0.5, 0.0), "tr": (1.0, 0.0),
    "r": (1.0, 0.5), "br": (1.0, 1.0), "b": (0.5, 1.0),
    "bl": (0.0, 1.0), "l": (0.0, 0.5),
}
CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
    "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
}


def bgr_to_qimage(bgr: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()


def rgba_to_qimage(rgba: np.ndarray) -> QImage:
    data = np.ascontiguousarray(rgba)
    h, w = data.shape[:2]
    return QImage(data.data, w, h, data.strides[0], QImage.Format_RGBA8888).copy()


class PreviewCanvas(QWidget):
    regionSelected = Signal(int)
    regionEdited = Signal(int, object)
    zoomChanged = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 140)

        self._loaded: LoadedImage | None = None
        self._regions: list[Region] = []
        self._pixmap: QPixmap | None = None
        self._result_pixmap: QPixmap | None = None
        self._mask_pixmap: QPixmap | None = None
        self._show_mask = True
        self.show_region_boxes = True
        self._split = 1.0          # 1.0 = full result, 0.0 = full original
        self._wipe_drag = False

        self._scale = 1.0
        self._offset = QPointF(0.0, 0.0)
        self._fit = True

        self._selected = -1
        self._hover_handle: str | None = None
        self._drag_id = -1
        self._drag_handle: str | None = None
        self._drag_rect: QRectF | None = None
        self._drag_start = QPointF()
        self._drag_origin = QRectF()
        self._panning = False
        self._pan_start = QPointF()
        self._pan_offset = QPointF()

    # ------------------------------------------------------------------ public

    def set_image(self, loaded: LoadedImage | None) -> None:
        self._loaded = loaded
        self._result_pixmap = None
        self._mask_pixmap = None
        self._pixmap = QPixmap.fromImage(bgr_to_qimage(loaded.bgr)) if loaded is not None else None
        self._selected = -1
        self._fit = True
        self._recalc_fit()
        self.update()

    def set_regions(self, regions: list[Region]) -> None:
        self._regions = regions
        if self._selected not in {r.id for r in regions}:
            self._selected = -1
        self.update()

    def set_selected(self, region_id: int) -> None:
        self._selected = region_id
        self.update()

    def set_result_image(self, bgr: np.ndarray | None) -> None:
        self._result_pixmap = QPixmap.fromImage(bgr_to_qimage(bgr)) if bgr is not None else None
        self._split = 1.0
        self.update()

    def set_split(self, fraction: float) -> None:
        self._split = max(0.0, min(1.0, float(fraction)))
        self.update()

    def set_mask_overlay(self, rgba: np.ndarray | None) -> None:
        self._mask_pixmap = QPixmap.fromImage(rgba_to_qimage(rgba)) if rgba is not None else None
        self.update()

    def set_show_mask(self, show: bool) -> None:
        self._show_mask = show
        self.update()

    def set_show_region_boxes(self, show: bool) -> None:
        """Hide overlays in the clean 'Result' view."""
        self.show_region_boxes = show
        self.update()

    def fit_to_view(self) -> None:
        self._fit = True
        self._recalc_fit()
        self.update()

    def zoom_to(self, scale: float) -> None:
        if self._pixmap is None:
            return
        self._fit = False
        self._scale = max(0.02, min(8.0, scale))
        self._center()
        self.zoomChanged.emit(self._scale)
        self.update()

    def scale_factor(self) -> float:
        return self._scale

    def selected_region(self) -> Region | None:
        for region in self._regions:
            if region.id == self._selected:
                return region
        return None

    # ------------------------------------------------------------- coordinates

    def _recalc_fit(self) -> None:
        if self._pixmap is None:
            return
        iw, ih = self._pixmap.width(), self._pixmap.height()
        vw, vh = max(1, self.width() - 24), max(1, self.height() - 24)
        self._scale = max(0.02, min(vw / iw, vh / ih, 4.0))
        self._center()
        self.zoomChanged.emit(self._scale)

    def _center(self) -> None:
        if self._pixmap is None:
            return
        iw = self._pixmap.width() * self._scale
        ih = self._pixmap.height() * self._scale
        self._offset = QPointF((self.width() - iw) / 2.0, (self.height() - ih) / 2.0)

    def _to_image(self, pos: QPointF) -> QPointF:
        return QPointF((pos.x() - self._offset.x()) / self._scale,
                       (pos.y() - self._offset.y()) / self._scale)

    def _update_split(self, pos: QPointF) -> None:
        if self._pixmap is None:
            return
        width = self._pixmap.width() * self._scale
        if width <= 0:
            return
        self.set_split((pos.x() - self._offset.x()) / width)

    def _to_screen_rect(self, rect) -> QRectF:
        if isinstance(rect, QRectF):
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        else:
            x, y, w, h = rect
        return QRectF(x * self._scale + self._offset.x(), y * self._scale + self._offset.y(),
                      w * self._scale, h * self._scale)

    def _effective_rect(self, region: Region) -> QRectF:
        if region.id == self._drag_id and self._drag_rect is not None:
            return self._drag_rect
        x, y, w, h = region.bbox
        return QRectF(x, y, w, h)

    # ----------------------------------------------------------------- painting

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt name
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101115"))

        if self._pixmap is None:
            painter.setPen(QColor("#6b7280"))
            painter.setFont(QFont(painter.font().family(), 12))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Select images, then click “Analyze” to start")
            return

        pixmap = self._result_pixmap or self._pixmap
        painter.save()
        painter.translate(self._offset)
        painter.scale(self._scale, self._scale)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self._scale < 1.0)
        if self._result_pixmap is not None and self._split < 0.999:
            # Wipe comparison: original on the right, result on the left.
            # Right-drag inside the canvas moves the divider (no extra widget).
            painter.drawPixmap(0, 0, self._pixmap)
            iw = self._pixmap.width()
            ih = self._pixmap.height()
            painter.save()
            painter.setClipRect(QRectF(0, 0, iw * self._split, ih))
            painter.drawPixmap(0, 0, self._result_pixmap)
            painter.restore()
        else:
            painter.drawPixmap(0, 0, pixmap)
        if self._show_mask and self._mask_pixmap is not None and self._result_pixmap is None:
            painter.setOpacity(0.38)
            painter.drawPixmap(0, 0, self._mask_pixmap)
            painter.setOpacity(1.0)
        painter.restore()

        if (self._result_pixmap is not None and 0.0 < self._split < 0.999
                and self._pixmap is not None):
            x = self._offset.x() + self._pixmap.width() * self._scale * self._split
            top = self._offset.y()
            bottom = top + self._pixmap.height() * self._scale
            painter.setPen(QPen(QColor("#f0a03c"), 2))
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))

        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.show_region_boxes:
            for region in self._regions:
                self._paint_region(painter, region)
        if self.show_region_boxes and self._selected >= 0:
            region = self.selected_region()
            if region is not None:
                self._paint_handles(painter, self._effective_rect(region))

        painter.setPen(QColor("#7c8394"))
        painter.setFont(QFont(painter.font().family(), 9))
        painter.drawText(8, self.height() - 8,
                         f"{int(round(self._scale * 100))}%  ·  scroll to zoom  ·  "
                         "drag to pan  ·  drag a corner to resize")

    def _paint_region(self, painter: QPainter, region: Region) -> None:
        rect = self._to_screen_rect(self._effective_rect(region))
        if rect.isEmpty():
            return
        color = COLORS.get(region.kind, QColor("#c084fc"))
        selected = region.id == self._selected
        width = 2.4 if selected else 1.4

        if region.kind is RegionKind.UNCERTAIN:
            pen = QPen(color, width, Qt.DashLine)
        else:
            pen = QPen(color, width)
        if not region.enabled:
            dim = QColor(color)
            dim.setAlpha(110)
            pen.setColor(dim)
            pen.setStyle(Qt.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

        if selected or self._scale > 0.25:
            label = region.kind.value
            if region.kind is RegionKind.UNCERTAIN:
                label = "needs review"
            if not region.enabled:
                label += " · off"
            label += f"  {region.confidence:.2f}"
            font = QFont(painter.font().family(), 9)
            font.setBold(selected)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(label) + 10
            th = metrics.height() + 2
            chip = QRectF(rect.left(), max(0.0, rect.top() - th - 2), tw, th)
            chip_color = QColor(color)
            chip_color.setAlpha(215)
            painter.setPen(Qt.NoPen)
            painter.setBrush(chip_color)
            painter.drawRoundedRect(chip, 3, 3)
            painter.setPen(QColor("#0d1220"))
            painter.drawText(chip.adjusted(5, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, label)

    def _paint_handles(self, painter: QPainter, rect) -> None:
        screen = self._to_screen_rect(rect)
        if screen.width() < 12 or screen.height() < 12:
            return
        painter.setPen(QPen(QColor("#0d1220"), 1))
        painter.setBrush(QColor("#ffffff"))
        for fx, fy in HANDLES.values():
            cx = screen.left() + fx * screen.width()
            cy = screen.top() + fy * screen.height()
            painter.drawRect(QRectF(cx - 3.5, cy - 3.5, 7, 7))

    # -------------------------------------------------------------------- mouse

    def _handle_at(self, pos: QPointF) -> str | None:
        region = self.selected_region()
        if region is None:
            return None
        screen = self._to_screen_rect(self._effective_rect(region))
        if screen.width() < 12 or screen.height() < 12:
            return None
        for name, (fx, fy) in HANDLES.items():
            cx = screen.left() + fx * screen.width()
            cy = screen.top() + fy * screen.height()
            if abs(pos.x() - cx) <= 8 and abs(pos.y() - cy) <= 8:
                return name
        return None

    def _region_at(self, pos: QPointF) -> Region | None:
        for region in reversed(self._regions):
            if self._to_screen_rect(region.bbox).contains(pos):
                return region
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None:
            return
        pos = event.position()
        if event.button() == Qt.RightButton and self._result_pixmap is not None:
            self._wipe_drag = True
            self._update_split(pos)
            self.setCursor(Qt.SplitHCursor)
            return
        if event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier
        ):
            self._panning = True
            self._pan_start = pos
            self._pan_offset = QPointF(self._offset)
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() != Qt.LeftButton:
            return

        handle = self._handle_at(pos)
        if handle is not None:
            region = self.selected_region()
            if region is not None:
                self._begin_drag(region, pos, handle)
                return

        region = self._region_at(pos)
        if region is not None:
            if region.id != self._selected:
                self._selected = region.id
                self.regionSelected.emit(region.id)
            self._begin_drag(region, pos, None)
            return

        self._selected = -1
        self.regionSelected.emit(-1)
        self.update()

    def _begin_drag(self, region: Region, pos: QPointF, handle: str | None) -> None:
        x, y, w, h = region.bbox
        self._drag_id = region.id
        self._drag_handle = handle
        self._drag_rect = QRectF(x, y, w, h)
        self._drag_origin = QRectF(x, y, w, h)
        self._drag_start = pos
        self.setCursor(CURSORS.get(handle, Qt.SizeAllCursor))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self._wipe_drag:
            self._update_split(pos)
            return
        if self._panning:
            self._offset = self._pan_offset + (pos - self._pan_start)
            self._fit = False
            self.update()
            return

        if self._drag_id >= 0 and self._drag_rect is not None and self._loaded is not None:
            self._update_drag(pos)
            self.update()
            return

        handle = self._handle_at(pos)
        if handle != self._hover_handle:
            self._hover_handle = handle
        if handle is not None:
            self.setCursor(CURSORS[handle])
        elif self._region_at(pos) is not None:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def _update_drag(self, pos: QPointF) -> None:
        assert self._loaded is not None
        iw, ih = self._loaded.width, self._loaded.height
        dx = (pos.x() - self._drag_start.x()) / self._scale
        dy = (pos.y() - self._drag_start.y()) / self._scale
        origin = QRectF(self._drag_origin)
        rect = QRectF(origin)

        if self._drag_handle is None:
            rect.moveTo(origin.left() + dx, origin.top() + dy)
            rect.moveLeft(max(0.0, min(rect.left(), iw - rect.width())))
            rect.moveTop(max(0.0, min(rect.top(), ih - rect.height())))
        else:
            handle = self._drag_handle
            left, top = origin.left(), origin.top()
            right, bottom = origin.right(), origin.bottom()
            if "l" in handle:
                left = min(origin.left() + dx, right - 8)
            if "r" in handle:
                right = max(origin.right() + dx, left + 8)
            if "t" in handle:
                top = min(origin.top() + dy, bottom - 8)
            if "b" in handle:
                bottom = max(origin.bottom() + dy, top + 8)
            left, top = max(0.0, left), max(0.0, top)
            right, bottom = min(float(iw), right), min(float(ih), bottom)
            rect = QRectF(left, top, max(8.0, right - left), max(8.0, bottom - top))

        self._drag_rect = rect

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._wipe_drag and event.button() == Qt.RightButton:
            self._wipe_drag = False
            self.setCursor(Qt.ArrowCursor)
            return
        if self._panning and event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            return
        if event.button() != Qt.LeftButton or self._drag_id < 0:
            return

        region_id = self._drag_id
        rect = self._drag_rect
        moved = rect is not None and (
            abs(rect.left() - self._drag_origin.left()) > 0.5
            or abs(rect.top() - self._drag_origin.top()) > 0.5
            or abs(rect.width() - self._drag_origin.width()) > 0.5
            or abs(rect.height() - self._drag_origin.height()) > 0.5
        )
        self._drag_id = -1
        self._drag_handle = None
        self._drag_rect = None
        self.setCursor(Qt.ArrowCursor)
        if moved and rect is not None:
            bbox = (int(round(rect.left())), int(round(rect.top())),
                    max(1, int(round(rect.width()))), max(1, int(round(rect.height()))))
            self.regionEdited.emit(region_id, bbox)
        else:
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.fit_to_view()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None:
            return
        pos = event.position()
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        image_pt = self._to_image(pos)
        self._scale = max(0.02, min(8.0, self._scale * factor))
        self._offset = QPointF(pos.x() - image_pt.x() * self._scale,
                               pos.y() - image_pt.y() * self._scale)
        self._fit = False
        self.zoomChanged.emit(self._scale)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit:
            self._recalc_fit()

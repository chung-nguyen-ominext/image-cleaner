"""Local per-word character boxes from pixels (column projection).

RapidOCR gives word boxes only, and its optional single-character cells are
estimated from CTC columns (averaged widths, overlapping cells). For substring
matching we need real glyph bounds: they are derived here by thresholding the
word crop against its local background and projecting ink onto the x axis.

This module NEVER grows a box: it validates one connected column group per
character and returns None on any ambiguity, so the caller can fall back to
the conservative UNCERTAIN path.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from src.core.models import Rect

log = logging.getLogger("imagecleaner.char")


def localize_char_boxes(bgr: np.ndarray, box: Rect, text: str) -> list[Rect] | None:
    """Return one Rect per character, or None when segmentation is unreliable."""
    x, y, w, h = box
    if w < 4 or h < 4 or not text or len(text) > 64:
        return None
    hh, ww = bgr.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(ww, x + w), min(hh, y + h)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None

    crop = bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Local background: median of a 2px frame around the word.
    frame = np.concatenate([
        gray[:2].reshape(-1), gray[-2:].reshape(-1),
        gray[:, :2].reshape(-1), gray[:, -2:].reshape(-1),
    ])
    bg = float(np.median(frame))
    diff = np.abs(gray.astype(np.int16) - int(bg)).astype(np.uint8)
    if int(diff.max()) < 12:
        return None

    otsu, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thr = int(np.clip(otsu * 0.65, 8, 70))
    ink = (diff > thr).astype(np.uint8) * 255

    # Drop single-pixel specks (anti-aliasing noise), keep real glyph strokes.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    cleaned = np.zeros_like(ink)
    for i in range(1, count):
        if int(stats[i, cv2.CC_STAT_AREA]) >= 2:
            cleaned[labels == i] = 255
    if int((cleaned > 0).sum()) < max(6, 2 * len(text)):
        return None

    # Strictly contiguous ink columns. Glyphs in normal UI text are separated
    # by at least one blank column; touching glyphs fail validation on purpose.
    projection = (cleaned > 0).sum(axis=0)
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for col, value in enumerate(projection):
        if value > 0 and start is None:
            start = col
        elif value == 0 and start is not None:
            groups.append((start, col))
            start = None
    if start is not None:
        groups.append((start, len(projection)))

    if len(groups) != len(text):
        log.debug("char localization rejected: %d groups for %d chars (%r)",
                  len(groups), len(text), text)
        return None

    out: list[Rect] = []
    for gx1, gx2 in groups:
        ys, _ = np.where(cleaned[:, gx1:gx2] > 0)
        if ys.size == 0:
            return None
        gy1 = int(ys.min())
        gy2 = int(ys.max()) + 1
        # One pixel of slack for anti-aliased edges, clamped to the word box.
        rx1 = max(0, gx1 - 1)
        rx2 = min(cleaned.shape[1], gx2 + 1)
        ry1 = max(0, gy1 - 1)
        ry2 = min(cleaned.shape[0], gy2 + 1)
        out.append((x1 + rx1, y1 + ry1, rx2 - rx1, ry2 - ry1))

    widths = [r[2] for r in out]
    if min(widths) < 1 or max(widths) > w * 0.8:
        return None
    return out

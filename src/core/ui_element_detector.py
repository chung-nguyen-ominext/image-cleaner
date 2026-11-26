"""Heuristic UI element (button/badge) detection around a matched text box.

No coordinates or sizes are hardcoded: candidates are proposed by several
independent CV strategies and scored by evidence. When evidence is weak the
caller must treat the region as UNCERTAIN and ask the user.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from src.core.models import Rect, clip_rect, expand_rect, rect_area, rect_contains
log = logging.getLogger("imagecleaner.elements")


@dataclass
class ElementCandidate:
    bbox: Rect
    confidence: float
    evidence: dict[str, float] = field(default_factory=dict)
    source: str = "contour"


def _odd(n: int, minimum: int = 3) -> int:
    n = max(minimum, int(n))
    return n if n % 2 == 1 else n + 1


def _edge_map(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    med = float(np.median(blur))
    lo = int(max(8, 0.55 * med))
    hi = int(min(255, max(lo + 25, 1.45 * med)))
    edges = cv2.Canny(blur, lo, hi)
    return cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)


def _perimeter_evidence(gray: np.ndarray, rect: Rect, band: int = 2) -> tuple[float, float]:
    """Return (mean border gradient 0..1, fraction of border covered by edges 0..1).

    Candidate bboxes are frequently 1-2 px off the true outline (Canny dilation,
    morph ops), so each perimeter position is sampled across a +/-band window and
    the strongest response is kept.
    """
    x, y, w, h = rect
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)

    h_img, w_img = gray.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(w_img, x + w), min(h_img, y + h)
    if x2 - x1 < 3 or y2 - y1 < 3:
        return 0.0, 0.0

    samples: list[float] = []
    step = max(1, (x2 - x1) // 80)
    for xx in range(x1, x2, step):
        for yy in (y1, y2 - 1):
            lo = max(0, yy - band)
            hi = min(h_img, yy + band + 1)
            seg = mag[lo:hi, xx]
            if seg.size:
                samples.append(float(seg.max()))
    step = max(1, (y2 - y1) // 80)
    for yy in range(y1, y2, step):
        for xx in (x1, x2 - 1):
            lo = max(0, xx - band)
            hi = min(w_img, xx + band + 1)
            seg = mag[yy, lo:hi]
            if seg.size:
                samples.append(float(seg.max()))

    if not samples:
        return 0.0, 0.0
    arr = np.asarray(samples, dtype=np.float32)
    strong = arr[arr >= np.percentile(arr, 40)]
    mean_grad = float(strong.mean()) if strong.size else float(arr.mean())
    coverage = float((arr > 30.0).mean())
    return min(1.0, mean_grad / 120.0), coverage


def _interior_uniformity(gray: np.ndarray, rect: Rect, text_rect: Rect) -> float:
    x, y, w, h = rect
    roi = gray[max(0, y):y + h, max(0, x):x + w]
    if roi.size == 0:
        return 0.0
    mask = np.ones(roi.shape, dtype=np.uint8) * 255
    tx, ty, tw, th = text_rect
    px1, py1 = max(0, tx - x - 3), max(0, ty - y - 3)
    px2 = min(roi.shape[1], tx - x + tw + 3)
    py2 = min(roi.shape[0], ty - y + th + 3)
    if px2 > px1 and py2 > py1:
        mask[py1:py2, px1:px2] = 0
    vals = roi[mask > 0]
    if vals.size < 16:
        return 0.5
    std = float(vals.std())
    return float(np.clip(1.0 - std / 45.0, 0.0, 1.0))


def _rect_ratio_score(ratio: float) -> float:
    if ratio < 1.05:
        return 0.0
    return float(math.exp(-0.5 * (math.log(ratio / 2.2) / 0.62) ** 2))


def _margin_score(margin: int, text_h: int) -> float:
    ideal = max(2.0, 0.25 * text_h)
    span = max(6.0, 1.1 * text_h)
    return float(np.clip(1.0 - abs(margin - ideal) / span, 0.0, 1.0))


class UIElementDetector:
    """Proposes the enclosing UI element for a text box and scores the evidence."""

    def detect(
        self,
        bgr: np.ndarray,
        text_box: Rect,
        match_line_boxes: list[Rect],
        other_line_boxes: list[Rect],
        options,
    ) -> ElementCandidate | None:
        if options.element_policy == "text_only":
            return None

        h_img, w_img = bgr.shape[:2]
        text_box = clip_rect(text_box, w_img, h_img)
        if rect_area(text_box) <= 0:
            return None

        text_h = max(6, text_box[3])
        pad_y = int(np.clip(2.4 * text_h, 12, 110))
        pad_x = int(np.clip(max(3.0 * text_h, 0.7 * text_box[2]), 16, 180))

        last: ElementCandidate | None = None
        # A widget can be wider than the window suggested by the text (a word is
        # narrow, a pill is not). Grow the window while the best candidate is cut
        # off by it, so a truncated widget is never half-removed.
        for grow in (1.0, 2.0, 4.0):
            px, py = int(pad_x * grow), int(pad_y * grow)
            roi_rect = clip_rect(
                (text_box[0] - px, text_box[1] - py,
                 text_box[2] + 2 * px, text_box[3] + 2 * py),
                w_img, h_img,
            )
            cand = self._detect_in_window(bgr, text_box, other_line_boxes, roi_rect, text_h)
            if cand is None:
                return last
            last = cand
            if not self._touches_window(cand.bbox, roi_rect, w_img, h_img):
                return cand
            log.debug("element %s truncated by analysis window %s; growing", cand.bbox, roi_rect)
        return last

    def _detect_in_window(
        self,
        bgr: np.ndarray,
        text_box: Rect,
        other_line_boxes: list[Rect],
        roi_rect: Rect,
        text_h: int,
    ) -> ElementCandidate | None:
        rx, ry, rw, rh = roi_rect
        roi = bgr[ry:ry + rh, rx:rx + rw]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        rel_text = (text_box[0] - rx, text_box[1] - ry, text_box[2], text_box[3])

        candidates: list[ElementCandidate] = []
        candidates.extend(self._contour_candidates(roi, gray, rel_text, text_box, roi_rect))
        candidates.extend(self._color_candidates(roi, gray, rel_text, text_box, roi_rect))
        candidates.extend(self._flood_candidates(roi, gray, rel_text, text_box, roi_rect))
        candidates.extend(self._snap_candidates(gray, rel_text, text_box, roi_rect))

        if not candidates:
            return None

        # Merge near-duplicates, keep the best-scoring representative.
        candidates.sort(key=lambda c: -c.confidence)
        merged: list[ElementCandidate] = []
        for cand in candidates:
            if any(self._iou(cand.bbox, m.bbox) > 0.82 for m in merged):
                continue
            merged.append(cand)

        # Ring bonus: a thin closed frame enclosing another candidate (annotation/highlight/border).
        for cand in merged:
            for other in merged:
                if other is cand:
                    continue
                if rect_contains(cand.bbox, other.bbox):
                    gap = min(
                        other.bbox[0] - cand.bbox[0],
                        other.bbox[1] - cand.bbox[1],
                        cand.bbox[0] + cand.bbox[2] - (other.bbox[0] + other.bbox[2]),
                        cand.bbox[1] + cand.bbox[3] - (other.bbox[1] + other.bbox[3]),
                    )
                    if 0 <= gap <= max(6, int(0.5 * text_h)) and \
                            rect_area(cand.bbox) <= 3.5 * max(1, rect_area(other.bbox)):
                        cand.evidence["ring"] = 1.0
                        cand.confidence = min(1.0, cand.confidence + 0.06)
                        break
        merged.sort(key=lambda c: -c.confidence)

        # Walk down the ranking: the top candidate may swallow unrelated text,
        # but a smaller (still valid) candidate can be the real widget.
        for best in merged:
            if self._swallows_other_text(best.bbox, other_line_boxes):
                log.debug("reject element %s: contains unrelated text", best.bbox)
                continue

            # Contour bboxes are offset by Canny dilation; snap each side to the real edge.
            # A colour component already gives exact bounds, so leave it untouched.
            if best.source != "color":
                best.bbox = self._refine_bbox(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), best.bbox)

            best.evidence = {k: round(float(v), 3) for k, v in best.evidence.items()}
            best.confidence = round(float(np.clip(best.confidence, 0.0, 1.0)), 3)
            log.debug("element candidate bbox=%s conf=%.3f src=%s evidence=%s",
                      best.bbox, best.confidence, best.source, best.evidence)
            return best
        return None

    # ------------------------------------------------------------------ candidates

    def _contour_candidates(self, roi, gray, rel_text, text_box, roi_rect) -> list[ElementCandidate]:
        edges = _edge_map(gray)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        out: list[ElementCandidate] = []
        h_img, w_img = gray.shape[:2]

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < 8 or h < 6:
                continue
            area = float(cv2.contourArea(cnt))
            if area <= 0:
                continue
            bbox_rel = (x, y, w, h)
            if not rect_contains(bbox_rel, rel_text, margin=1):
                continue
            ratio = (w * h) / max(1, text_box[2] * text_box[3])
            if ratio < 1.05 or ratio > 14:
                continue
            if w * h > 0.35 * float(w_img * h_img):
                continue
            abs_bbox = (x + roi_rect[0], y + roi_rect[1], w, h)
            if abs_bbox[2] <= text_box[2] * 1.02 and abs_bbox[3] <= text_box[3] * 1.02:
                continue

            fill = float(np.clip(area / (w * h), 0.0, 1.0))
            border, coverage = _perimeter_evidence(gray, bbox_rel)
            uniform = _interior_uniformity(gray, bbox_rel, rel_text)
            margin = min(
                rel_text[0] - x,
                rel_text[1] - y,
                x + w - (rel_text[0] + rel_text[2]),
                y + h - (rel_text[1] + rel_text[3]),
            )
            ev = {
                "shape": fill,
                "closed": coverage,
                "border": border,
                "ratio": _rect_ratio_score(ratio),
                "margin": _margin_score(margin, text_box[3]),
                "uniform": uniform,
            }
            conf = (
                0.30 * ev["shape"]
                + 0.25 * ev["closed"]
                + 0.20 * ev["border"]
                + 0.10 * ev["ratio"]
                + 0.08 * ev["margin"]
                + 0.07 * ev["uniform"]
            )
            ev["area_ratio"] = ratio
            out.append(ElementCandidate(abs_bbox, float(conf), ev, "contour"))
        return out

    def _color_candidates(self, roi, gray, rel_text, text_box, roi_rect) -> list[ElementCandidate]:
        """Badges/buttons with a saturated fill or outline (exact bounds, no dilation)."""
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        sat, val = hsv[:, :, 1], hsv[:, :, 2]
        colored = ((sat > 70) & (val > 50)).astype(np.uint8)
        if int(colored.sum()) < 20:
            return []
        colored = cv2.morphologyEx(colored, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(colored, connectivity=8)

        out: list[ElementCandidate] = []
        text_area = max(1, text_box[2] * text_box[3])
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < max(12, 0.25 * text_area):
                continue
            x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                          int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
            if w < 8 or h < 6:
                continue
            if not rect_contains((x, y, w, h), rel_text, margin=2):
                continue
            ratio = (w * h) / text_area
            if ratio < 1.05 or ratio > 14:
                continue
            if w * h > 0.35 * float(gray.shape[0] * gray.shape[1]):
                continue

            border, coverage = _perimeter_evidence(gray, (x, y, w, h))
            margin = min(rel_text[0] - x, rel_text[1] - y,
                         x + w - (rel_text[0] + rel_text[2]), y + h - (rel_text[1] + rel_text[3]))
            ev = {
                "shape": 0.95,
                "closed": coverage,
                "border": border,
                "ratio": _rect_ratio_score(ratio),
                "margin": _margin_score(margin, text_box[3]),
                "uniform": _interior_uniformity(gray, (x, y, w, h), rel_text),
                "area_ratio": ratio,
            }
            conf = (
                0.30 * ev["shape"] + 0.25 * ev["closed"] + 0.20 * ev["border"]
                + 0.10 * ev["ratio"] + 0.08 * ev["margin"] + 0.07 * ev["uniform"]
            )
            # Raw colour components have pixel-exact bounds (no morphology offset).
            conf += 0.06
            abs_bbox = (x + roi_rect[0], y + roi_rect[1], w, h)
            out.append(ElementCandidate(abs_bbox, float(conf), ev, "color"))
        return out

    def _flood_candidates(self, roi, gray, rel_text, text_box, roi_rect) -> list[ElementCandidate]:
        """Homogeneous filled shapes around the text (pills, filled buttons).

        Uses a hard colour band around the seed instead of OpenCV floodFill:
        floodFill follows smooth gradients and leaks from a pill into the page
        background (especially in dark mode), producing a whole-screen box.
        """
        h, w = gray.shape[:2]
        x, y, tw, th = rel_text
        gap = max(3, th // 5)
        seeds = [
            (x - gap, y + th // 2),
            (x + tw + gap, y + th // 2),
            (x + tw // 2, y - gap),
            (x + tw // 2, y + th + gap),
        ]
        blur = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.int16)
        out: list[ElementCandidate] = []
        seen: list[Rect] = []
        roi_area = float(h * w)
        text_area = max(1, text_box[2] * text_box[3])

        for sx, sy in seeds:
            if not (0 <= sx < w and 0 <= sy < h):
                continue
            seed_val = int(blur[sy, sx])
            band = (np.abs(blur - seed_val) <= 12).astype(np.uint8)
            n, labels, stats, _ = cv2.connectedComponentsWithStats(band, connectivity=4)
            label = int(labels[sy, sx])
            if label == 0:
                continue
            area = int(stats[label, cv2.CC_STAT_AREA])
            # Too small to be a widget, or so large it is the page background.
            if area < max(20, 0.2 * text_area) or area > 0.40 * roi_area:
                continue

            comp = labels == label
            ys, xs = np.where(comp)
            bx, by = int(xs.min()), int(ys.min())
            bw, bh = int(xs.max() - bx + 1), int(ys.max() - by + 1)
            # A background region runs off the ROI on several sides.
            touch = int(bx <= 1) + int(by <= 1) + int(bx + bw >= w - 1) + int(by + bh >= h - 1)
            if touch >= 2:
                continue
            if not rect_contains((bx, by, bw, bh), rel_text, margin=1):
                continue
            ratio = (bw * bh) / text_area
            if ratio < 1.1 or ratio > 12:
                continue

            # Score the padded box so the shape/border evidence sees the real outline.
            ex = max(0, bx - 2)
            ey = max(0, by - 2)
            ew = min(w - ex, bw + 4)
            eh = min(h - ey, bh + 4)
            border, coverage = _perimeter_evidence(gray, (ex, ey, ew, eh))
            fill = float(np.clip(area / max(1, bw * bh), 0.0, 1.0))
            top = comp[by, bx:bx + bw].mean()
            bottom = comp[by + bh - 1, bx:bx + bw].mean()
            left = comp[by:by + bh, bx].mean()
            right = comp[by:by + bh, bx + bw - 1].mean()
            geom = float(np.mean([top, bottom, left, right]))
            ev = {
                "shape": fill,
                "closed": geom,
                "border": border,
                "ratio": _rect_ratio_score(ratio),
                "uniform": _interior_uniformity(gray, (bx, by, bw, bh), rel_text),
                "area_ratio": ratio,
            }
            conf = (0.30 * ev["shape"] + 0.25 * ev["closed"] + 0.15 * ev["border"]
                    + 0.15 * ev["ratio"] + 0.15 * ev["uniform"])
            abs_bbox = (ex + roi_rect[0], ey + roi_rect[1], ew, eh)
            if any(self._iou(abs_bbox, s) > 0.8 for s in seen):
                continue
            seen.append(abs_bbox)
            out.append(ElementCandidate(abs_bbox, float(conf), ev, "flood"))
        return out

    def _snap_candidates(self, gray, rel_text, text_box, roi_rect) -> list[ElementCandidate]:
        """Find the nearest strong straight edge on each side of the text."""
        h, w = gray.shape[:2]
        x, y, tw, th = rel_text
        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        limit = max(8, int(1.8 * th))

        def scan(profile: np.ndarray, start: int, stop: int) -> int | None:
            lo, hi = sorted((max(0, start), min(len(profile), stop)))
            if hi - lo < 3:
                return None
            seg = profile[lo:hi]
            idx = int(np.argmax(seg))
            if float(seg[idx]) < 45.0:
                return None
            return lo + idx

        ys = slice(max(0, y), min(h, y + th))
        xs = slice(max(0, x), min(w, x + tw))
        # Vertical edges (left/right) -> horizontal gradient; horizontal edges -> vertical gradient.
        col_profile = gx[ys, :].mean(axis=0)
        row_profile = gy[:, xs].mean(axis=1)
        left = scan(col_profile, x - limit, x + 1)
        right = scan(col_profile, x + tw - 1, x + tw + limit)
        top = scan(row_profile, y - limit, y + 1)
        bottom = scan(row_profile, y + th - 1, y + th + limit)
        if None in (left, right, top, bottom):
            return []
        bw, bh = right - left + 1, bottom - top + 1
        if bw < text_box[2] * 1.05 or bh < text_box[3] * 1.05:
            return []
        abs_bbox = (left + roi_rect[0], top + roi_rect[1], bw, bh)
        border, coverage = _perimeter_evidence(gray, (left, top, bw, bh))
        if coverage < 0.55:
            # Not a real closed widget, just accidental edges around the text.
            return []
        ratio = (bw * bh) / max(1, text_box[2] * text_box[3])
        ev = {
            "shape": 0.8, "closed": coverage, "border": border,
            "ratio": _rect_ratio_score(ratio), "area_ratio": ratio,
        }
        conf = 0.30 * 0.8 + 0.25 * coverage + 0.20 * border + 0.15 * ev["ratio"]
        return [ElementCandidate(abs_bbox, float(conf), ev, "snap")]

    # -------------------------------------------------------------------- guards

    @staticmethod
    def _touches_window(bbox: Rect, win: Rect, w_img: int, h_img: int, tol: int = 2) -> bool:
        """True when a bbox side is cut by the analysis window (not by the image edge)."""
        x, y, w, h = bbox
        wx, wy, ww, wh = win
        if wx > 0 and x - wx <= tol:
            return True
        if wy > 0 and y - wy <= tol:
            return True
        if wx + ww < w_img and (wx + ww) - (x + w) <= tol:
            return True
        if wy + wh < h_img and (wy + wh) - (y + h) <= tol:
            return True
        return False

    @staticmethod
    def _refine_bbox(gray: np.ndarray, bbox: Rect, search: int = 4) -> Rect:
        """Move each side onto the strongest nearby gradient (removes dilation offset)."""
        h_img, w_img = gray.shape[:2]
        x, y, w, h = bbox
        x, y = max(0, x), max(0, y)
        w, h = min(w, w_img - x), min(h, h_img - y)
        if w < 4 or h < 4:
            return (x, y, w, h)

        gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))

        def best(profile: np.ndarray, center: int, lo: int, hi: int) -> int:
            lo, hi = max(0, lo), min(len(profile) - 1, hi)
            if hi <= lo:
                return center
            seg = profile[lo:hi + 1]
            idx = int(np.argmax(seg))
            if float(seg[idx]) < 20.0:
                return center
            return lo + idx

        rows = slice(y, y + h)
        cols = slice(x, x + w)
        col_prof = gx[rows, :].mean(axis=0)
        row_prof = gy[:, cols].mean(axis=1)

        nx1 = best(col_prof, x, x - search, x + search)
        nx2 = best(col_prof, x + w - 1, x + w - 1 - search, x + w - 1 + search)
        ny1 = best(row_prof, y, y - search, y + search)
        ny2 = best(row_prof, y + h - 1, y + h - 1 - search, y + h - 1 + search)

        nx1, nx2 = min(nx1, nx2), max(nx1, nx2)
        ny1, ny2 = min(ny1, ny2), max(ny1, ny2)
        return (nx1, ny1, max(1, nx2 - nx1 + 1), max(1, ny2 - ny1 + 1))

    @staticmethod
    def _iou(a: Rect, b: Rect) -> float:
        ax2, ay2 = a[0] + a[2], a[1] + a[3]
        bx2, by2 = b[0] + b[2], b[1] + b[3]
        ix = max(0, min(ax2, bx2) - max(a[0], b[0]))
        iy = max(0, min(ay2, by2) - max(a[1], b[1]))
        inter = ix * iy
        if inter <= 0:
            return 0.0
        return inter / float(rect_area(a) + rect_area(b) - inter)

    @staticmethod
    def _swallows_other_text(bbox: Rect, other_boxes: list[Rect]) -> bool:
        """Reject a candidate that would erase unrelated text.

        Thresholds are deliberately low (15% of a line's area, or a quarter of
        its height/width): deleting a panel that merely clips a neighbour's
        descenders is worse than treating the keyword as standalone text.
        """
        for ob in other_boxes:
            area = rect_area(ob)
            if area <= 0:
                continue
            ix = max(0, min(bbox[0] + bbox[2], ob[0] + ob[2]) - max(bbox[0], ob[0]))
            iy = max(0, min(bbox[1] + bbox[3], ob[1] + ob[3]) - max(bbox[1], ob[1]))
            if ix <= 0 or iy <= 0:
                continue
            # Only a box that is mostly *inside* the candidate is really being
            # swallowed. An icon read as text often overlaps a button corner
            # while most of its ink stays outside; rejecting the frame there
            # loses the button entirely (Phase 8 R3).
            inside = (ix * iy) / area
            if inside <= 0.55:
                continue
            if inside > 0.15:
                return True
            if iy / ob[3] > 0.25 or ix / ob[2] > 0.25:
                return True
        return False

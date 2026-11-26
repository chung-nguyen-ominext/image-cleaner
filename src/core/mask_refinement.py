"""Post-detection mask refinement: keep objects that are not the target.

Detection is intentionally left untouched. This module takes the region/mask
produced by the pipeline and removes pixels that belong to protected objects
(other OCR text, compact dark icons). Red annotation connected to the target
stays removable. Hidden parts of a roughly circular icon are reconstructed by
symmetry from its visible half; there is no colour guessing or blind dilation.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.core.mask_generator import build_region_mask, region_is_element
from src.core.models import (
    Region, RegionKind, TextLine, expand_rect, rect_area, rect_intersection_area,
)
from src.config.settings import ProcessOptions


@dataclass
class RefinedMask:
    mask: np.ndarray
    protected: np.ndarray
    restore_pixels: np.ndarray            # bool: overwrite after composite
    restore_values: np.ndarray            # BGR values for those pixels
    note: str = ""


def _other_text_mask(lines: list[TextLine], region: Region, shape) -> np.ndarray:
    out = np.zeros(shape, np.uint8)
    if region.match is None:
        return out
    own = set(region.match.line_ids)
    h, w = shape[:2]
    for i, line in enumerate(lines):
        if i in own:
            continue
        x, y, bw, bh = line.box
        x1, y1 = max(0, x - 2), max(0, y - 2)
        x2, y2 = min(w, x + bw + 2), min(h, y + bh + 2)
        out[y1:y2, x1:x2] = 1
    return out


def _own_text_mask(lines: list[TextLine], region: Region, shape) -> np.ndarray:
    """Text belonging to the matched line itself.

    For an annotated text line the whole line is the target: its other words
    (`Add`, `Configuration...`) must not be mistaken for icons and protected.
    """
    out = np.zeros(shape, np.uint8)
    if region.match is None:
        return out
    own = set(region.match.line_ids)
    h, w = shape[:2]
    for i, line in enumerate(lines):
        if i not in own:
            continue
        x, y, bw, bh = line.box
        x1, y1 = max(0, x - 2), max(0, y - 2)
        x2, y2 = min(w, x + bw + 2), min(h, y + bh + 2)
        out[y1:y2, x1:x2] = 1
    return out



def _match_inside_region(region: Region) -> bool:
    """Guard against stale/scaled OCR boxes (e.g. a test double mismatch)."""
    if region.match is None:
        return True
    area = rect_area(region.match.box)
    if area <= 0:
        return False
    return rect_intersection_area(region.match.box, region.bbox) >= 0.5 * area


def _compact_dark_components(bgr: np.ndarray, region: Region,
                             other_text: np.ndarray,
                             own_text: np.ndarray | None = None) -> tuple[np.ndarray, tuple | None]:
    """Compact dark blobs (icons) outside the matched text box."""
    if not _match_inside_region(region):
        return np.zeros(bgr.shape[:2], np.uint8), None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    low_sat = hsv[:, :, 1] < 70
    dark = (gray < 95) & low_sat
    text_h = region.match.box[3] if region.match is not None else max(12, region.bbox[3] // 4)

    if region.match is not None:
        mx, my, mw, mh = region.match.box
        ex = expand_rect((mx, my, mw, mh), 5, bgr.shape[1], bgr.shape[0])
        dark[ex[1]:ex[1] + ex[3], ex[0]:ex[0] + ex[2]] = False
    dark[other_text > 0] = False
    # Words on the matched line are target content, not icons to preserve.
    if own_text is not None:
        dark[own_text > 0] = False

    bx, by, bw, bh = region.bbox
    scope = np.zeros(gray.shape, np.uint8)
    # Icons can sit just outside the detected bbox (the button overlaps them).
    x1, y1 = max(0, bx - 100), max(0, by - 60)
    x2, y2 = min(bgr.shape[1], bx + bw + 100), min(bgr.shape[0], by + bh + 60)
    scope[y1:y2, x1:x2] = 1

    # No closing here: it merges the icon with the nearby button frame.
    closed = (dark & (scope > 0)).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, 8)

    protected = np.zeros(gray.shape, np.uint8)
    icon = None
    region_area = max(1, bw * bh)
    max_icon_area = max(4000, 8 * region_area)
    for i in range(1, count):
        area = int(stats[i, cv2.CC_STAT_AREA])
        cw, ch = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        if area < 40 or not (5 <= cw <= 160 and 5 <= ch <= 160):
            continue
        # A dark page/card fill is a huge component, never an icon.
        if area > max_icon_area:
            continue
        # A glyph is about one text line tall; an icon is visibly larger.
        if max(cw, ch) < 1.2 * text_h:
            continue
        aspect = cw / max(1, ch)
        if not (0.40 <= aspect <= 2.5):
            continue
        comp = (labels == i)
        if comp.sum() < 0.25 * cw * ch:
            continue
        cx, cy = float(centroids[i][0]), float(centroids[i][1])
        protected[comp] = 1
        radius = 0.5 * max(cw, ch)
        if icon is None or area > icon[2]:
            icon = (cx, cy, area, radius)
    if icon is not None:
        cx, cy, _, radius = icon
        # An icon is usually several dark components (arcs + centre dot).
        # Adopt neighbouring components around the compact core into the same
        # protected object; a distant button frame is not adopted.
        for i in range(1, count):
            px, py = float(centroids[i][0]), float(centroids[i][1])
            if (px - cx) ** 2 + (py - cy) ** 2 <= (radius + 15) ** 2:
                protected[labels == i] = 1
        # Thin arcs may not survive connected-component filtering; protect all
        # dark pixels inside the icon's radius as well.
        gy, gx = np.ogrid[:dark.shape[0], :dark.shape[1]]
        protected |= dark & (((gx - cx) ** 2 + (gy - cy) ** 2) <= (radius + 20) ** 2)
    if protected.any():
        protected = cv2.dilate(protected,
                               cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    return protected, icon


def _solid_compact_objects(bgr: np.ndarray, region: Region,
                           other_text: np.ndarray,
                           own_text: np.ndarray | None = None) -> tuple[np.ndarray, tuple | None]:
    """Solid icons/objects that differ from the button fill, in any position.

    The dark-component detector above handles outlined gear-like icons. This
    second pass is colour/position agnostic: it samples the region fill, finds
    compact solid components that differ from it, and protects those that
    intersect the target area. It deliberately requires a high fill ratio so a
    thin button border or a page background ring is not mistaken for an icon.
    """
    h_img, w_img = bgr.shape[:2]
    bx, by, bw, bh = region.bbox
    if bw <= 0 or bh <= 0 or not _match_inside_region(region):
        return np.zeros((h_img, w_img), np.uint8), None

    text_h = region.match.box[3] if region.match is not None else max(12, bh // 4)
    scope = expand_rect((bx, by, bw, bh), max(24, int(2.5 * text_h)), w_img, h_img)
    sx, sy, sw, sh = scope
    roi = bgr[sy:sy + sh, sx:sx + sw]

    # Estimate the button/container fill from the target box only. Including
    # the surrounding page in this sample would make a dark button look white.
    zone = bgr[by:by + bh, bx:bx + bw]
    zone_inside = np.ones(zone.shape[:2], bool)
    if region.match is not None:
        mx, my, mw, mh = expand_rect(region.match.box, 4, w_img, h_img)
        zx1, zy1 = max(0, mx - bx), max(0, my - by)
        zx2, zy2 = min(bw, mx - bx + mw), min(bh, my - by + mh)
        if zx2 > zx1 and zy2 > zy1:
            zone_inside[zy1:zy2, zx1:zx2] = False
    zone_inside[other_text[by:by + bh, bx:bx + bw] > 0] = False
    if own_text is not None:
        zone_inside[own_text[by:by + bh, bx:bx + bw] > 0] = False
    pixels = zone[zone_inside]
    if pixels.shape[0] < 64:
        return np.zeros((h_img, w_img), np.uint8), None

    fill = np.median(pixels, axis=0)
    diff = (np.max(np.abs(roi.astype(np.int16) - fill.astype(np.int16)), axis=2) > 28)
    diff[other_text[sy:sy + sh, sx:sx + sw] > 0] = False
    if own_text is not None:
        diff[own_text[sy:sy + sh, sx:sx + sw] > 0] = False
    if region.match is not None:
        px1, py1 = max(0, mx - sx), max(0, my - sy)
        px2, py2 = min(sw, mx - sx + mw), min(sh, my - sy + mh)
        if px2 > px1 and py2 > py1:
            diff[py1:py2, px1:px2] = False

    protect_zone = expand_rect((bx, by, bw, bh), max(6, int(0.35 * text_h)), w_img, h_img)
    zx1, zy1, zx2, zy2 = (protect_zone[0], protect_zone[1],
                          protect_zone[0] + protect_zone[2],
                          protect_zone[1] + protect_zone[3])

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        diff.astype(np.uint8), 8
    )
    region_area = max(1, bw * bh)
    min_area = max(24, int(0.002 * region_area))
    protected = np.zeros((h_img, w_img), np.uint8)
    icon = None

    for i in range(1, count):
        area = int(stats[i, cv2.CC_STAT_AREA])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        if area < min_area or area > 0.35 * region_area:
            continue
        if cw < 5 or ch < 5:
            continue
        if max(cw, ch) < 1.2 * text_h:
            continue
        aspect = cw / max(1, ch)
        if not (0.35 <= aspect <= 2.8):
            continue
        if area < 0.45 * cw * ch:
            continue

        ax = int(stats[i, cv2.CC_STAT_LEFT]) + sx
        ay = int(stats[i, cv2.CC_STAT_TOP]) + sy
        if ax + cw <= zx1 or ax >= zx2 or ay + ch <= zy1 or ay >= zy2:
            continue

        comp = labels == i
        ys, xs = np.where(comp)
        protected[ys + sy, xs + sx] = 1
        if icon is None or area > icon[2]:
            icon = (float(centroids[i][0]) + sx, float(centroids[i][1]) + sy,
                    area, 0.5 * max(cw, ch))

    if protected.any():
        protected = cv2.dilate(
            protected, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        ) > 0
    return protected, icon


def _red_annotation(bgr: np.ndarray, region: Region,
                    base_mask: np.ndarray | None = None,
                    protected: np.ndarray | None = None) -> np.ndarray:
    h_img, w_img = bgr.shape[:2]
    bx, by, bw, bh = region.bbox
    text_h = region.match.box[3] if region.match is not None else max(12, bh // 4)
    margin = max(24, int(3.0 * text_h))
    x1, y1 = max(0, bx - margin), max(0, by - margin)
    x2, y2 = min(w_img, bx + bw + margin), min(h_img, by + bh + margin)
    crop = bgr[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168))
           & (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 60))

    # Only red connected to the target object is part of the annotation.
    # A red badge merely near the button must survive.
    if base_mask is not None:
        target = cv2.dilate(
            (base_mask[y1:y2, x1:x2] > 0).astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
        ) > 0
    else:
        target = np.ones(red.shape, bool)
    protected_crop = protected[y1:y2, x1:x2] if protected is not None else None

    count, labels, stats, _ = cv2.connectedComponentsWithStats(red.astype(np.uint8), 8)
    include = np.zeros_like(red)
    # A red annotation may sit outside the detected widget (a strike-through
    # crossing an icon), but a frame spanning the whole row is a different
    # annotation: its span is far larger than the target in at least one axis.
    tol = max(4, int(0.6 * text_h))
    max_w = max(1.5 * bw, bw + 2 * tol)
    max_h = max(1.5 * bh, bh + 2 * tol)
    for i in range(1, count):
        comp = labels == i
        cbw = int(stats[i, cv2.CC_STAT_WIDTH])
        cbh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if cbw > max_w or cbh > max_h:
            continue
        # A coloured icon must not be re-added as an annotation.
        if protected_crop is not None:
            overlap = int((comp & protected_crop).sum())
            if overlap > 0.5 * int(comp.sum()):
                continue
        if not target[comp].any():
            continue
        include[comp] = True

    out = np.zeros((h_img, w_img), np.uint8)
    out[y1:y2, x1:x2] = include.astype(np.uint8)
    return out


def _symmetric_icon_source(bgr: np.ndarray, mask: np.ndarray,
                           protected: np.ndarray, icon) -> tuple[np.ndarray, np.ndarray]:
    """Reconstruct icon pixels hidden under an annotation from a mirror axis.

    I5: instead of blindly reflecting through the centroid, estimate the icon's
    principal axes (PCA) and only use an axis whose mirror residual is high.
    An asymmetric logo therefore produces no fabricated ghost; the engine
    reconstructs the hidden area instead.
    """
    pixels = np.zeros(mask.shape, bool)
    values = np.zeros_like(bgr)
    if icon is None:
        return pixels, values
    cx, cy, _, radius = icon
    h, w = mask.shape[:2]

    gy, gx = np.ogrid[:h, :w]
    circle = ((gx - cx) ** 2 + (gy - cy) ** 2) <= (radius + 4) ** 2
    visible = protected.astype(bool) & ~mask & circle
    hidden = mask.astype(bool) & circle
    if int(visible.sum()) < 24:
        return pixels, values

    vy, vx = np.where(visible)
    pts = np.column_stack([vx.astype(np.float32), vy.astype(np.float32)])
    center = np.array([cx, cy], np.float32)
    cov = np.cov((pts - center).T) if pts.shape[0] > 2 else np.eye(2)
    try:
        eigvals, eigvecs = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        eigvecs = np.eye(2, dtype=np.float32)
    axes = [eigvecs[:, 0], eigvecs[:, 1]]

    valid = cv2.dilate((visible | hidden).astype(np.uint8),
                       np.ones((5, 5), np.uint8)) > 0

    def reflect(points: np.ndarray, axis: np.ndarray) -> np.ndarray:
        v = axis / max(1e-6, float(np.linalg.norm(axis)))
        d = points - center
        proj = d @ v
        return center + 2.0 * proj[:, None] * v[None, :] - d

    best_axis = None
    best_score = -1.0
    best_reconstruct = 0.0
    for axis in axes:
        mirrored = reflect(pts, axis)
        mx = np.clip(np.round(mirrored[:, 0]).astype(int), 0, w - 1)
        my = np.clip(np.round(mirrored[:, 1]).astype(int), 0, h - 1)
        valid_frac = float(valid[my, mx].mean()) if mx.size else 0.0

        hy, hx = np.where(hidden)
        if hx.size:
            hp = np.column_stack([hx.astype(np.float32), hy.astype(np.float32)])
            hm = reflect(hp, axis)
            hmx = np.clip(np.round(hm[:, 0]).astype(int), 0, w - 1)
            hmy = np.clip(np.round(hm[:, 1]).astype(int), 0, h - 1)
            reconstruct = float(visible[hmy, hmx].mean())
        else:
            reconstruct = 0.0
        score = 0.6 * valid_frac + 0.4 * reconstruct
        if score > best_score:
            best_score, best_axis = score, axis
            best_reconstruct = reconstruct

    # Both the visible-half self-symmetry and the hidden-half explanation must
    # be strong; otherwise leave the hidden area to the engine (no ghost).
    if best_axis is None or best_score < 0.75 or best_reconstruct < 0.40:
        return pixels, values

    hy, hx = np.where(hidden)
    if hx.size == 0:
        return pixels, values
    hp = np.column_stack([hx.astype(np.float32), hy.astype(np.float32)])
    hm = reflect(hp, best_axis)
    hmx = np.clip(np.round(hm[:, 0]).astype(int), 0, w - 1)
    hmy = np.clip(np.round(hm[:, 1]).astype(int), 0, h - 1)
    for y, x, my, mx in zip(hy, hx, hmy, hmx):
        if mask[my, mx] or not protected[my, mx]:
            continue
        if int(bgr[my, mx].max()) > 160:
            continue
        pixels[y, x] = True
        values[y, x] = bgr[my, mx]
    return pixels, values


def refine_element_mask(bgr: np.ndarray, region: Region, lines: list[TextLine],
                        options: ProcessOptions) -> RefinedMask:
    """Refine one ELEMENT mask; other region kinds are returned unchanged."""
    base = build_region_mask(bgr, region, options)
    if not region_is_element(region, options):
        empty = np.zeros(base.shape, bool)
        return RefinedMask(base, empty, empty, np.zeros_like(bgr), "unchanged")

    other_text = _other_text_mask(lines, region, base.shape)
    own_text = _own_text_mask(lines, region, base.shape)
    dark_protected, dark_icon = _compact_dark_components(bgr, region, other_text, own_text)
    solid_protected, solid_icon = _solid_compact_objects(bgr, region, other_text, own_text)
    protected = cv2.bitwise_or(dark_protected, solid_protected) > 0
    icons = [i for i in (dark_icon, solid_icon) if i is not None]
    icon = max(icons, key=lambda i: i[2]) if icons else None
    red = _red_annotation(bgr, region, base, protected)

    refined = base.copy()
    refined[other_text > 0] = 0
    refined[(protected > 0) & (red == 0)] = 0
    # Red annotation is part of the target even where it crosses the icon.
    refined[red > 0] = 255

    restore_pixels, restore_values = _symmetric_icon_source(
        bgr, refined > 0, protected, icon
    )
    note = f"protected={int(protected.sum())} red={int(red.sum())} icon={icon is not None}"
    return RefinedMask(refined > 0, protected, restore_pixels, restore_values, note)

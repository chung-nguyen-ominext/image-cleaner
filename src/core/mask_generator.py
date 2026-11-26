"""Mask construction for TEXT (pixel-level glyph mask) and ELEMENT (whole widget)."""

from __future__ import annotations

import logging

import cv2
import numpy as np

from src.core.models import Rect, Region, RegionKind, clip_rect, expand_rect
from src.utils.image_utils import dilate_mask

log = logging.getLogger("imagecleaner.mask")


def _text_mask_local(crop: np.ndarray, bg: np.ndarray | None = None) -> np.ndarray:
    """Estimate glyph pixels inside a crop without assuming a fixed colour.

    `bg` lets the caller pass the *container* colour (e.g. the blue fill of an
    icon). Without it the 2px frame is used, which is right for a standalone
    word whose box sits directly on the page.
    """
    h, w = crop.shape[:2]
    if h < 3 or w < 3:
        return np.full((h, w), 255, np.uint8)

    if bg is None:
        # Background estimate: median of the 2px frame (works for both light and dark UI).
        frame = np.concatenate([
            crop[0:2].reshape(-1, 3), crop[-2:].reshape(-1, 3),
            crop[:, 0:2].reshape(-1, 3), crop[:, -2:].reshape(-1, 3),
        ])
        bg = np.median(frame, axis=0)

    diff = np.abs(crop.astype(np.int16) - bg.astype(np.int16)).max(axis=2).astype(np.uint8)
    if int(diff.max()) < 12:
        return np.zeros((h, w), np.uint8)

    thr_otsu, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thr = int(max(12, min(thr_otsu * 0.65, 70)))
    mask = (diff > thr).astype(np.uint8) * 255

    # Remove isolated noise, then reconnect strokes.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 3:
            cleaned[labels == i] = 255
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return cv2.dilate(cleaned, np.ones((3, 3), np.uint8), iterations=1)


def _clamped_expand(gray: np.ndarray, box: Rect, pad: int, edge_thr: float = 55.0) -> Rect:
    """Expand a box by `pad` px per side, but never across a strong chrome edge."""
    h_img, w_img = gray.shape[:2]
    x, y, w, h = box
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))

    cols = slice(max(0, x), min(w_img, x + w))
    rows = slice(max(0, y), min(h_img, y + h))

    def expand(limit: int, profile: np.ndarray, start: int, step: int, bound: int) -> int:
        out = 0
        for d in range(1, limit + 1):
            idx = start + step * d
            if idx < 0 or idx >= bound:
                break
            if float(profile[idx]) > edge_thr:
                break
            out = d
        return out

    top = expand(pad, gy[:, cols].mean(axis=1), y - 1, -1, h_img)
    bottom = expand(pad, gy[:, cols].mean(axis=1), y + h, 1, h_img)
    left = expand(pad, gx[rows, :].mean(axis=0), x - 1, -1, w_img)
    right = expand(pad, gx[rows, :].mean(axis=0), x + w, 1, w_img)

    x1, y1 = max(0, x - left), max(0, y - top)
    x2, y2 = min(w_img, x + w + right), min(h_img, y + h + bottom)
    return (x1, y1, x2 - x1, y2 - y1)


def _element_mask(bgr: np.ndarray, box: Rect, padding: int) -> np.ndarray:
    """Whole-widget mask, expanded but clamped at neighbouring chrome edges."""
    h_img, w_img = bgr.shape[:2]
    full = np.zeros((h_img, w_img), np.uint8)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    x, y, w, h = _clamped_expand(gray, box, max(1, padding))
    full[y:y + h, x:x + w] = 255
    return full


def _intersect_rect(a: Rect, b: Rect) -> Rect:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[0] + a[2], b[0] + b[2])
    y2 = min(a[1] + a[3], b[1] + b[3])
    return (x1, y1, max(0, x2 - x1), max(0, y2 - y1))


def _mode_color(pixels: np.ndarray) -> np.ndarray | None:
    if pixels.size == 0 or pixels.shape[0] < 8:
        return None
    q = (pixels.astype(np.int16) // 8 * 8).astype(np.uint8)
    colors, counts = np.unique(q, axis=0, return_counts=True)
    mode = colors[int(np.argmax(counts))].astype(np.int16)
    close = np.max(np.abs(pixels.astype(np.int16) - mode), axis=1) <= 12
    if close.sum() >= 6:
        return np.median(pixels[close], axis=0)
    return np.median(pixels, axis=0)


def _ring_pixels(bgr: np.ndarray, box: Rect, radius: int = 1) -> np.ndarray:
    h, w = bgr.shape[:2]
    x, y, bw, bh = box
    x1, y1 = max(0, x - radius), max(0, y - radius)
    x2, y2 = min(w, x + bw + radius), min(h, y + bh + radius)
    ix1, iy1 = max(0, x), max(0, y)
    ix2, iy2 = min(w, x + bw), min(h, y + bh)
    outer = bgr[y1:y2, x1:x2]
    if outer.size == 0:
        return np.empty((0, 3), np.uint8)
    keep = np.ones(outer.shape[:2], bool)
    keep[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1] = False
    return outer[keep]


def _ring_color(bgr: np.ndarray, box: Rect, radius: int = 1) -> np.ndarray | None:
    return _mode_color(_ring_pixels(bgr, box, radius))


def _flat_word_fill(bgr: np.ndarray, word: Rect) -> np.ndarray | None:
    """Background colour around a word when that background is provably flat."""
    ring = _ring_pixels(bgr, word, 1)
    if ring.shape[0] < 8:
        return None
    hsv = cv2.cvtColor(ring.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    red = (((hsv[:, 0] < 12) | (hsv[:, 0] > 168))
           & (hsv[:, 1] > 100) & (hsv[:, 2] > 60))
    sample = ring[~red]
    if sample.shape[0] < 8:
        sample = ring
    mode = _mode_color(sample)
    if mode is None:
        return None
    for thresh in (8, 12):
        close = np.max(np.abs(sample.astype(np.int16) - mode.astype(np.int16)), axis=1) <= thresh
        if int(close.sum()) < max(8, int(0.30 * sample.shape[0])):
            continue
        vals = sample[close].astype(np.float32)
        spread = float(np.max(np.percentile(vals, 90, axis=0) - np.percentile(vals, 10, axis=0)))
        if spread <= 8.0:
            return mode
    return None


def _glyph_mask_from_fill(bgr: np.ndarray, word: Rect, fill: np.ndarray,
                          pad: int, widget: Rect | None = None,
                          erode: int = 0) -> np.ndarray | None:
    """Glyph-pixel mask around `word` against a known flat fill colour."""
    h_img, w_img = bgr.shape[:2]
    word = clip_rect(word, w_img, h_img)
    # OCR boxes can be several pixels tighter than the ink at high DPI.
    dx = max(pad + 2, 4, int(round(0.28 * word[3])))
    dy = max(2, int(round(0.08 * word[3])))
    search = (word[0] - dx, word[1] - dy, word[2] + 2 * dx, word[3] + 2 * dy)
    cx, cy, cw, ch = clip_rect(search, w_img, h_img)
    if cw < 3 or ch < 3:
        return None
    crop = bgr[cy:cy + ch, cx:cx + cw]
    local = _text_mask_local(crop, bg=fill)

    wx1, wy1 = max(0, word[0] - cx), max(0, word[1] - cy)
    wx2 = min(cw, word[0] + word[2] - cx)
    wy2 = min(ch, word[1] + word[3] - cy)
    ink = None
    if wx2 > wx1 and wy2 > wy1:
        sub = crop[wy1:wy2, wx1:wx2]
        d_sub = np.max(np.abs(sub.astype(np.int16) - fill.astype(np.int16)), axis=2)
        core = d_sub > max(30.0, 0.35 * float(d_sub.max()))
        if int(core.sum()) >= 6:
            ink = np.median(sub[core], axis=0)
    if ink is not None:
        cf = crop.astype(np.int32) - fill.astype(np.int32)
        ci = crop.astype(np.int32) - ink.astype(np.int32)
        d_fill2 = np.sum(cf * cf, axis=2)
        d_ink2 = np.sum(ci * ci, axis=2)
        wbox = clip_rect(widget, w_img, h_img) if widget is not None else word
        gy, gx = np.ogrid[0:ch, 0:cw]
        in_widget = ((gx + cx >= wbox[0]) & (gx + cx < wbox[0] + wbox[2])
                     & (gy + cy >= wbox[1]) & (gy + cy < wbox[1] + wbox[3]))
        keep_ink = (d_ink2 <= d_fill2 + 64) & (in_widget | (d_ink2 < d_fill2))
        local = np.where((local > 0) & keep_ink, 255, 0).astype(np.uint8)

    n, labels = cv2.connectedComponents(local, connectivity=8)
    sx, sy = max(0, word[0] - cx - 2), max(0, word[1] - cy - 2)
    ex = min(cw, word[0] + word[2] - cx + 2)
    ey = min(ch, word[1] + word[3] - cy + 2)
    keep = np.zeros_like(local)
    if ex > sx and ey > sy:
        touching = np.unique(labels[sy:ey, sx:ex])
        for i in touching:
            if i != 0:
                keep[labels == i] = 255
    local = keep

    # Grow into the anti-aliased fringe of the glyphs. The pure ink mask stops
    # at ~50% blend, so a fully anti-aliased screenshot (or an upscaled one)
    # would keep a grey outline around the removed text. The growth is bounded
    # to a few pixels around the word, only into pixels that differ from the
    # flat fill, and never into a red annotation frame.
    if fill is not None and (local > 0).any():
        d_fill = np.max(np.abs(crop.astype(np.int16) - fill.astype(np.int16)), axis=2)
        allow = d_fill > 3
        px = np.uint8([[fill]])
        fh, fs, fv = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0]
        fill_is_red = bool((fh < 12 or fh > 168) and fs > 100 and fv > 60)
        if not fill_is_red:
            # Broad red-ish test catches dark anti-aliased frame edges too;
            # dilating keeps the whole frame rim out of the glyph halo.
            r = crop[:, :, 2].astype(np.int16)
            g = crop[:, :, 1].astype(np.int16)
            b = crop[:, :, 0].astype(np.int16)
            reddish = (r > b + 10) & (r > g + 10) & (r > 20)
            reddish = cv2.dilate(reddish.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            allow &= ~reddish
        grow = max(2, min(6, int(round(0.12 * word[3]))))
        # The crop is already word+dx/dy; growth distance itself is bounded by
        # `grow`, so resampling ringing just outside a tight OCR box is included
        # without reaching unrelated glyphs several pixels away.
        for _ in range(grow):
            grown = cv2.dilate((local > 0).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            add = grown & allow & (local == 0)
            if not add.any():
                break
            local[add] = 255

    if erode > 0:
        local = cv2.erode(local, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * erode + 1, 2 * erode + 1)))
    coverage = float((local > 0).mean()) if local.size else 0.0
    if not (0.004 <= coverage <= 0.90):
        return None
    full = np.zeros((h_img, w_img), np.uint8)
    full[cy:cy + ch, cx:cx + cw] = dilate_mask(local, pad)
    return full


def _widget_fill(bgr: np.ndarray, widget: Rect, text_box: Rect) -> np.ndarray | None:
    """Colour immediately behind the glyphs (card / blue icon / red button).

    A red annotation frame can be almost half of a tight element box, so the
    median of the whole box is unreliable. We compare the dominant colour
    inside the widget with the dominant colour of the 2px ring around the word:
    the ring is never the annotation and disambiguates red-frame-on-card from
    a genuinely red button.
    """
    h, w = bgr.shape[:2]
    x, y, bw, bh = clip_rect(widget, w, h)
    ring = _ring_color(bgr, text_box, 1)
    if bw < 4 or bh < 4:
        return ring
    roi = bgr[y:y + bh, x:x + bw]
    keep = np.ones(roi.shape[:2], bool)
    tx, ty, tw, th = text_box
    px1, py1 = max(0, tx - x - 2), max(0, ty - y - 2)
    px2 = min(bw, tx - x + tw + 2)
    py2 = min(bh, ty - y + th + 2)
    if px2 > px1 and py2 > py1:
        keep[py1:py2, px1:px2] = False

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168))
           & (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 60))
    inside = roi[keep]
    non_red = roi[keep & ~red]
    cand_nored = _mode_color(non_red)
    cand_all = _mode_color(inside)

    def is_red(c) -> bool:
        if c is None:
            return False
        px = np.uint8([[c]])
        h, s, v = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0]
        return bool((h < 12 or h > 168) and s > 100 and v > 60)

    # A red frame hugging the word makes the 1px ring red, but the card inside
    # the element (red pixels excluded) is still the real background.
    if is_red(ring) and cand_nored is not None and not is_red(cand_nored):
        if inside.size:
            support = np.mean(np.max(
                np.abs(inside.astype(np.int16) - cand_nored.astype(np.int16)), axis=1
            ) <= 24)
            if support >= 0.25:
                return cand_nored

    candidates = [cand_nored, cand_all]
    if ring is not None:
        for cand in candidates:
            if cand is not None and float(np.abs(cand - ring).max()) <= 18:
                return cand
        return ring
    return next((c for c in candidates if c is not None), None)


def region_is_element(region: Region, options) -> bool:
    """Effective mask policy, honouring the per-region user override."""
    mode = getattr(region, "mask_mode", None)
    if mode == "text":
        return False
    if mode == "element":
        return True
    return region.kind is RegionKind.ELEMENT and options.mask_mode != "text"


def region_text_fill(bgr: np.ndarray, region: Region, options) -> tuple[int, int, int] | None:
    """Exact background colour for glyph-only removal when it is provably flat.

    A glyph mask can cover most of a small icon, so the generic background
    model only sees the surrounding card. Filling the glyphs with the known
    flat fill is exact and cannot smear.
    """
    if region_is_element(region, options) or region.match is None:
        return None
    # A localized substring sits inside a word: its ring contains the other
    # glyphs, so a "flat fill" would be the ink colour, not the background.
    if bool(getattr(region.match, "partial", False)) and bool(
        getattr(region.match, "localized", False)
    ):
        return None
    word = clip_rect(region.match.box, bgr.shape[1], bgr.shape[0])
    fill = _flat_word_fill(bgr, word)
    if fill is None:
        return None
    return (int(round(fill[0])), int(round(fill[1])), int(round(fill[2])))


def build_region_mask(bgr: np.ndarray, region: Region, options) -> np.ndarray:
    h_img, w_img = bgr.shape[:2]
    margin = int(getattr(region, "margin_px", 0) or 0)
    total = int(options.padding) + margin
    pad = max(0, total)
    erode = max(0, -total)
    box = clip_rect(region.bbox, w_img, h_img)
    full = np.zeros((h_img, w_img), np.uint8)
    if box[2] <= 0 or box[3] <= 0:
        return full

    use_element_mask = region_is_element(region, options)
    if use_element_mask:
        elem_box = expand_rect(box, margin, w_img, h_img) if margin else box
        if elem_box[2] <= 0 or elem_box[3] <= 0:
            return full
        return _element_mask(bgr, elem_box, max(0, int(options.padding)))

    if options.mask_mode == "bbox":
        x, y, w, h = expand_rect(box, total, w_img, h_img)
        full[y:y + h, x:x + w] = 255
        return full

    forced_text = (
        getattr(region, "mask_mode", None) == "text"
        and region.kind is RegionKind.ELEMENT
        and region.match is not None
    )
    if forced_text:
        # Widget detected as a whole, but the user asked for glyph-only removal.
        # Search only around the matched word, against the widget fill (blue
        # icon / card). The element bbox may include a red annotation frame;
        # the tight word box keeps that frame out of the mask.
        word = clip_rect(region.match.box, w_img, h_img)
        fill = _flat_word_fill(bgr, word)
        if fill is None:
            fill = _widget_fill(bgr, box, word)
        glyph = (_glyph_mask_from_fill(bgr, word, fill, pad, widget=box,
                                       erode=erode)
                 if fill is not None else None)
        if glyph is not None:
            return glyph
        # Fall back to the word box (never the whole widget: the user asked text-only).
        x, y, w, h = expand_rect(word, pad, w_img, h_img)
        full[y:y + h, x:x + w] = 255
        return full

    # TEXT: pixel-level glyph mask inside the box.
    # A localized substring must not re-enter neighbouring glyphs, so its
    # search margin stays tight; whole-word boxes get the usual proportional
    # margin because OCR boxes are often 1-3px tight.
    partial = region.match is not None and bool(getattr(region.match, "partial", False))
    if partial and bool(getattr(region.match, "localized", False)):
        # Character boxes are already tight glyph bounds. Delete the glyph
        # pixels inside that union, not the whole rectangle: the gaps between
        # glyphs are background and must survive.
        x, y, w, h = box
        crop = bgr[y:y + h, x:x + w]
        local = _text_mask_local(crop)
        coverage = float((local > 0).mean()) if local.size else 0.0
        if 0.004 <= coverage <= 0.85:
            if erode > 0:
                local = cv2.erode(local, cv2.getStructuringElement(
                    cv2.MORPH_ELLIPSE, (2 * erode + 1, 2 * erode + 1)))
            full[y:y + h, x:x + w] = dilate_mask(local, pad)
            return full
        full[y:y + h, x:x + w] = 255
        return full

    flat = _flat_word_fill(bgr, box)
    if flat is not None:
        glyph = _glyph_mask_from_fill(bgr, box, flat, pad, widget=box,
                                      erode=erode)
        if glyph is not None:
            return glyph
    margin = max(pad + 2, int(round(0.25 * box[3])))
    crop_box = expand_rect(box, margin, w_img, h_img)
    cx, cy, cw, ch = crop_box
    crop = bgr[cy:cy + ch, cx:cx + cw]
    local = _text_mask_local(crop)
    coverage = float((local > 0).mean()) if local.size else 0.0

    if coverage < 0.004 or coverage > 0.85:
        # Nothing reliable -> remove the whole box (safe: never leave text behind).
        x, y, w, h = expand_rect(box, pad, w_img, h_img)
        full[y:y + h, x:x + w] = 255
        return full

    if erode > 0:
        local = cv2.erode(local, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * erode + 1, 2 * erode + 1)))
    local = dilate_mask(local, pad)
    full[cy:cy + ch, cx:cx + cw] = local
    return full


def build_mask(bgr: np.ndarray, regions: list[Region], options) -> np.ndarray:
    h, w = bgr.shape[:2]
    out = np.zeros((h, w), np.uint8)
    for region in regions:
        if not region.enabled:
            continue
        if region.mask is None:
            region.mask = build_region_mask(bgr, region, options)
        if region.mask.shape != (h, w):
            continue
        out = cv2.bitwise_or(out, region.mask)
    return out

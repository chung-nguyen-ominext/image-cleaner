"""Robust background estimation for inpainting.

The old `flat_fill` looked only at a 9px ring and picked the most frequent
quantised colour. A red border hugging the mask can dominate that thin ring,
so white was rejected and Telea/LaMa propagated red into the restored area.

This module estimates the *background* instead of the most frequent nearby
colour: it combines a global colour prior, coverage on several sides of the
mask and robust inlier statistics. When a background model is trusted, masked
pixels are filled from the model directly (exact flat colour or fitted plane),
so no neighbouring border/text colour can leak in. For complex backgrounds it
only sanitises the narrow source band fed to the inpainting engine; composite
still keeps every pixel outside the mask bit-identical.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.utils.image_utils import mask_bbox


@dataclass
class BackgroundModel:
    kind: str = "none"           # none | flat | plane
    color: np.ndarray | None = None
    coeff: np.ndarray | None = None
    sides: int = 0
    inlier_fraction: float = 0.0
    residual: float = 0.0

    def predict(self, ys: np.ndarray, xs: np.ndarray, shape) -> np.ndarray | None:
        if self.kind == "flat" and self.color is not None:
            out = np.empty((ys.size, 3), np.uint8)
            out[:] = self.color.astype(np.uint8)
            return out
        if self.kind == "plane" and self.coeff is not None:
            A = np.stack([xs, ys, np.ones_like(xs)], axis=1).astype(np.float32)
            return np.clip(A @ self.coeff, 0, 255).astype(np.uint8)
        return None


def _ring(image: np.ndarray, mask: np.ndarray, radius: int):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    ring = (cv2.dilate(mask, kernel) - mask) > 0
    ys, xs = np.where(ring)
    return ring, ys, xs


def _quantized_mode(pixels: np.ndarray, step: int = 4):
    if pixels.size == 0:
        return None, 0.0
    q = (pixels.astype(np.int16) // step * step).astype(np.uint8)
    colors, counts = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
    best = int(np.argmax(counts))
    return colors[best].astype(np.float32), float(counts[best]) / len(q.reshape(-1, 3))


def _side_coverage(xs: np.ndarray, ys: np.ndarray, box) -> int:
    """How many sides of the mask are represented in a set of ring pixels."""
    if xs.size == 0:
        return 0
    bx, by, bw, bh = box
    sides = 0
    if ys.min() < by:
        sides += 1
    if ys.max() > by + bh:
        sides += 1
    if xs.min() < bx:
        sides += 1
    if xs.max() > bx + bw:
        sides += 1
    return sides


def estimate_background(image: np.ndarray, mask: np.ndarray) -> BackgroundModel:
    """Estimate the *immediate container* background, never the page colour.

    A 3px inner ring anchors the model to the colour right next to the object
    (the card / blue box). The wider annulus is then filtered by that anchor so
    an outer black/white page cannot win just because it is large.
    """
    if mask.max() == 0:
        return BackgroundModel()
    box = mask_bbox(mask)
    if box is None:
        return BackgroundModel()

    # Anchor: colour of the pixels immediately surrounding the mask. A 1px
    # ring stays inside the container even when the object nearly fills it.
    _, ays, axs = _ring(image, mask, 1)
    if axs.size < 12:
        return BackgroundModel()
    anchor, _ = _quantized_mode(image[ays, axs], step=4)
    if anchor is None:
        return BackgroundModel()

    anchor_sat = int(cv2.cvtColor(
        np.uint8([[anchor]]), cv2.COLOR_BGR2HSV
    )[0, 0, 1])

    def choose(annulus, xs, ys):
        """Pick the container colour of one annulus."""
        q = (annulus // 4 * 4).astype(np.uint8)
        colors, counts = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
        candidates = []
        anchor_support = 0.0
        for idx in np.argsort(-counts)[:8]:
            color = colors[idx].astype(np.float32)
            close = np.max(np.abs(annulus - color.astype(np.int16)), axis=1) <= 6
            fraction = float(close.sum()) / len(annulus)
            sides = _side_coverage(xs[close], ys[close], box)
            candidates.append((color, fraction, sides))
            if float(np.abs(color - anchor).max()) <= 12:
                anchor_support = max(anchor_support, fraction)

        # A saturated anchor (e.g. a red badge) is the object, not the page.
        if anchor_sat > 70:
            neutral = [c for c in candidates
                       if int(cv2.cvtColor(np.uint8([[c[0]]]), cv2.COLOR_BGR2HSV)[0, 0, 1]) < 40
                       and c[1] >= 0.50 and c[1] > anchor_support + 0.10]
            if neutral:
                return max(neutral, key=lambda c: c[1])

        def score(c):
            near_anchor = float(np.abs(c[0] - anchor).max()) <= 12
            return c[1] + (0.70 if near_anchor else 0.0) + 0.10 * c[2] / 4.0
        return max(candidates, key=score)

    # Grow the annulus only until the anchor colour has a solid majority. A
    # wide annulus would cross the card/container edge and see the outer page.
    chosen = None
    annulus = ys = xs = None
    for radius in (3, 5, 8, 10, 14, 20):
        _, ring_ys, ring_xs = _ring(image, mask, radius)
        if ring_xs.size < 40:
            continue
        annulus = image[ring_ys, ring_xs].astype(np.int16)
        candidate = choose(annulus, ring_xs, ring_ys)
        support = np.max(np.abs(annulus - candidate[0].astype(np.int16)), axis=1) <= 32
        if support.mean() >= 0.50 and _side_coverage(ring_xs[support], ring_ys[support], box) >= 3:
            chosen, ys, xs = candidate, ring_ys, ring_xs
            break
    if chosen is None:
        # Keep the widest ring: texture/no dominant colour -> engine fallback.
        _, ys, xs = _ring(image, mask, 20)
        if xs.size < 40:
            return BackgroundModel()
        annulus = image[ys, xs].astype(np.int16)
        chosen = choose(annulus, xs, ys)

    support = np.max(np.abs(annulus - chosen[0].astype(np.int16)), axis=1) <= 32
    fraction = float(support.sum()) / len(annulus)
    sides = _side_coverage(xs[support], ys[support], box)
    if fraction < 0.15 or sides < 3:
        return BackgroundModel()

    A = np.stack([xs[support], ys[support], np.ones(int(support.sum()))], axis=1).astype(np.float32)
    vals = annulus[support].astype(np.float32)
    coeff, *_ = np.linalg.lstsq(A, vals, rcond=None)
    for _ in range(3):
        resid = np.abs(A @ coeff - vals).max(axis=1)
        keep = resid <= 6.0
        if keep.sum() < 12:
            break
        new_coeff, *_ = np.linalg.lstsq(A[keep], vals[keep], rcond=None)
        if np.allclose(new_coeff, coeff):
            break
        coeff = new_coeff
    resid_all = np.abs(A @ coeff - vals).max(axis=1)
    keep = resid_all <= 6.0
    if keep.sum() < 12:
        return BackgroundModel()
    final_resid = float(resid_all[keep].max())
    sides = _side_coverage(xs[support][keep], ys[support][keep], box)
    if final_resid > 6.0 or sides < 3:
        return BackgroundModel()

    slope = float(np.max(np.abs(coeff[:2]))) * 100.0
    inlier_fraction = float(keep.mean())
    if slope <= 2.0:
        return BackgroundModel("flat", color=np.median(vals[keep], axis=0).astype(np.uint8),
                               sides=sides, inlier_fraction=inlier_fraction,
                               residual=final_resid)
    return BackgroundModel("plane", coeff=coeff, sides=sides,
                           inlier_fraction=inlier_fraction, residual=final_resid)


def apply_model(image: np.ndarray, mask: np.ndarray, model: BackgroundModel):
    """Direct, exact background reconstruction when the model is trusted."""
    ys, xs = np.where(mask > 0)
    filled = model.predict(ys, xs, image.shape[:2])
    if filled is None:
        return None
    out = image.copy()
    out[ys, xs] = filled
    return out


def contaminated_band(image: np.ndarray, mask: np.ndarray, band: int,
                      model: BackgroundModel | None = None,
                      protected: np.ndarray | None = None) -> np.ndarray:
    """Pixels near the mask belonging to the removed object, not its container.

    With a trusted model, deviation from the model prediction defines
    contamination (a saturated blue/red container is NOT contamination).
    Without a model (texture/photo), only locally-dark glyph fringes are
    flagged — never saturated pixels, which may be the real background.
    """
    if band <= 0 or mask.max() == 0:
        return np.zeros(mask.shape, np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    near = (cv2.dilate(mask, kernel) - mask) > 0
    if not near.any():
        return np.zeros(mask.shape, np.uint8)

    ys, xs = np.where(near)
    if model is not None and model.kind in ("flat", "plane"):
        prediction = model.predict(ys, xs, image.shape[:2])
        bad = np.max(np.abs(image[ys, xs].astype(np.int16)
                            - prediction.astype(np.int16)), axis=1) > 30
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        local = cv2.medianBlur(gray, 31)
        bad = (gray[ys, xs].astype(np.int16) < local[ys, xs].astype(np.int16) - 25)

    contaminated = np.zeros(mask.shape, np.uint8)
    contaminated[ys[bad], xs[bad]] = 1
    contaminated = cv2.dilate(contaminated, np.ones((3, 3), np.uint8)) > 0
    if protected is not None:
        contaminated[protected] = False
    return (contaminated & near).astype(np.uint8)


def sanitize_guide(image: np.ndarray, mask: np.ndarray,
                   model: BackgroundModel, band: int,
                   protected: np.ndarray | None = None) -> np.ndarray:
    """Clean only the engine's source pixels, using the local container colour."""
    if band <= 0:
        return image
    contaminated = contaminated_band(image, mask, band, model, protected)
    if not contaminated.any():
        return image

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    near = (cv2.dilate(mask, kernel) - mask) > 0
    tmp = image.copy()

    if model.kind in ("flat", "plane"):
        my, mx = np.where(mask > 0)
        prediction = model.predict(my, mx, image.shape[:2])
        if prediction is not None:
            tmp[my, mx] = prediction
    else:
        source = image[near & (contaminated == 0)]
        if source.size:
            tmp[mask > 0] = np.median(source, axis=0).astype(np.uint8)

    return cv2.inpaint(tmp, contaminated * 255, 3, cv2.INPAINT_TELEA)


def restore_masked(image: np.ndarray, filled: np.ndarray, mask: np.ndarray,
                   model: BackgroundModel, snap_uniform: bool = False) -> np.ndarray:
    """Keep the original outside the mask; optionally snap a truly uniform fill.

    `filled` always comes from a real inpainting engine. Snapping is only a
    final 1-2 level correction for backgrounds that are provably one colour;
    it never replaces texture/gradient detail reconstructed by the engine.
    """
    out = image.copy()
    out[mask > 0] = filled[mask > 0]
    exact_flat = model.kind == "flat" and model.inlier_fraction >= 0.20
    exact_plane = model.kind == "plane" and model.residual <= 1.0
    if snap_uniform and (exact_flat or exact_plane):
        direct = apply_model(out, mask, model)
        if direct is not None:
            return direct
    return out

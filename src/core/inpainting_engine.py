"""Inpainting engine registry + `auto` policy with colour-bleed protection."""

from __future__ import annotations

import logging

import cv2
import numpy as np

from src.core.background_restore import (
    apply_model, estimate_background, restore_masked, sanitize_guide,
)
from src.engines.lama_inpainting import LamaInpaint
from src.engines.opencv_inpainting import OpenCVInpaint

log = logging.getLogger("imagecleaner.inpaint")


class AutoInpaint:
    """Reconstruct the background with a real engine, never just paint over it."""

    name = "auto"

    def __init__(self, options, lama: LamaInpaint | None = None) -> None:
        self._telea = OpenCVInpaint("telea", options.inpaint_radius)
        self._lama = lama or LamaInpaint()
        self._use_flat_fill = bool(getattr(options, "flat_fill", True))
        self.last_engine = "opencv-telea"

    def available(self) -> bool:
        return True

    def inpaint(self, image: np.ndarray, mask: np.ndarray,
                protected: np.ndarray | None = None) -> np.ndarray:
        if mask.max() == 0:
            return image

        model = estimate_background(image, mask)
        # The engine reads pixels just outside the mask. Clean the red border /
        # black glyph fringes there BEFORE reconstruction, but never the pixels
        # marked as protected (icons / other UI elements).
        guide = sanitize_guide(image, mask, model, band=14, protected=protected)

        # Flat / smooth-gradient UI: Telea propagates the local background
        # accurately and instantly. Texture / photo backgrounds: LaMa actually
        # hallucinates the missing detail instead of smearing it.
        if model.kind == "none" and self._lama.available():
            self.last_engine = "lama"
            filled = self._lama.inpaint(guide, mask)
        else:
            self.last_engine = "opencv-telea"
            filled = self._telea.inpaint(guide, mask)
        return restore_masked(image, filled, mask, model, snap_uniform=self._use_flat_fill)


class GuardedInpaint:
    """Wrap an explicitly selected engine with the same colour-bleed guards."""

    def __init__(self, delegate, options) -> None:
        self._delegate = delegate
        self.name = getattr(delegate, "name", "unknown")
        self.last_engine = self.name
        self._use_flat_fill = bool(getattr(options, "flat_fill", True))
        radius = int(getattr(options, "inpaint_radius", 3))
        self._band = 14 if "lama" in self.name.lower() else max(8, radius + 3)

    def available(self) -> bool:
        return self._delegate.available()

    def inpaint(self, image: np.ndarray, mask: np.ndarray,
                protected: np.ndarray | None = None) -> np.ndarray:
        if mask.max() == 0:
            return image
        model = estimate_background(image, mask)
        guide = sanitize_guide(image, mask, model, self._band, protected)
        filled = self._delegate.inpaint(guide, mask)
        self.last_engine = getattr(self._delegate, "last_engine", None) or self.name
        return restore_masked(image, filled, mask, model, snap_uniform=self._use_flat_fill)


def create_engine(options):
    choice = (options.inpaint_engine or "auto").lower()
    if choice == "lama":
        lama = LamaInpaint()
        if lama.available():
            return GuardedInpaint(lama, options)
        log.warning("LaMa requested but unavailable (%s); falling back to OpenCV", lama.error)
        return GuardedInpaint(OpenCVInpaint("telea", options.inpaint_radius), options)
    if choice == "ns":
        return GuardedInpaint(OpenCVInpaint("ns", options.inpaint_radius), options)
    if choice == "telea":
        return GuardedInpaint(OpenCVInpaint("telea", options.inpaint_radius), options)
    return AutoInpaint(options)

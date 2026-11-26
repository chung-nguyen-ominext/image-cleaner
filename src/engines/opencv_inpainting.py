"""OpenCV classical inpainting (Telea / Navier-Stokes)."""

from __future__ import annotations

import cv2
import numpy as np


class OpenCVInpaint:
    def __init__(self, method: str = "telea", radius: int = 3) -> None:
        self._flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
        self.name = "opencv-ns" if method == "ns" else "opencv-telea"
        self.radius = max(1, int(radius))

    def available(self) -> bool:
        return True

    def inpaint(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if mask.max() == 0:
            return image
        return cv2.inpaint(image, mask, self.radius, self._flag)

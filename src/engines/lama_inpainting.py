"""LaMa ONNX inpainting, CPU-only, offline.

Model: Carve/LaMa-ONNX (big-lama), fixed 512x512 input, image in [0,1] RGB,
mask in {0,1}. Runs on padded square crops around each mask cluster so that
4K screenshots do not get downscaled as a whole.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from src.config import paths

log = logging.getLogger("imagecleaner.lama")

# One ONNX session per model path: repeated engine creation (GUI previews,
# batch runs, tests) must not reload the 199MB model.
_SESSION_CACHE: dict[str, tuple] = {}


class LamaInpaint:
    name = "lama"
    SIZE = 512

    def __init__(self, model_path=None, threads: int = 0) -> None:
        self._session = None
        self._input_names: list[str] = []
        self._path = model_path or paths.model_path("lama/lama_fp32.onnx")
        self._threads = threads
        self._error: str | None = None

    def available(self) -> bool:
        if self._session is not None:
            return True
        if self._path is not None:
            cached = _SESSION_CACHE.get(str(self._path))
            if cached is not None:
                self._session, self._input_names = cached
                return True
        if self._path is None or not self._path.exists():
            self._error = "model not found (models/lama/lama_fp32.onnx)"
            return False
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            if self._threads > 0:
                opts.intra_op_num_threads = self._threads
            self._session = ort.InferenceSession(
                str(self._path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self._input_names = [i.name for i in self._session.get_inputs()]
            _SESSION_CACHE[str(self._path)] = (self._session, self._input_names)
            log.info("LaMa loaded from %s", self._path)
            return True
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            log.warning("LaMa unavailable: %s", exc)
            return False

    @property
    def error(self) -> str | None:
        return self._error

    def inpaint(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if mask.max() == 0 or not self.available():
            return image

        # Merge nearby glyphs into clusters so LaMa sees enough context.
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
        clustered = cv2.dilate(mask, kernel, iterations=1)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(clustered, connectivity=8)

        result = image.copy()
        for i in range(1, n):
            x = int(stats[i, cv2.CC_STAT_LEFT])
            y = int(stats[i, cv2.CC_STAT_TOP])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            self._inpaint_into(result, mask, (x, y, w, h))
        return result

    def _inpaint_into(self, image: np.ndarray, mask: np.ndarray, bbox: tuple[int, int, int, int]) -> None:
        h_img, w_img = image.shape[:2]
        x, y, w, h = bbox
        pad = max(24, int(0.6 * max(w, h)))
        x1, y1 = max(0, x - pad), max(0, y - pad)
        x2, y2 = min(w_img, x + w + pad), min(h_img, y + h + pad)
        cw, ch = x2 - x1, y2 - y1

        side = max(cw, ch, 96)
        side = min(side, max(cw, ch) + 2 * pad)

        canvas = image[y1:y2, x1:x2]
        top = (side - ch) // 2
        bottom = side - ch - top
        left = (side - cw) // 2
        right = side - cw - left
        canvas = cv2.copyMakeBorder(canvas, top, bottom, left, right, cv2.BORDER_REPLICATE)
        canvas_mask = cv2.copyMakeBorder(
            (mask[y1:y2, x1:x2] > 0).astype(np.uint8), top, bottom, left, right,
            cv2.BORDER_CONSTANT, value=0,
        )

        resized = cv2.resize(canvas, (self.SIZE, self.SIZE), interpolation=cv2.INTER_LINEAR)
        resized_mask = cv2.resize(canvas_mask, (self.SIZE, self.SIZE), interpolation=cv2.INTER_NEAREST)

        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        inp = np.transpose(rgb, (2, 0, 1))[None, ...]
        m = resized_mask.astype(np.float32)[None, None, ...]
        out = self._session.run(None, {self._input_names[0]: inp, self._input_names[1]: m})[0]

        # This export returns 0..255 floats (not 0..1): do not rescale.
        out = np.clip(out[0], 0.0, 255.0)
        out = np.transpose(out, (1, 2, 0))
        out = cv2.cvtColor(out, cv2.COLOR_RGB2BGR).astype(np.uint8)
        out = cv2.resize(out, (side, side), interpolation=cv2.INTER_AREA)
        out = out[top:top + ch, left:left + cw]

        # Paste only where the user mask is set; composite() will feather the edge.
        sel = mask[y1:y2, x1:x2] > 0
        region = image[y1:y2, x1:x2]
        region[sel] = out[sel]
        image[y1:y2, x1:x2] = region

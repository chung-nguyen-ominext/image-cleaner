"""OCR engine abstraction. Default backend: RapidOCR (ONNX, fully offline)."""

from __future__ import annotations

import logging
from typing import Protocol

import cv2
import numpy as np

from src.core.char_localizer import localize_char_boxes
from src.core.models import TextLine, Word

log = logging.getLogger("imagecleaner.ocr")


class OCREngine(Protocol):
    name: str

    def recognize(self, bgr: np.ndarray) -> list[TextLine]: ...


class RapidOCREngine:
    """Wraps rapidocr 3.x. Models ship inside the wheel -> no runtime download."""

    name = "rapidocr"

    def __init__(self, min_side: int = 30, max_side: int = 2048) -> None:
        self._engine = None
        self._min_side = min_side
        self._max_side = max_side

    def _ensure(self):
        if self._engine is not None:
            return self._engine
        from rapidocr import RapidOCR

        self._engine = RapidOCR(
            params={
                "Global.return_word_box": True,
                "Global.log_level": "error",
                "Global.min_side_len": self._min_side,
                "Global.max_side_len": self._max_side,
            }
        )
        return self._engine

    def recognize(self, bgr: np.ndarray) -> list[TextLine]:
        engine = self._ensure()
        result = engine(bgr)
        if result is None or result.boxes is None or result.txts is None:
            return []

        boxes = np.asarray(result.boxes)
        txts = tuple(result.txts)
        scores = tuple(result.scores or [1.0] * len(txts))
        word_results = getattr(result, "word_results", None) or ()

        lines: list[TextLine] = []
        for i, (quad, text, score) in enumerate(zip(boxes, txts, scores)):
            text = str(text).strip()
            if not text:
                continue
            q = [(float(p[0]), float(p[1])) for p in np.asarray(quad)]
            line = TextLine(text=text, quad=q, score=float(score))

            # RapidOCR may return word_results for a different subset of lines
            # (score-filtered). Indexing blindly by `i` then misaligns boxes to
            # text, so only use them when counts match; otherwise fall back to
            # one word covering the whole line.
            aligned = len(word_results) == len(boxes)
            if aligned:
                for item in word_results[i]:
                    try:
                        wtext, wscore, wquad = item[0], float(item[1]), item[2]
                    except (TypeError, IndexError):
                        continue
                    wq = np.asarray(wquad)
                    x1, y1 = int(np.floor(wq[:, 0].min())), int(np.floor(wq[:, 1].min()))
                    x2, y2 = int(np.ceil(wq[:, 0].max())), int(np.ceil(wq[:, 1].max()))
                    if x2 > x1 and y2 > y1:
                        box = (x1, y1, x2 - x1, y2 - y1)
                        word = Word(str(wtext), box, wscore)
                        word.char_boxes = localize_char_boxes(bgr, box, str(wtext))
                        line.words.append(word)

            if not line.words:
                bx, by, bw, bh = line.box
                line.words.append(Word(text, (bx, by, bw, bh), float(score)))

            lines.append(line)
        return lines


def crop_for_ocr(bgr: np.ndarray, scale: float) -> np.ndarray:
    """Upscale small screenshots so tiny UI text becomes readable."""
    if abs(scale - 1.0) < 1e-6:
        return bgr
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=interp)


def scale_lines(lines: list[TextLine], inv_scale: float) -> list[TextLine]:
    for line in lines:
        line.quad = [(x * inv_scale, y * inv_scale) for x, y in line.quad]
        for w in line.words:
            x, y, ww, hh = w.box
            w.box = (int(round(x * inv_scale)), int(round(y * inv_scale)),
                     max(1, int(round(ww * inv_scale))), max(1, int(round(hh * inv_scale))))
            if w.char_boxes:
                w.char_boxes = [
                    (int(round(cx * inv_scale)), int(round(cy * inv_scale)),
                     max(1, int(round(cw * inv_scale))), max(1, int(round(ch * inv_scale))))
                    for cx, cy, cw, ch in w.char_boxes
                ]
    return lines


class TesseractEngine:  # pragma: no cover - optional backend, not bundled
    """Optional backend. Requires the `tesseract` binary and `pytesseract`."""

    name = "tesseract"

    def __init__(self) -> None:
        import pytesseract  # noqa: F401

    def recognize(self, bgr: np.ndarray) -> list[TextLine]:
        import pytesseract

        data = pytesseract.image_to_data(
            cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), output_type=pytesseract.Output.DICT
        )
        grouped: dict[tuple, list[int]] = {}
        for i in range(len(data["text"])):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            grouped.setdefault(key, []).append(i)

        lines: list[TextLine] = []
        for idxs in grouped.values():
            words = []
            xs1, ys1, xs2, ys2 = [], [], [], []
            confs = []
            for i in idxs:
                x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                words.append(Word(data["text"][i].strip(), (x, y, w, h), float(data["conf"][i]) / 100.0))
                xs1.append(x); ys1.append(y); xs2.append(x + w); ys2.append(y + h)
                confs.append(float(data["conf"][i]) / 100.0)
            x1, y1, x2, y2 = min(xs1), min(ys1), max(xs2), max(ys2)
            quad = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            lines.append(TextLine(" ".join(w.text for w in words), quad, float(np.mean(confs)), 0, words))
        return lines

"""Frozen-build self-check: proves OCR + bundled models work without source/python.

Usage (from a PyInstaller bundle):
    imagecleaner-selftest path/to/image.png
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from src.config.paths import model_dir, model_path
from src.config.settings import ProcessOptions
from src.core.inpainting_engine import create_engine
from src.engines.ocr_engine import RapidOCREngine
from src.utils.image_utils import load_image


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: selftest IMAGE", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"missing image: {path}", file=sys.stderr)
        return 2

    loaded = load_image(path)
    ocr = RapidOCREngine(max_side=2048)
    lines = ocr.recognize(loaded.ocr_image())
    text = " | ".join(line.text for line in lines)
    print(f"image: {path.name} ({loaded.width}x{loaded.height})")
    print(f"OCR lines: {len(lines)}")
    print(f"text: {text[:300]}")
    print(f"model dir: {model_dir()}")

    lama = model_path("lama/lama_fp32.onnx")
    print(f"lama model: {lama} ({'yes' if lama else 'no'})")
    if not lines:
        print("SELFTEST FAILED: no OCR lines", file=sys.stderr)
        return 1

    # Inpainting self-check: white background + red border + black text.
    probe = np.full((200, 300, 3), 255, np.uint8)
    cv2.rectangle(probe, (60, 85), (185, 128), (0, 0, 235), 4)
    cv2.putText(probe, "VPN", (68, 112), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.line(probe, (68, 121), (150, 121), (0, 0, 235), 3)
    mask = np.zeros(probe.shape[:2], np.uint8)
    mask[92:120, 68:150] = 255
    engine = create_engine(ProcessOptions())
    out = engine.inpaint(probe, mask)
    restored = out[mask > 0]
    if not np.array_equal(restored, np.full_like(restored, 255)):
        print(f"SELFTEST FAILED: background bleed ({restored.mean(axis=0)})", file=sys.stderr)
        return 1
    print(f"inpaint: exact white via {engine.last_engine}")

    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

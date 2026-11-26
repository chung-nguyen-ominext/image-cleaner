"""Regression tests for nested containers and protected icons (Phase 7)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import cv2
import numpy as np

from src.config.settings import ProcessOptions
from src.core.background_restore import estimate_background
from src.core.inpainting_engine import create_engine
from src.core.models import AnalysisResult, Match, Region, RegionKind, TextLine, Word
from src.core.pipeline import Pipeline
from src.core.ui_element_detector import ElementCandidate
from src.utils.image_utils import load_image

FIXTURES = Path("tests/fixtures")


def gt(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def rect_from(box):
    x, y, w, h = box
    return (int(x), int(y), int(w), int(h))


class FakeOCR:
    name = "fake"

    def __init__(self, lines):
        self._lines = lines

    def recognize(self, bgr):
        return [copy.deepcopy(line) for line in self._lines]


def _line(box, text="VPN"):
    x, y, w, h = box
    quad = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return TextLine(text, quad, 0.99, 0, [Word(text, (x, y, w, h), 0.99)])


def _mean_error(a, b):
    return float(np.abs(a.astype(int) - b.astype(int)).mean())


def _red_count(bgr):
    r, g, b = [v.astype(int) for v in cv2.split(bgr)]
    return int(((b - r > 90) & (b - g > 90) & (g - r < 25)).sum())


# ------------------------------------------------------------ case 1: gray card

def test_gray_card_model_is_local_not_black_page():
    g = gt("gray_card_black_page")
    img = cv2.imread(str(FIXTURES / "gray_card_black_page.png"))
    mask = np.zeros(img.shape[:2], np.uint8)
    x, y, w, h = g["button_bbox"]
    mask[y:y + h, x:x + w] = 255

    model = estimate_background(img, mask)
    assert model.kind == "flat"
    assert np.abs(model.color.astype(int) - np.array(g["card_bgr"])).max() <= 2
    assert model.sides >= 3


def test_gray_card_engine_restores_card_colour():
    g = gt("gray_card_black_page")
    img = cv2.imread(str(FIXTURES / "gray_card_black_page.png"))
    mask = np.zeros(img.shape[:2], np.uint8)
    x, y, w, h = g["button_bbox"]
    mask[y:y + h, x:x + w] = 255

    out = create_engine(ProcessOptions()).inpaint(img, mask)
    assert _mean_error(out[mask > 0], np.array(g["card_bgr"])) <= 1
    assert np.array_equal(out[mask == 0], img[mask == 0])


def test_gray_card_pipeline_restores_card_not_page(tmp_path):
    g = gt("gray_card_black_page")
    path = FIXTURES / "gray_card_black_page.png"
    line = _line(g["text_bbox"])
    pipe = Pipeline(ProcessOptions(keywords=["VPN"]), ocr=FakeOCR([line]))
    pipe.detector.detect = lambda *args, **kwargs: ElementCandidate(
        bbox=tuple(g["button_bbox"]), confidence=0.95, evidence={}
    )
    loaded = load_image(path)
    result = pipe.analyze(path, loaded)
    report = pipe.write_result(loaded, result, tmp_path / "out")

    assert report.status.value == "success"
    out = cv2.imread(report.output_path)
    bx, by, bw, bh = g["button_bbox"]
    roi = out[by:by + bh, bx:bx + bw]
    assert _mean_error(roi, np.array(g["card_bgr"])) <= 2
    px, py, pw, ph = g["page_bgr"], 0, 100, 100
    assert np.array_equal(out[:ph, :pw], np.tile(np.array(g["page_bgr"]), (ph, pw, 1)))


# ------------------------------------------------------------- case 2: blue box

def test_blue_box_model_is_local_not_white_page():
    g = gt("blue_box_white_page")
    img = cv2.imread(str(FIXTURES / "blue_box_white_page.png"))
    mask = np.zeros(img.shape[:2], np.uint8)
    x, y, w, h = g["unit_mask_bbox"]
    mask[y:y + h, x:x + w] = 255

    model = estimate_background(img, mask)
    assert model.kind == "flat"
    assert np.abs(model.color.astype(int) - np.array(g["box_bgr"])).max() <= 2


def test_blue_box_engine_restores_box_colour():
    g = gt("blue_box_white_page")
    img = cv2.imread(str(FIXTURES / "blue_box_white_page.png"))
    mask = np.zeros(img.shape[:2], np.uint8)
    x, y, w, h = g["unit_mask_bbox"]
    mask[y:y + h, x:x + w] = 255

    out = create_engine(ProcessOptions()).inpaint(img, mask)
    assert _mean_error(out[mask > 0], np.array(g["box_bgr"])) <= 1
    assert np.array_equal(out[mask == 0], img[mask == 0])


def test_blue_box_pipeline_restores_box_not_page(tmp_path):
    g = gt("blue_box_white_page")
    path = FIXTURES / "blue_box_white_page.png"
    line = _line(g["text_bbox"])
    pipe = Pipeline(ProcessOptions(keywords=["VPN"]), ocr=FakeOCR([line]))
    tb = g["text_bbox"]
    region_box = (tb[0] - 4, tb[1] - 4, tb[2] + 8, tb[3] + 8)
    pipe.detector.detect = lambda *args, **kwargs: ElementCandidate(
        bbox=region_box, confidence=0.95, evidence={}
    )
    loaded = load_image(path)
    result = pipe.analyze(path, loaded)
    report = pipe.write_result(loaded, result, tmp_path / "out")

    assert report.status.value == "success"
    out = cv2.imread(report.output_path)
    bx, by, bw, bh = region_box
    roi = out[by:by + bh, bx:bx + bw]
    assert _mean_error(roi, np.array(g["box_bgr"])) <= 2
    assert np.array_equal(out[:40, :40], np.full((40, 40, 3), 255, np.uint8))


# ------------------------------------------------- case 3: icon under annotation

def _icon_pipeline():
    g = gt("vpn_button_icon_annotation")
    path = FIXTURES / "vpn_button_icon_annotation.png"
    loaded = load_image(path)
    line = _line(g["text_bbox"])
    match = Match(
        keyword="VPN", line_ids=[0], box=rect_from(g["text_bbox"]),
        text="VPN", match_score=1.0, ocr_score=0.99,
    )
    region = Region(
        id=0, kind=RegionKind.ELEMENT, bbox=rect_from(g["button_bbox"]),
        confidence=0.95, mask=None, match=match, enabled=True,
    )
    result = AnalysisResult(str(path), loaded.width, loaded.height,
                            lines=[line], regions=[region])
    return g, loaded, result, Pipeline(ProcessOptions(keywords=["VPN"]),
                                       ocr=FakeOCR([line]))


def test_icon_mask_excludes_icon_core():
    g, loaded, result, pipe = _icon_pipeline()
    out, mask, _ = pipe.render(loaded, result)
    ix, iy, iw, ih = g["icon_core_bbox"]
    icon_roi = loaded.bgr[iy:iy + ih, ix:ix + iw]
    red_over_icon = _red_count(icon_roi)
    masked = int((mask[iy:iy + ih, ix:ix + iw] > 0).sum())
    # Only the red stroke crossing the icon may be removed.
    # +~1px anti-aliasing fringe around the red stroke is also target.
    assert masked <= int(red_over_icon * 1.3) + 30, (masked, red_over_icon)
    bx, by, bw, bh = g["button_bbox"]
    assert (mask[by:by + bh, bx:bx + bw] > 0).mean() >= 0.8


def test_icon_and_red_annotation_after_restoration():
    g, loaded, result, pipe = _icon_pipeline()
    clean = cv2.imread(str(FIXTURES / "vpn_button_icon_clean.png"))
    out, mask, _ = pipe.render(loaded, result)

    rx, ry, rw, rh = g["red_union_bbox"]
    assert _red_count(out[ry:ry + rh, rx:rx + rw]) == 0

    px, py, pw, ph = g["icon_pristine_bbox"]
    assert np.array_equal(out[py:py + ph, px:px + pw],
                          clean[py:py + ph, px:px + pw])

    bx, by, bw, bh = g["button_bbox"]
    assert _red_count(out[by:by + bh, bx:bx + bw]) == 0
    assert np.array_equal(out[mask == 0], loaded.bgr[mask == 0])

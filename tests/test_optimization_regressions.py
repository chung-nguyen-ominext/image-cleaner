"""Stage R regressions for Phase 8 (must fail before the architecture change).

These tests intentionally describe the target behaviour:
- each region gets its own local background;
- preview and saved output are identical;
- a frame overlapping an icon is not rejected;
- a large container defaults to text-only removal.

They are red on the current code and must stay red until the corresponding
implementation stage is finished. Do not use xfail to hide them.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.config.settings import ProcessOptions
from src.core.mask_generator import build_region_mask
from src.core.mask_refinement import refine_element_mask
from src.core.models import (
    AnalysisResult, Match, Region, RegionKind, TextLine, Word, rect_iou,
)
from src.core.pipeline import Pipeline
from src.utils.image_utils import load_image

FIXTURES = Path("tests/fixtures")


def gt(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _line(text: str, box: tuple[int, int, int, int], score: float = 0.99) -> TextLine:
    x, y, w, h = box
    return TextLine(
        text, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], score, 0,
        [Word(text, (x, y, w, h), score)],
    )


def _match(keyword: str, line_id: int, box: tuple[int, int, int, int]) -> Match:
    return Match(keyword, [line_id], box, keyword, 1.0, 0.99)


class FakeOCR:
    name = "fake"

    def __init__(self, lines: list[TextLine]) -> None:
        self._lines = lines

    def recognize(self, bgr: np.ndarray) -> list[TextLine]:
        return [copy.deepcopy(line) for line in self._lines]


def _options(**kwargs) -> ProcessOptions:
    # Keep fake OCR boxes in image coordinates: the synthetic fixtures are
    # smaller than ocr_min_side and would otherwise be upscaled by _run_ocr().
    return ProcessOptions(keywords=["VPN"], ocr_min_side=1, **kwargs)


def _red_count(bgr: np.ndarray) -> int:
    r, g, b = [v.astype(int) for v in cv2.split(bgr)]
    return int(((b - r > 90) & (b - g > 90) & (g - r < 25)).sum())


def _mean_error(roi: np.ndarray, truth) -> float:
    return float(np.abs(roi.astype(int) - np.array(truth, dtype=int)).mean())


# ---------------------------------------------------------------------------
# R1: one mask/model for all regions destroys local container colours
# ---------------------------------------------------------------------------

def test_multi_region_local_backgrounds_are_independent():
    g = gt("multi_container")
    loaded = load_image(FIXTURES / "multi_container.png")

    left_line = _line("VPN", tuple(g["left_text_bbox"]))
    right_line = _line("VPN", tuple(g["right_text_bbox"]))
    left_region = Region(
        0, RegionKind.ELEMENT, tuple(g["left_button_bbox"]), 0.95,
        match=_match("VPN", 0, tuple(g["left_text_bbox"])),
    )
    right_region = Region(
        1, RegionKind.ELEMENT, tuple(g["right_text_bbox"]), 0.95,
        match=_match("VPN", 1, tuple(g["right_text_bbox"])),
    )
    result = AnalysisResult(
        str(FIXTURES / "multi_container.png"), loaded.width, loaded.height,
        [left_line, right_line], [left_region, right_region],
    )

    pipe = Pipeline(_options())
    out, _mask, engine = pipe.render(loaded, result)

    for region, truth, label in (
        (left_region, g["left_card_bgr"], "left gray card"),
        (right_region, g["right_box_bgr"], "right blue box"),
    ):
        x, y, w, h = region.bbox
        error = _mean_error(out[y:y + h, x:x + w], truth)
        assert error <= 2.0, (
            f"{label} restored with the wrong container colour: error={error:.1f}, "
            f"engine={engine}, mean={out[y:y + h, x:x + w].mean(axis=(0, 1)).round(1)}"
        )

    # Control: rendering each region alone must already be correct. This keeps
    # the failure focused on the shared-mask/shared-model architecture.
    for region, truth, label in (
        (left_region, g["left_card_bgr"], "left"),
        (right_region, g["right_box_bgr"], "right"),
    ):
        single = AnalysisResult(
            str(FIXTURES / "multi_container.png"), loaded.width, loaded.height,
            [left_line, right_line], [region],
        )
        out_one, _m, _e = pipe.render(loaded, single)
        x, y, w, h = region.bbox
        assert _mean_error(out_one[y:y + h, x:x + w], truth) <= 2.0, label


# ---------------------------------------------------------------------------
# R2: preview snapshot must render exactly like the saved path
# ---------------------------------------------------------------------------

def test_preview_snapshot_matches_saved_render():
    pytest.importorskip("PySide6")
    from src.ui.main_window import _snapshot

    g = gt("preview_parity")
    loaded = load_image(FIXTURES / "preview_parity.png")
    vpn_line = _line("VPN", tuple(g["vpn_bbox"]))
    keep_line = _line("KEEP", tuple(g["keep_bbox"]))
    region = Region(
        0, RegionKind.ELEMENT, tuple(g["button_bbox"]), 0.95,
        match=_match("VPN", 0, tuple(g["vpn_bbox"])),
    )
    result = AnalysisResult(
        str(FIXTURES / "preview_parity.png"), loaded.width, loaded.height,
        [vpn_line, keep_line], [region],
    )

    pipe = Pipeline(_options())
    saved, saved_mask, _ = pipe.render(loaded, result)
    preview, preview_mask, _ = pipe.render(loaded, _snapshot(result))

    assert np.array_equal(saved_mask, preview_mask), (
        f"preview mask differs from saved mask by {int((saved_mask != preview_mask).sum())} px"
    )

    x, y, w, h = g["keep_bbox"]
    assert np.array_equal(
        saved[y:y + h, x:x + w], loaded.bgr[y:y + h, x:x + w]
    ), "saved render removed unrelated KEEP text"
    assert np.array_equal(
        preview[y:y + h, x:x + w], loaded.bgr[y:y + h, x:x + w]
    ), "preview removed unrelated KEEP text"


# ---------------------------------------------------------------------------
# R3: a correct frame must not be rejected because an icon was OCR'd as text
# ---------------------------------------------------------------------------

def test_frame_with_overlapping_icon_is_not_rejected():
    g = gt("vpn_button_icon_annotation")
    path = FIXTURES / "vpn_button_icon_annotation.png"
    loaded = load_image(path)

    # Real OCR reads the gear icon as "G". That unrelated line currently makes
    # the detector reject the correct button candidate and fall back to TEXT.
    lines = [
        _line("VPN", (112, 181, 47, 37)),
        _line("G", (36, 160, 72, 80), score=0.89),
    ]
    pipe = Pipeline(_options(), ocr=FakeOCR(lines))
    result = pipe.analyze(path, loaded)

    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.kind is RegionKind.ELEMENT, (
        f"frame rejected; got {region.kind.value} bbox={region.bbox}"
    )
    assert rect_iou(region.bbox, tuple(g["button_bbox"])) >= 0.45

    out, _mask, _engine = pipe.render(loaded, result)
    pristine = cv2.imread(str(FIXTURES / "vpn_button_icon_clean.png"))
    px, py, pw, ph = g["icon_pristine_bbox"]
    assert np.array_equal(
        out[py:py + ph, px:px + pw], pristine[py:py + ph, px:px + pw]
    ), "visible icon pixels must stay bit-identical"

    rx, ry, rw, rh = g["red_union_bbox"]
    assert _red_count(out[ry:ry + rh, rx:rx + rw]) == 0


# ---------------------------------------------------------------------------
# R4: a large flat container defaults to text-only, not whole-element removal
# ---------------------------------------------------------------------------

def test_container_is_not_classified_as_button_by_default():
    g = gt("blue_box_white_page")
    path = FIXTURES / "blue_box_white_page.png"
    loaded = load_image(path)

    # OCR-like line box (the real OCR box from the fixture, not the tight ink box).
    lines = [_line("VPN", (82, 155, 56, 32))]
    pipe = Pipeline(_options(), ocr=FakeOCR(lines))
    result = pipe.analyze(path, loaded)

    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.kind is RegionKind.TEXT, (
        f"container classified as {region.kind.value} bbox={region.bbox}; "
        "text-only removal is the safe default"
    )

    out, _mask, engine = pipe.render(loaded, result)
    x, y, w, h = g["text_bbox"]
    error = _mean_error(out[y:y + h, x:x + w], g["box_bgr"])
    assert error <= 2.0, (
        f"blue box background not restored: error={error:.1f}, engine={engine}"
    )


# ---------------------------------------------------------------------------
# T-lite: protected objects must be found in every position/colour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "case_name",
    ["left", "right", "above", "light_on_dark", "colored_on_light"],
)
def test_protected_icon_variants(case_name):
    g = gt("protected_icon_variants")["cases"][case_name]
    loaded = load_image(FIXTURES / "protected_icon_variants.png")

    text_box = tuple(g["text_bbox"])
    line = _line("VPN", text_box)
    region = Region(
        0, RegionKind.ELEMENT, tuple(g["region_bbox"]), 0.95,
        match=_match("VPN", 0, text_box),
    )
    refined = refine_element_mask(loaded.bgr, region, [line], _options())

    ix, iy, iw, ih = g["icon_bbox"]
    roi = loaded.bgr[iy:iy + ih, ix:ix + iw]
    icon_px = np.max(np.abs(roi.astype(int) - np.array(g["icon_bgr"])), axis=2) <= 25
    assert icon_px.sum() >= 0.2 * iw * ih, "fixture icon is too small to test"

    protected = refined.protected[iy:iy + ih, ix:ix + iw][icon_px]
    masked = refined.mask[iy:iy + ih, ix:ix + iw][icon_px]
    assert float(protected.mean()) >= 0.90, (
        f"{case_name}: icon not protected (protected={protected.mean():.2f}, "
        f"note={refined.note})"
    )
    assert float(masked.mean()) <= 0.05, (
        f"{case_name}: icon still masked (masked={masked.mean():.2f})"
    )


def test_unrelated_red_badge_is_kept():
    g = gt("unrelated_red_badge")
    loaded = load_image(FIXTURES / "unrelated_red_badge.png")
    text_box = tuple(g["text_bbox"])
    line = _line("VPN", text_box)
    region = Region(
        0, RegionKind.ELEMENT, tuple(g["button_bbox"]), 0.95,
        match=_match("VPN", 0, text_box),
    )
    refined = refine_element_mask(loaded.bgr, region, [line], _options())

    bx, by, bw, bh = g["badge_bbox"]
    badge = refined.mask[by:by + bh, bx:bx + bw]
    assert float((badge > 0).mean()) <= 0.05, (
        f"unrelated red badge was added to the mask ({int((badge > 0).sum())} px); "
        f"note={refined.note}"
    )


def test_partial_text_mask_is_glyph_not_rectangle():
    g = gt("substring_zza")
    loaded = load_image(FIXTURES / "substring_zza.png")
    # zza_bbox is [x1, y1, x2, y2] in the fixture JSON.
    x1, y1, x2, y2 = g["zza_bbox"]
    box = (x1, y1, x2 - x1, y2 - y1)
    match = Match(
        "zza", [0], box, "zza", 1.0, 1.0, localized=True, partial=True,
    )
    region = Region(0, RegionKind.TEXT, box, 1.0, match=match)
    mask = build_region_mask(loaded.bgr, region, ProcessOptions(padding=0))

    x, y, w, h = box
    density = float((mask[y:y + h, x:x + w] > 0).mean())
    assert 0.02 <= density <= 0.80, (
        f"partial text mask is a rectangle (density={density:.2f})"
    )

    boxes = g["char_boxes"]
    gap_x1, gap_x2 = boxes[6][2], boxes[7][0]
    assert gap_x2 - gap_x1 >= 2
    gap = mask[y:y + h, gap_x1 + 1:gap_x2 - 1]
    assert float((gap > 0).mean()) <= 0.05, (
        "mask covers the gap between adjacent glyphs"
    )

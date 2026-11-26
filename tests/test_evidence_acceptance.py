"""Acceptance tests for the two user-supplied `evidence/` screenshots.

e1.png
  - ELEMENT case: the red annotation frame *and* the text it encloses are
    removed, and the card behind them is restored to one exact colour.
  - TEXT case: only the keyword `VPN` glyphs inside `Add VPN Configuration...`
    are removed; the red frame and the other words stay untouched.

e2.png
  - standalone `VPN` (title and the row label) is removed without touching the
    neighbouring words or the red annotation frame;
  - the blue `VPN` button has two policies: glyph-only (blue square survives)
    and whole-button (the blue square is gone and the row fill is exact).

Acceptance invariants (same wording as the user):
  * no smearing of pixels around the target;
  * after removal the background matches the untouched background colour.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.config.settings import ProcessOptions
from src.core.models import RegionKind
from src.core.mask_generator import build_region_mask
from src.core.pipeline import Pipeline
from src.utils.image_utils import load_image

EVIDENCE = Path("evidence")
KEYWORDS = ["VPN", "Upgrade now", "Internal Use Only", "Confidential"]


def _gt(name: str) -> dict:
    return json.loads((EVIDENCE / f"{name}.json").read_text(encoding="utf-8"))


def _analyse(name: str):
    path = EVIDENCE / f"{name}.png"
    loaded = load_image(path)
    pipe = Pipeline(ProcessOptions(keywords=KEYWORDS))
    result = pipe.analyze(path, loaded)
    return pipe, loaded, result


def _region_for_word(result, needle: str, *, y_min=-1, y_max=10_000, x_min=-1, x_max=10_000):
    hits = []
    for region in result.regions:
        if region.match is None or needle.casefold() not in region.match.text.casefold():
            continue
        x, y, w, h = region.bbox
        if y >= y_min and y <= y_max and x >= x_min and x <= x_max:
            hits.append(region)
    assert hits, f"no region matched {needle!r}"
    return max(hits, key=lambda r: r.confidence)


def _red_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168))
            & (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 60))


def _blue_button_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 0] > 95) & (hsv[:, :, 0] < 115)
            & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 80))


def _assert_flat(roi: np.ndarray, color, label: str, tol: float = 2.0) -> None:
    """Every pixel of a restored flat area must match the untouched colour."""
    err = np.abs(roi.astype(np.int16) - np.array(color, np.int16)).max(axis=2)
    assert float(err.mean()) <= tol, (
        f"{label}: restored background does not match ({roi.mean(axis=(0, 1)).round(1)} "
        f"vs {color}); mean abs error={err.mean():.2f}"
    )
    assert float(np.percentile(err, 95)) <= tol + 1.0, (
        f"{label}: visible colour bleed in restored background "
        f"(p95 error={np.percentile(err, 95):.2f})"
    )


def _assert_no_blur(roi: np.ndarray, color, label: str, tol: float = 3.0) -> None:
    """A blurred flat fill has a spread of values; an exact fill has none."""
    err = np.abs(roi.astype(np.int16) - np.array(color, np.int16)).max(axis=2)
    assert float(err.max()) <= 8, (
        f"{label}: smeared/blurred background, max error={err.max()} "
        f"mean={roi.mean(axis=(0, 1)).round(1)} expected={color}"
    )
    assert float(np.percentile(err, 99)) <= tol, (
        f"{label}: background spread too wide (p99={np.percentile(err, 99):.2f})"
    )


def _assert_unchanged(out: np.ndarray, original: np.ndarray, box, label: str) -> None:
    x, y, w, h = box
    a = out[y:y + h, x:x + w]
    b = original[y:y + h, x:x + w]
    assert np.array_equal(a, b), f"{label}: neighbouring pixels changed ({int((a != b).sum())} px)"


def _assert_unchanged_outside(out: np.ndarray, original: np.ndarray,
                              mask: np.ndarray, label: str) -> None:
    grown = cv2.dilate((mask > 0).astype(np.uint8),
                       cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    changed = (out != original).any(axis=2) & ~grown
    assert not changed.any(), (
        f"{label}: {int(changed.sum())} pixel(s) changed outside the removal mask"
    )


# ---------------------------------------------------------------------------
# e1
# ---------------------------------------------------------------------------

def test_e1_element_removes_red_frame_and_inside_exactly():
    g = _gt("e1")
    pipe, loaded, result = _analyse("e1")

    title = _region_for_word(result, "VPN", y_max=60)
    title.mask_mode = "text"
    annotated = _region_for_word(result, "VPN", y_min=60)
    annotated.mask_mode = "element"

    out, mask, _engine = pipe.render(loaded, result)

    rx, ry, rw, rh = g["red_frame_bbox"]
    assert int(_red_mask(out)[ry:ry + rh, rx:rx + rw].sum()) == 0, "red frame was not removed"

    # The whole text line inside the frame is gone and the card is one colour.
    tx, ty, tw, th = g["annotation_text_bbox"]
    _assert_flat(out[ty:ty + th, tx:tx + tw], g["card_bgr"], "e1 element text area")
    _assert_no_blur(out[ty:ty + th, tx:tx + tw], g["card_bgr"], "e1 element text area")

    # Ring around the restored rectangle is the untouched card colour.
    _assert_flat(out[g["card_below_bbox"][1]:g["card_below_bbox"][1] + g["card_below_bbox"][3], g["card_below_bbox"][0]:g["card_below_bbox"][0] + g["card_below_bbox"][2]], g["card_bgr"], "e1 card below")

    for box, label in (
        (g["status_bbox"], "status bar"),
        (g["back_bbox"], "Back"),
        (g["card_right_bbox"], "card right"),
        (g["status_text_bbox"], "status text"),
    ):
        _assert_unchanged(out, loaded.bgr, box, label)
    _assert_unchanged_outside(out, loaded.bgr, mask, "e1 element")


def test_e1_text_removes_only_vpn_glyphs():
    g = _gt("e1")
    pipe, loaded, result = _analyse("e1")

    _region_for_word(result, "VPN", y_max=60).mask_mode = "text"
    _region_for_word(result, "VPN", y_min=60).mask_mode = "text"

    out, mask, _engine = pipe.render(loaded, result)

    # The red frame must survive a text-only removal.
    rx, ry, rw, rh = g["red_frame_bbox"]
    original_red = int(_red_mask(loaded.bgr)[ry:ry + rh, rx:rx + rw].sum())
    remaining_red = int(_red_mask(out)[ry:ry + rh, rx:rx + rw].sum())
    assert remaining_red == original_red, (
        f"text-only removal touched the red frame ({remaining_red}/{original_red} px left)"
    )

    # Only the VPN word is gone; the other words are bit-identical.
    x, y, w, h = g["vpn_word_ink_bbox"]
    _assert_flat(out[y:y + h, x:x + w], g["card_bgr"], "e1 VPN word area")
    _assert_unchanged(out, loaded.bgr, g["add_word_bbox"], "e1 'Add'")
    _assert_unchanged(out, loaded.bgr, g["configuration_word_bbox"], "e1 'Configuration...'")
    _assert_unchanged(out, loaded.bgr, g["status_bbox"], "e1 status bar")
    _assert_unchanged(out, loaded.bgr, g["back_bbox"], "e1 Back")
    _assert_unchanged(out, loaded.bgr, g["card_right_bbox"], "e1 card right")
    _assert_unchanged_outside(out, loaded.bgr, mask, "e1 text")


def test_scaled_antialiased_text_leaves_no_outline(tmp_path):
    """Same layout at 2x with smooth anti-aliasing (the e3 bug).

    A pure ink mask stops at ~50% blend; on a scaled screenshot that leaves a
    grey outline. The glyph halo must be removed too, while the red frame and
    unrelated text stay bit-identical.
    """
    g = _gt("e2")
    src = load_image(EVIDENCE / "e2.png")
    big = cv2.resize(src.bgr, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    path = tmp_path / "e2_x2.png"
    cv2.imwrite(str(path), big)

    pipe = Pipeline(ProcessOptions(keywords=KEYWORDS, element_policy="text_only",
                                   ocr_max_side=4096))
    loaded = load_image(path)
    result = pipe.analyze(path, loaded)
    out, _mask, _engine = pipe.render(loaded, result)

    def scaled(box):
        x, y, w, h = box
        return (x * 2, y * 2, w * 2, h * 2)

    tx, ty, tw, th = scaled(g["title_vpn_bbox"])
    title = out[ty:ty + th, tx:tx + tw]
    assert int(title.max()) <= 12, (
        f"antialiased title left a residual outline (max={int(title.max())})"
    )

    lx, ly, lw, lh = scaled(g["row_label_bbox"])
    label = out[ly:ly + lh, lx:lx + lw]
    err = np.abs(label.astype(np.int16) - np.array(g["row_bgr"], np.int16)).max(axis=2)
    assert int(err.max()) <= 12, (
        f"antialiased row label left a residual outline (max error={int(err.max())})"
    )

    _assert_unchanged(out, big, scaled(g["red_frame_strip_bbox"]), "scaled red frame")
    _assert_unchanged(out, big, scaled(g["not_connected_bbox"]), "scaled 'Not Connected'")

def test_e2_auto_does_not_cut_the_red_row_annotation():
    """Default auto policy: row label stays text-only; only the icon is ELEMENT."""
    g = _gt("e2")
    pipe, loaded, result = _analyse("e2")
    label = _region_for_word(result, "VPN", x_min=50, x_max=120, y_min=60)
    icon = _region_for_word(result, "VPN", x_min=0, x_max=50, y_min=60)
    assert label.kind is RegionKind.TEXT, (
        "a word inside a row-wide red annotation must not become an ELEMENT"
    )
    assert icon.kind is RegionKind.ELEMENT

    out, _mask, _engine = pipe.render(loaded, result)

    lx, ly, lw, lh = g["row_label_bbox"]
    _assert_flat(out[ly:ly + lh, lx:lx + lw], g["row_bgr"], "e2 auto row label")
    ix, iy, iw, ih = g["icon_bbox"]
    _assert_flat(out[iy:iy + ih, ix:ix + iw], g["row_bgr"], "e2 auto removed icon")
    _assert_unchanged(out, loaded.bgr, g["red_frame_strip_bbox"], "e2 auto red frame")
    _assert_unchanged(out, loaded.bgr, g["not_connected_bbox"], "e2 auto 'Not Connected'")


# ---------------------------------------------------------------------------
# e2
# ---------------------------------------------------------------------------

def _e2_regions(result):
    title = _region_for_word(result, "VPN", y_max=60)
    icon = _region_for_word(result, "VPN", x_min=0, x_max=50, y_min=60)
    label = _region_for_word(result, "VPN", x_min=50, x_max=120, y_min=60)
    title.mask_mode = "text"
    label.mask_mode = "text"
    return title, label, icon


def test_e2_text_policies_keep_blue_button_and_neighbours():
    g = _gt("e2")
    pipe, loaded, result = _analyse("e2")
    _title, _label, icon = _e2_regions(result)
    icon.mask_mode = "text"

    out, mask, _engine = pipe.render(loaded, result)

    # Standalone title VPN is gone; the rest of the header survives.
    title_x, title_y, title_w, title_h = g["title_vpn_bbox"]
    _assert_flat(out[title_y:title_y + title_h, title_x:title_x + title_w],
                 g["page_bgr"], "e2 title VPN area")
    _assert_unchanged(out, loaded.bgr, g["title_suffix_bbox"], "e2 header suffix")
    _assert_unchanged(out, loaded.bgr, g["back_bbox"], "e2 Back")

    # Row label VPN is text-only: card fill restored, unrelated text untouched.
    lx, ly, lw, lh = g["row_label_bbox"]
    _assert_flat(out[ly:ly + lh, lx:lx + lw], g["row_bgr"], "e2 row label area")
    _assert_unchanged(out, loaded.bgr, g["not_connected_bbox"], "e2 'Not Connected'")

    # The blue button survives: the square outline is still blue, no dark card
    # leaked into it, and the glyph area is flat blue.
    ix, iy, iw, ih = g["icon_bbox"]
    blue_before = int(_blue_button_mask(loaded.bgr)[iy:iy + ih, ix:ix + iw].sum())
    blue_after = int(_blue_button_mask(out)[iy:iy + ih, ix:ix + iw].sum())
    assert blue_after >= int(0.70 * blue_before), (
        f"text-only removal destroyed the blue button ({blue_after}/{blue_before} blue px)"
    )
    cx, cy, cw, ch = g["icon_inner_bbox"]
    _assert_flat(out[cy:cy + ch, cx:cx + cw], g["icon_bgr"], "e2 blue button fill", tol=3.0)
    _assert_no_blur(out[cy:cy + ch, cx:cx + cw], g["icon_bgr"], "e2 blue button fill")

    # The red annotation frame is not the target here.
    _assert_unchanged(out, loaded.bgr, g["red_frame_strip_bbox"], "e2 red frame")
    _assert_unchanged_outside(out, loaded.bgr, mask, "e2 text")


def test_e2_element_policy_removes_whole_blue_button_exactly():
    g = _gt("e2")
    pipe, loaded, result = _analyse("e2")
    _title, _label, icon = _e2_regions(result)
    icon.mask_mode = "element"

    out, mask, _engine = pipe.render(loaded, result)

    ix, iy, iw, ih = g["icon_bbox"]
    assert int(_blue_button_mask(out)[iy:iy + ih, ix:ix + iw].sum()) == 0, (
        "whole-button policy left blue pixels behind"
    )
    # The row behind the button is exactly the untouched row colour, no smear.
    _assert_flat(out[iy:iy + ih, ix:ix + iw], g["row_bgr"], "e2 removed button")
    _assert_no_blur(out[iy:iy + ih, ix:ix + iw], g["row_bgr"], "e2 removed button")

    # Neighbouring row pixels and the red frame stay bit-identical.
    _assert_unchanged(out, loaded.bgr, g["not_connected_bbox"], "e2 'Not Connected'")
    _assert_unchanged(out, loaded.bgr, g["red_frame_strip_bbox"], "e2 red frame")
    _assert_unchanged(out, loaded.bgr, g["title_suffix_bbox"], "e2 header suffix")
    _assert_unchanged_outside(out, loaded.bgr, mask, "e2 element")


# ---------------------------------------------------------------------------
# e4: weak `snap` must not hide a keyword behind UNCERTAIN
# ---------------------------------------------------------------------------

def test_e4_weak_snap_falls_back_to_text_and_removes_vpn():
    g = _gt("e4")
    loaded = load_image(EVIDENCE / "e4.png")
    pipe = Pipeline(ProcessOptions(keywords=["VPN"]))
    result = pipe.analyze(EVIDENCE / "e4.png", loaded)

    assert not result.uncertain_regions, (
        "a 0.63-confidence snap candidate must not hide the VPN keyword"
    )
    region = _region_for_word(result, "VPN", y_min=330, y_max=400)
    assert region.kind is RegionKind.TEXT
    assert region.enabled
    assert region.evidence.get("weak_snap") == 1.0
    assert region.match.text == "VPN"

    out, _mask, _engine = pipe.render(loaded, result)

    x, y, w, h = g["bubble_vpn_bbox"]
    _assert_flat(out[y:y + h, x:x + w], g["bubble_fill_bgr"],
                 "e4 bubble VPN area", tol=4.0)
    for key, label in (
        ("proton_word_bbox", "Proton"),
        ("speak1_bbox", "Speak"),
        ("otherwise_bbox", "Otherwise"),
        ("endif_bbox", "End If"),
        ("connected_chip_bbox", "Connected chip"),
        ("search_bbox", "Search Actions"),
    ):
        _assert_unchanged(out, loaded.bgr, g[key], f"e4 {label}")


def test_e4_saved_output_is_lossless_png(tmp_path):
    """e4 is WebP bytes with a .png name; save must follow the extension."""
    from PIL import Image

    g = _gt("e4")
    loaded = load_image(EVIDENCE / "e4.png")
    pipe = Pipeline(ProcessOptions(keywords=["VPN"]))
    report = pipe.process(EVIDENCE / "e4.png", tmp_path)

    assert report.status.name == "SUCCESS"
    assert Path(report.output_path).suffix == ".png"
    assert Image.open(report.output_path).format == "PNG"

    saved = cv2.imread(report.output_path)
    for key in (
        "proton_word_bbox", "speak1_bbox", "otherwise_bbox", "endif_bbox",
        "connected_chip_bbox", "search_bbox",
    ):
        x, y, w, h = g[key]
        assert np.array_equal(saved[y:y + h, x:x + w],
                              loaded.bgr[y:y + h, x:x + w]), (
            f"{key} changed during lossless PNG save"
        )


def test_per_region_margin_expands_and_shrinks_mask():
    loaded = load_image(EVIDENCE / "e4.png")
    pipe = Pipeline(ProcessOptions(keywords=["VPN"]))
    result = pipe.analyze(EVIDENCE / "e4.png", loaded)
    region = _region_for_word(result, "VPN", y_min=330, y_max=400)

    def count(margin: int) -> int:
        region.margin_px = margin
        region.mask = None
        mask = build_region_mask(loaded.bgr, region, pipe.options)
        return int((mask > 0).sum())

    base = count(0)
    grown = count(6)
    shrunk = count(-2)
    assert grown > base > shrunk, (base, grown, shrunk)

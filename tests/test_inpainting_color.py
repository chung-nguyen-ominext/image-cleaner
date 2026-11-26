"""Colour-bleed regression tests for inpainting (detection untouched).

Fixtures: uniform white / gray / gradient background, red border hugging the
mask and black text. The restored pixels must match the true background; no
red/pink/gray smear may appear, and everything outside the mask is bit-exact.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.config.settings import ProcessOptions
from src.core.background_restore import estimate_background
from src.core.inpainting_engine import AutoInpaint, create_engine

# Optional real-world screenshot kept outside the repo. Set IC_REAL_SCREENSHOT
# to test with your own crop; CI skips this test when the file is absent.
REAL_SCREENSHOT = Path(
    os.environ.get("IC_REAL_SCREENSHOT", "~/Downloads/image_cleaner/image_vpn.png")
).expanduser()


def make_case(height=200, width=300, levels=0, gray=255):
    """Return (image, clean background, mask) with a red border + black text."""
    clean = np.zeros((height, width, 3), np.uint8)
    for y in range(height):
        value = int(gray - levels * y / max(1, height - 1))
        clean[y, :, :] = (value, value, value)
    img = clean.copy()
    cv2.rectangle(img, (60, 85), (width - 115, height - 72),
                  (0, 0, 235), 4)               # red border close to the mask
    cv2.putText(img, "VPN", (68, height // 2 + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.line(img, (68, height - 79), (width - 115, height - 79),
             (0, 0, 235), 3)                    # red underline just outside mask
    mask = np.zeros((height, width), np.uint8)
    mask[height // 2 - 28:height // 2, 68:width - 120] = 255
    return img, clean, mask


def bleed_stats(pixels: np.ndarray) -> dict:
    r, g, b = pixels[:, 0].astype(int), pixels[:, 1].astype(int), pixels[:, 2].astype(int)
    return {
        "reddish": int(((b - r > 8) & (b - g > 8)).sum()),
        "dark": int(((r < 100) & (g < 100) & (b < 100)).sum()),
        "min": int(pixels.min()),
        "mean": pixels.mean(axis=0).round(2),
        "std": pixels.std(axis=0).round(2),
    }


def assert_no_bleed(pixels: np.ndarray) -> None:
    stats = bleed_stats(pixels)
    assert stats["reddish"] == 0, stats
    assert stats["dark"] == 0, stats
    assert stats["std"].max() <= 1.0, stats


@pytest.mark.parametrize("engine_name", ["auto", "telea", "ns"])
def test_white_background_red_border_black_text(engine_name):
    img, _clean, mask = make_case(levels=0)
    model = estimate_background(img, mask)
    assert model.kind == "flat"
    assert tuple(model.color) == (255, 255, 255)

    engine = create_engine(ProcessOptions(inpaint_engine=engine_name))
    out = engine.inpaint(img, mask)

    assert_no_bleed(out[mask > 0])
    assert np.array_equal(out[mask == 0], img[mask == 0])


def test_explicit_lama_no_bleed():
    img, _clean, mask = make_case(levels=0)
    engine = create_engine(ProcessOptions(inpaint_engine="lama"))
    out = engine.inpaint(img, mask)
    assert_no_bleed(out[mask > 0])
    assert np.array_equal(out[mask == 0], img[mask == 0])


def test_flat_fill_disabled_still_sanitizes_source_band():
    """Engine path (no direct fill) must also be protected from border colours."""
    img, clean, mask = make_case(levels=0)
    engine = AutoInpaint(ProcessOptions(flat_fill=False))
    out = engine.inpaint(img, mask)

    assert engine.last_engine in ("lama", "opencv-telea")
    assert_no_bleed(out[mask > 0])
    assert np.array_equal(out[mask == 0], img[mask == 0])
    diff = np.abs(out[mask > 0].astype(int) - clean[mask > 0].astype(int))
    assert diff.max() <= 2 and diff.mean() <= 1.2


def test_gray_background_exact():
    img, clean, mask = make_case(levels=0, gray=128)
    out = create_engine(ProcessOptions()).inpaint(img, mask)
    assert_no_bleed(out[mask > 0])
    assert np.array_equal(out[mask > 0], clean[mask > 0])


@pytest.mark.parametrize("levels", [30, 100])
def test_gradient_background_reconstructed_without_bleed(levels):
    img, clean, mask = make_case(height=300, width=400, levels=levels)
    model = estimate_background(img, mask)
    assert model.kind in ("flat", "plane")

    out = create_engine(ProcessOptions()).inpaint(img, mask)
    error = np.abs(out[mask > 0].astype(int) - clean[mask > 0].astype(int))
    stats = bleed_stats(out[mask > 0])
    assert stats["reddish"] == 0 and stats["dark"] == 0, stats
    assert error.max() <= 20
    assert error.mean() <= 4.0


def test_empty_mask_is_noop():
    img, _clean, _mask = make_case()
    out = create_engine(ProcessOptions()).inpaint(img, np.zeros(img.shape[:2], np.uint8))
    assert np.array_equal(out, img)


def textured_case():
    """A photo-like background: waves + noise, with red frame + black text."""
    h, w = 300, 400
    yy, xx = np.mgrid[0:h, 0:w]
    clean = np.zeros((h, w, 3), np.uint8)
    for c in range(3):
        base = 220 - 40 * yy / h + 12 * np.sin(xx / 33.0 + c) + 8 * np.cos(yy / 25.0)
        clean[:, :, c] = np.clip(base, 0, 255).astype(np.uint8)
    rng = np.random.default_rng(3)
    clean = np.clip(clean.astype(np.int16) + rng.normal(0, 4, clean.shape),
                    0, 255).astype(np.uint8)
    img = clean.copy()
    cv2.rectangle(img, (60, 85), (300, 215), (0, 0, 235), 5)
    cv2.putText(img, "VPN", (80, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    mask = np.zeros((h, w), np.uint8)
    mask[82:218, 57:303] = 255
    return img, clean, mask


def stats_free_of_text(restored: np.ndarray, truth: np.ndarray) -> bool:
    """No black glyph remnant: restored minimum must stay near clean minimum."""
    return bool((restored.min(axis=0) >= truth.min(axis=0) - 12).all())


def test_textured_background_details_are_reconstructed():
    """Acceptance: restored area keeps texture detail, not a flat patch."""
    img, clean, mask = textured_case()
    engine = create_engine(ProcessOptions())
    out = engine.inpaint(img, mask)
    assert engine.last_engine == "lama"

    restored = out[mask > 0].astype(float)
    truth = clean[mask > 0].astype(float)
    error = np.abs(restored - truth)
    assert error.mean() <= 10, float(error.mean())
    # Texture must survive: restored std cannot collapse to a flat block.
    ratio = restored.std(axis=0) / truth.std(axis=0)
    assert np.all(ratio > 0.45) and np.all(ratio < 1.8), ratio

    # The synthetic texture itself has a slight red/blue wave, so compare
    # against ground truth rather than a global "reddish == 0" rule.
    assert stats_free_of_text(restored, truth)
    assert np.abs(restored.mean(axis=0) - truth.mean(axis=0)).max() <= 8
    assert np.array_equal(out[mask == 0], img[mask == 0])


@pytest.mark.skipif(
    not REAL_SCREENSHOT.is_file(),
    reason="optional real screenshot missing (set IC_REAL_SCREENSHOT)",
)
def test_real_screenshot_restores_background_without_touching_neighbours():
    """Real user screenshot: VPN badge removed, status bar details survive."""
    from src.utils.image_utils import load_image

    loaded = load_image(REAL_SCREENSHOT)
    mask = np.zeros((loaded.height, loaded.width), np.uint8)
    mask[7:25, 93:119] = 255

    out = create_engine(ProcessOptions()).inpaint(loaded.bgr, mask)
    assert out.shape == loaded.bgr.shape
    assert np.array_equal(out[mask == 0], loaded.bgr[mask == 0])
    stats = bleed_stats(out[mask > 0])
    assert stats["reddish"] == 0 and stats["dark"] == 0, stats

    for name, (x, y, w, h) in {
        "No SIM": (10, 6, 56, 19),
        "clock": (217, 7, 45, 17),
        "battery": (394, 7, 48, 17),
    }.items():
        assert np.array_equal(out[y:y + h, x:x + w], loaded.bgr[y:y + h, x:x + w]), name

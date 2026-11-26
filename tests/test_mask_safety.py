"""Safety invariants: nothing outside the mask may change; masks stay local."""

from __future__ import annotations

import numpy as np

from src.config.settings import ProcessOptions
from src.core.mask_generator import build_mask, build_region_mask
from src.core.models import Region, RegionKind, expand_rect
from src.utils.image_utils import composite


def _image(w=320, h=200) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def test_composite_is_bit_exact_outside_mask():
    img = _image()
    mask = np.zeros(img.shape[:2], np.uint8)
    mask[40:90, 30:120] = 255
    filled = np.full_like(img, 17)

    out = composite(img, filled, mask)

    assert np.array_equal(out[mask == 0], img[mask == 0])
    assert np.array_equal(out[mask > 0], filled[mask > 0])


def test_element_mask_stays_inside_padded_box():
    img = _image()
    region = Region(id=0, kind=RegionKind.ELEMENT, bbox=(60, 50, 80, 40), confidence=0.9)
    mask = build_region_mask(img, region, ProcessOptions(padding=2))

    allowed = expand_rect(region.bbox, 2, img.shape[1], img.shape[0])
    ys, xs = np.where(mask > 0)
    assert xs.size > 0
    assert xs.min() >= allowed[0] and xs.max() < allowed[0] + allowed[2]
    assert ys.min() >= allowed[1] and ys.max() < allowed[1] + allowed[3]


def test_text_mask_stays_local():
    img = _image()
    region = Region(id=0, kind=RegionKind.TEXT, bbox=(100, 60, 70, 30), confidence=1.0)
    pad = 2
    mask = build_region_mask(img, region, ProcessOptions(padding=pad))

    margin = max(pad + 2, int(round(0.25 * region.bbox[3])))
    allowed = expand_rect(region.bbox, margin, img.shape[1], img.shape[0])
    ys, xs = np.where(mask > 0)
    assert xs.min() >= allowed[0] and xs.max() < allowed[0] + allowed[2]
    assert ys.min() >= allowed[1] and ys.max() < allowed[1] + allowed[3]


def test_disabled_region_produces_no_mask():
    img = _image()
    region = Region(id=0, kind=RegionKind.ELEMENT, bbox=(10, 10, 40, 20), confidence=0.9, enabled=False)
    assert build_mask(img, [region], ProcessOptions()).max() == 0


def test_empty_region_list_produces_no_mask():
    img = _image()
    assert build_mask(img, [], ProcessOptions()).max() == 0


def test_mask_is_binary():
    img = _image()
    region = Region(id=0, kind=RegionKind.ELEMENT, bbox=(10, 10, 40, 20), confidence=0.9)
    mask = build_region_mask(img, region, ProcessOptions())
    assert set(np.unique(mask)).issubset({0, 255})

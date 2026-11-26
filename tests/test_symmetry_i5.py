"""I5: mirror reconstruction must be validated, never invented.

The old code reflected hidden icon pixels through the centroid unconditionally,
which can paint ghost strokes onto asymmetric logos. `_symmetric_icon_source`
now estimates PCA axes and requires a high mirror residual.
"""

from __future__ import annotations

import cv2
import numpy as np

from src.core.mask_refinement import _symmetric_icon_source


def _page() -> np.ndarray:
    return np.full((200, 200, 3), 255, np.uint8)


def _ring_icon() -> tuple[np.ndarray, np.ndarray, tuple]:
    img = _page()
    cv2.circle(img, (100, 100), 24, (10, 10, 10), 5)
    cv2.circle(img, (100, 100), 6, (10, 10, 10), -1)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    protected = (gray < 80).astype(bool)
    return img, protected, (100.0, 100.0, int(protected.sum()), 24.0)


def test_symmetric_icon_reconstructs_only_hidden_half():
    img, protected, icon = _ring_icon()
    mask = np.zeros(img.shape[:2], bool)
    mask[70:130, 100:132] = True          # right half hidden by annotation
    restore, values = _symmetric_icon_source(img, mask, protected, icon)

    assert restore.any(), "a symmetric icon should reconstruct its hidden half"
    hidden_ring = restore & protected
    assert int(hidden_ring.sum()) >= 20

    # Nothing outside the mask may be reported for restore, and copied values
    # must be real icon pixels, not white page/background.
    assert not (restore & ~mask).any()
    assert int(values[restore].max()) <= 80


def test_asymmetric_icon_does_not_fabricate_mirror_pixels():
    img = _page()
    # A deliberately one-sided logo: a centre stem plus a top-right arm only.
    # Mirroring it would invent a top-left arm that does not exist.
    cv2.rectangle(img, (88, 78), (100, 122), (10, 10, 10), -1)
    cv2.rectangle(img, (100, 78), (128, 90), (10, 10, 10), -1)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    protected = (gray < 80).astype(bool)
    icon = (100.0, 100.0, int(protected.sum()), 28.0)
    mask = np.zeros(img.shape[:2], bool)
    mask[70:130, 100:132] = True

    restore, _values = _symmetric_icon_source(img, mask, protected, icon)

    assert not restore.any(), (
        "asymmetric icon must fall back to the engine, not a mirrored ghost"
    )

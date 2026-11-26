"""Test-first suite for exact keyword localization (Stage R/T).

These tests are written BEFORE the implementation. They fail against the current
matcher for the right reasons: missed matches, whole-word boxes, and lost word
boundaries. Fake OCR supplies per-character boxes via `Word.char_boxes` so the
expected geometry is deterministic.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.config.settings import ProcessOptions
from src.core.models import ImageStatus, RegionKind, TextLine, Word
from src.core.pipeline import Pipeline

FIXTURES = Path("tests/fixtures")
GT = json.loads((FIXTURES / "substring_zza.json").read_text(encoding="utf-8"))


def make_word(text: str, x: int = 0, y: int = 0, h: int = 20,
              char_boxes: bool = True) -> Word:
    boxes: list[tuple[int, int, int, int]] = []  # Rect = (x, y, w, h)
    cx = x
    for _ in text:
        boxes.append((cx, y, 10, h))
        cx += 11
    word = Word(text, (x, y, cx - x - 1, h), 0.99)
    if char_boxes:
        word.char_boxes = boxes  # dynamic until implementation adds the field
    return word


def make_line(text: str, x: int = 0, y: int = 0, h: int = 20,
              char_boxes: bool = True) -> TextLine:
    words: list[Word] = []
    cx = x
    for token in text.split():
        words.append(make_word(token, cx, y, h, char_boxes))
        cx += words[-1].box[2] + 6
    quad = [
        (x, y), (cx - 6, y), (cx - 6, y + h), (x, y + h),
    ]
    return TextLine(text, quad, 0.99, 0, words)


def opts(keyword: str, **kw) -> ProcessOptions:
    """Text-only by default: pixel tests isolate matching, not widget detection."""
    kw.setdefault("element_policy", "text_only")
    return ProcessOptions(keywords=[keyword], **kw)


def union(boxes) -> tuple[int, int, int, int]:
    """Union of Rects in (x, y, w, h) form."""
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return (int(x1), int(y1), int(x2 - x1), int(y2 - y1))


class FakeOCR:
    """Returns pre-baked lines; used for deterministic pixel/E2E tests."""

    name = "fake"

    def __init__(self, lines: list[TextLine]):
        self._lines = lines

    def recognize(self, bgr: np.ndarray) -> list[TextLine]:
        return [copy.deepcopy(line) for line in self._lines]


# --------------------------------------------------------------- matcher tests

def test_exact_suffix_is_localized():
    """Headline case: `zza` at the end of `abcxyzzza`, not the whole token."""
    from src.core.text_detector import find_matches

    line = make_line("abcxyzzza")
    matches = find_matches([line], ["zza"], opts("zza", word_boundary=True, match_mode="exact"))

    assert len(matches) == 1
    assert matches[0].text == "zza"
    assert matches[0].box == union(line.words[0].char_boxes[6:9])


def test_short_prefix_rejected():
    from src.core.text_detector import find_matches

    assert find_matches([make_line("VPNX")], ["VPN"],
                        opts("VPN", match_mode="exact")) == []


def test_short_infix_rejected():
    from src.core.text_detector import find_matches

    assert find_matches([make_line("AVPNB")], ["VPN"],
                        opts("VPN", match_mode="exact")) == []


def test_short_whole_word_matches():
    from src.core.text_detector import find_matches

    line = make_line("VPN")
    matches = find_matches([line], ["VPN"], opts("VPN", match_mode="exact"))
    assert len(matches) == 1
    assert matches[0].box == line.words[0].box


def test_long_substring_at_start_localized():
    from src.core.text_detector import find_matches

    line = make_line("Confidentiality")
    matches = find_matches([line], ["Confidential"],
                           opts("Confidential", match_mode="exact"))
    assert len(matches) == 1
    assert matches[0].box == union(line.words[0].char_boxes[:12])


def test_long_substring_in_middle_localized():
    from src.core.text_detector import find_matches

    line = make_line("xxConfidentialyy")
    matches = find_matches([line], ["Confidential"],
                           opts("Confidential", match_mode="exact"))
    assert len(matches) == 1
    assert matches[0].box == union(line.words[0].char_boxes[2:14])


def test_long_substring_multiple_occurrences_all_localized():
    from src.core.text_detector import find_matches

    line = make_line("ConfidentialxxConfidential")
    matches = find_matches([line], ["Confidential"],
                           opts("Confidential", match_mode="exact"))
    assert len(matches) == 2
    expected = {
        union(line.words[0].char_boxes[:12]),
        union(line.words[0].char_boxes[14:26]),
    }
    assert {m.box for m in matches} == expected


def test_case_insensitive_suffix_localized():
    from src.core.text_detector import find_matches

    line = make_line("abcXYZZA")  # 8 chars: suffix is the last three
    matches = find_matches([line], ["zza"], opts("zza", match_mode="exact"))
    assert len(matches) == 1
    assert matches[0].box == union(line.words[0].char_boxes[5:8])


def test_whole_word_inside_multiword_line_matches():
    """Currently broken: `sep=""` makes boundaries see the neighbouring words."""
    from src.core.text_detector import find_matches

    line = make_line("Internal VPN enabled")
    matches = find_matches([line], ["VPN"], opts("VPN", match_mode="exact"))
    assert len(matches) == 1
    assert matches[0].box == line.words[1].box


def test_multiword_exact_keyword_matches():
    from src.core.text_detector import find_matches

    line = make_line("Internal Use Only")
    matches = find_matches(
        [line], ["internal use only"],
        opts("internal use only", match_mode="exact"),
    )
    assert len(matches) == 1
    assert matches[0].box == (
        line.words[0].box[0], line.words[0].box[1],
        line.words[-1].box[0] + line.words[-1].box[2] - line.words[0].box[0],
        line.words[0].box[3],
    )


def test_fuzzy_short_keyword_stays_disabled():
    from src.core.text_detector import find_matches

    line = make_line("VPNX")
    assert find_matches([line], ["VPN"],
                        opts("VPN", match_mode="fuzzy", fuzzy_threshold=0.5)) == []


def test_fuzzy_typo_over_five_chars_preserved():
    from src.core.text_detector import find_matches

    matches = find_matches([make_line("Confidentlal")], ["Confidential"],
                           opts("Confidential", match_mode="fuzzy", fuzzy_threshold=0.85))
    assert len(matches) == 1
    assert matches[0].match_score >= 0.85


def test_localization_failure_is_not_auto_approved():
    """No char boxes + partial long keyword => UNCERTAIN, never whole word."""
    from src.core.text_detector import find_matches

    word = Word("Confidentiality", (0, 0, 150, 20), 0.99)  # no char_boxes
    line = TextLine("Confidentiality", [(0, 0), (150, 0), (150, 20), (0, 20)], 0.99, 0, [word])
    matches = find_matches([line], ["Confidential"],
                           opts("Confidential", match_mode="exact"))
    assert matches
    assert all(hasattr(m, "localized") and m.localized is False for m in matches)


# ------------------------------------------------------------ pipeline / pixels

def _make_bar_image(path: Path, boxes, keep_box) -> None:
    img = np.full((400, 1280, 3), 255, np.uint8)
    for x1, y1, x2, y2 in boxes:
        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (10, 10, 10), -1)
    cv2.rectangle(img, (keep_box[0], keep_box[1]),
                  (keep_box[0] + keep_box[2], keep_box[1] + keep_box[3]),
                  (10, 10, 10), -1)
    cv2.imwrite(str(path), img)


def test_substring_removal_changes_only_zza_bbox(tmp_path: Path):
    base_x, base_y = 40, 100
    char_boxes = [
        (base_x + i * 11, base_y, base_x + i * 11 + 10, base_y + 40)
        for i in range(9)
    ]
    keep_box = (600, 100, 80, 40)
    src = tmp_path / "in.png"
    _make_bar_image(src, char_boxes, keep_box)

    word = Word("abcxyzzza", (base_x, base_y, 98, 40), 0.99)
    word.char_boxes = [(x1, y1, x2 - x1, y2 - y1) for x1, y1, x2, y2 in char_boxes]
    quad = [(base_x, base_y), (base_x + 98, base_y),
            (base_x + 98, base_y + 40), (base_x, base_y + 40)]
    line = TextLine("abcxyzzza", quad, 0.99, 0, [word])

    pipe = Pipeline(opts("zza", word_boundary=True, match_mode="exact"),
                    ocr=FakeOCR([line]))
    report = pipe.process(src, tmp_path / "out")

    assert report.status is ImageStatus.SUCCESS
    original = cv2.imread(str(src))
    result = cv2.imread(report.output_path)
    assert result.shape == original.shape

    expected = union([(x1, y1, x2 - x1, y2 - y1) for x1, y1, x2, y2 in char_boxes[6:9]])
    x, y, w, h = expected
    pad = 5
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(original.shape[1], x + w + pad), min(original.shape[0], y + h + pad)
    outside = np.ones(original.shape[:2], bool)
    outside[y1:y2, x1:x2] = False
    assert np.array_equal(result[outside], original[outside])

    inside = (slice(y, y + h), slice(x, x + w))
    assert int((result[inside] != original[inside]).any(axis=2).sum()) > 0


def test_neighbouring_bar_untouched(tmp_path: Path):
    char_boxes = [(40 + i * 11, 100, 50 + i * 11, 140) for i in range(9)]
    keep_box = (600, 100, 80, 40)
    src = tmp_path / "in.png"
    _make_bar_image(src, char_boxes, keep_box)

    word = Word("abcxyzzza", (40, 100, 98, 40), 0.99)
    word.char_boxes = [(x1, y1, x2 - x1, y2 - y1) for x1, y1, x2, y2 in char_boxes]
    line = TextLine("abcxyzzza", [(40, 100), (138, 100), (138, 140), (40, 140)], 0.99, 0, [word])

    pipe = Pipeline(opts("zza", match_mode="exact"), ocr=FakeOCR([line]))
    report = pipe.process(src, tmp_path / "out")

    assert report.status is ImageStatus.SUCCESS
    original = cv2.imread(str(src))
    result = cv2.imread(report.output_path)
    kx, ky, kw, kh = keep_box
    assert np.array_equal(
        result[ky:ky + kh, kx:kx + kw],
        original[ky:ky + kh, kx:kx + kw],
    )


def test_unlocalized_partial_match_is_skipped(tmp_path: Path):
    src = tmp_path / "in.png"
    _make_bar_image(src, [(40 + i * 11, 100, 50 + i * 11, 140) for i in range(15)],
                    (600, 100, 80, 40))
    word = Word("Confidentiality", (40, 100, 150, 40), 0.99)  # no char_boxes
    line = TextLine("Confidentiality", [(40, 100), (190, 100), (190, 140), (40, 140)],
                    0.99, 0, [word])
    pipe = Pipeline(opts("Confidential", match_mode="exact"), ocr=FakeOCR([line]))

    report = pipe.process(src, tmp_path / "out")

    assert report.status is ImageStatus.SKIPPED_UNCERTAIN
    assert report.output_path is None
    assert list((tmp_path / "out").glob("*.png")) == []


@pytest.mark.real_ocr
def test_real_ocr_substring_box_precision(tmp_path: Path):
    """Real model must read the fixture and localize `zza` within 3 px of truth."""
    from src.engines.ocr_engine import RapidOCREngine

    src = FIXTURES / "substring_zza.png"
    pipe = Pipeline(opts("zza", match_mode="exact"), ocr=RapidOCREngine(max_side=2048))
    result = pipe.analyze(src)

    assert any(GT["text"] in line.text.replace(" ", "") for line in result.lines), \
        [line.text for line in result.lines]
    region = [r for r in result.regions if r.kind is not RegionKind.UNCERTAIN]
    assert len(region) == 1
    x1, y1, x2, y2 = GT["zza_bbox"]
    bx, by, bw, bh = region[0].bbox
    assert abs(bx - x1) <= 3 and abs(by - y1) <= 3
    assert abs((bx + bw) - x2) <= 3 and abs((by + bh) - y2) <= 3

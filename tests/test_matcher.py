"""Keyword matching: case, whitespace, boundaries, typos, multi-line blocks."""

from __future__ import annotations

import pytest

from src.config.settings import ProcessOptions, normalize_keyword_list
from src.core.models import TextLine, Word
from src.core.text_detector import find_matches, normalize_text


def make_line(text: str, x: int = 0, y: int = 0, h: int = 20, score: float = 0.99) -> TextLine:
    words: list[Word] = []
    cx = x
    for token in text.split():
        ww = max(8, len(token) * 9)
        words.append(Word(token, (cx, y, ww, h), score))
        cx += ww + 6
    width = max(8, cx - x - 6)
    quad = [(x, y), (x + width, y), (x + width, y + h), (x, y + h)]
    return TextLine(text, quad, score, 0, words)


def opts(**kw) -> ProcessOptions:
    base = dict(keywords=["VPN"])
    base.update(kw)
    return ProcessOptions(**base)


def test_case_insensitive():
    matches = find_matches([make_line("VPN")], ["vpn"], opts())
    assert len(matches) == 1
    assert matches[0].text == "VPN"


def test_keyword_display_keeps_user_spelling():
    matches = find_matches([make_line("VPN")], ["VpN"], opts())
    assert matches[0].keyword == "VpN"


def test_whitespace_tolerant():
    matches = find_matches([make_line("Internal Use Only")], ["internal   use only"], opts())
    assert len(matches) == 1
    assert matches[0].text == "Internal Use Only"


def test_word_boundary_rejects_substring():
    assert find_matches([make_line("VPNX")], ["VPN"], opts(word_boundary=True)) == []


def test_word_boundary_allows_punctuation():
    matches = find_matches([make_line("VPN: connected")], ["VPN"], opts())
    assert len(matches) == 1


def test_fuzzy_absorbs_ocr_typo():
    matches = find_matches([make_line("Confidentlal")], ["Confidential"],
                           opts(keywords=["Confidential"], match_mode="fuzzy", fuzzy_threshold=0.85))
    assert len(matches) == 1
    assert matches[0].match_score >= 0.85


def test_exact_mode_rejects_typo():
    assert find_matches([make_line("Confidentlal")], ["Confidential"],
                        opts(keywords=["Confidential"], match_mode="exact")) == []


def test_multiline_block():
    lines = [make_line("Internal", y=0), make_line("Use Only", y=24)]
    matches = find_matches(lines, ["Internal Use Only"], opts(keywords=["Internal Use Only"]))
    assert len(matches) == 1
    assert matches[0].box[3] >= 44  # spans both lines


def test_multiple_occurrences_all_found():
    lines = [make_line("VPN", y=0), make_line("VPN", y=60), make_line("VPN", y=120)]
    matches = find_matches(lines, ["VPN"], opts())
    assert len(matches) == 3
    assert len({m.box for m in matches}) == 3


def test_line_and_block_units_deduplicated():
    lines = [make_line("Internal", y=0), make_line("Use Only", y=24)]
    matches = find_matches(lines, ["Internal Use Only", "Internal"],
                           opts(keywords=["Internal Use Only", "Internal"]))
    boxes = [m.box for m in matches]
    assert len(boxes) == len(set(boxes))


def test_ignore_spaces():
    matches = find_matches([make_line("InternalUseOnly")], ["internal use only"],
                           opts(keywords=["internal use only"], ignore_spaces=True))
    assert len(matches) == 1


def test_no_match_returns_empty():
    assert find_matches([make_line("Nothing sensitive")], ["VPN"], opts()) == []


def test_no_lines_returns_empty():
    assert find_matches([], ["VPN"], opts()) == []


def test_confidence_is_bounded():
    m = find_matches([make_line("VPN", score=1.0)], ["VPN"], opts())[0]
    assert 0.0 <= m.confidence <= 1.0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("vpn, DNS\nProxy", ["vpn", "DNS", "Proxy"]),
        (["vpn, DNS", " vpn ", ""], ["vpn", "DNS"]),
        ("  spaced   out  ", ["spaced out"]),
    ],
)
def test_normalize_keyword_list(raw, expected):
    assert normalize_keyword_list(raw) == expected


def test_normalize_text_folds_case_and_spaces():
    assert normalize_text("  Internal   Use  ONLY ") == "internal use only"

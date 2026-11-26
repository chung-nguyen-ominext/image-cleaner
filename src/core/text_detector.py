"""Dynamic keyword detection on OCR output.

No keyword is ever hardcoded: everything comes from ProcessOptions.keywords.
Matching is case-insensitive, whitespace tolerant and optionally fuzzy.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass

from src.core.models import Match, Rect, TextLine, Word, rect_area, rect_iou, rect_union

log = logging.getLogger("imagecleaner.matcher")

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_QUOTES = {
    ord("\u2018"): "'", ord("\u2019"): "'", ord("\u201a"): "'", ord("\u201b"): "'",
    ord("\u201c"): '"', ord("\u201d"): '"', ord("\u201e"): '"', ord("\u00ab"): '"',
    ord("\u00bb"): '"',
}
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00ad"), None)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_INVISIBLE).translate(_DASHES).translate(_QUOTES)
    s = s.casefold()
    s = " ".join(s.split())
    return s.strip()


@dataclass
class _Span:
    start: int
    end: int
    word_index: int


@dataclass
class _Unit:
    """A searchable string built from one line or a vertical block of lines."""

    text: str
    words: list[Word]
    word_norm: list[str]
    spans: list[_Span]
    line_ids: list[int]
    line_scores: list[float]

    def box_for(self, first_word: int, last_word: int) -> Rect:
        boxes = [self.words[i].box for i in range(first_word, last_word + 1) if self.words[i].box[2] > 0]
        if not boxes:
            return (0, 0, 1, 1)
        out = boxes[0]
        for b in boxes[1:]:
            out = rect_union(out, b)
        return out

    def score_for(self, first_word: int, last_word: int) -> float:
        ids = {self.line_ids[i] for i in range(first_word, last_word + 1)}
        scores = [s for lid, s in zip(self.line_ids, self.line_scores) if lid in ids]
        return min(scores) if scores else 1.0


def _build_unit(words: list[Word], line_ids: list[int], line_scores: list[float],
                ignore_spaces: bool) -> _Unit | None:
    if not words:
        return None
    norms = [normalize_text(w.text) for w in words]
    if not any(norms):
        return None
    # Keep real word boundaries: concatenating words with "" made boundary
    # checks see neighbouring letters ("Internal VPN" -> "internalvpn").
    sep = "" if ignore_spaces else " "
    if ignore_spaces:
        norms = [n.replace(" ", "") for n in norms]

    parts: list[str] = []
    spans: list[_Span] = []
    cursor = 0
    for i, n in enumerate(norms):
        if i > 0:
            cursor += len(sep)
        spans.append(_Span(cursor, cursor + len(n), i))
        parts.append(n)
        cursor += len(n)
    return _Unit(
        text=sep.join(parts),
        words=words,
        word_norm=norms,
        spans=spans,
        line_ids=line_ids,
        line_scores=line_scores,
    )


def _word_index_at(unit: _Unit, char_index: int) -> int:
    for sp in unit.spans:
        if sp.start <= char_index < sp.end:
            return sp.word_index
    return len(unit.spans) - 1


def _exact_spans(
    haystack: str,
    needle: str,
    word_boundary: bool,
    short_suffix_only: bool = False,
) -> list[tuple[int, int]]:
    """Find exact occurrences with boundary policy.

    - whole-token matches are always allowed;
    - keywords >= 5 chars may match a substring of a token, but only if
      character-level localisation succeeds later;
    - keywords < 5 chars may only be a *suffix* of a token (so `zza` in
      `abcxyzzza` matches, while `VPN` in `VPNX` does not).
    """
    out: list[tuple[int, int]] = []
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            break
        j = i + len(needle)
        ok = True
        if word_boundary and needle:
            before = haystack[i - 1] if i > 0 else " "
            after = haystack[j] if j < len(haystack) else " "
            before_token = bool(_TOKEN_RE.match(before))
            after_token = bool(_TOKEN_RE.match(after))
            if before_token or after_token:
                if short_suffix_only:
                    ok = before_token and not after_token
                # Long keywords: allowed, but box construction must verify
                # character boxes; otherwise the region becomes UNCERTAIN.
        if ok:
            out.append((i, j))
        start = i + max(1, len(needle))
    return out


def _match_geometry(unit: _Unit, s: int, e: int) -> tuple[Rect, bool, bool, str]:
    """Return (box, localized, partial, matched text) for a char span of a unit.

    Uses per-character boxes for the partial edges of a span and whole word
    boxes for fully covered words. If a touched word has no validated character
    boxes, `localized=False` tells the pipeline to mark the region UNCERTAIN.
    """
    wi = _word_index_at(unit, s)
    wj = _word_index_at(unit, max(s, e - 1))
    boxes: list[Rect] = []
    pieces: list[str] = []
    localized = True
    partial = False

    for k in range(wi, wj + 1):
        span = unit.spans[k]
        word = unit.words[k]
        a = max(s, span.start) - span.start
        b = min(e, span.end) - span.start
        full = a == 0 and b == (span.end - span.start)
        if full:
            boxes.append(word.box)
            pieces.append(word.text)
            continue
        partial = True
        chars = getattr(word, "char_boxes", None)
        if chars and len(chars) == (span.end - span.start) and 0 <= a < b <= len(chars):
            boxes.extend(chars[a:b])
            pieces.append(word.text[a:b])
        elif chars and len(chars) == len(word.text) and 0 <= a < b <= len(chars):
            boxes.extend(chars[a:b])
            pieces.append(word.text[a:b])
        else:
            boxes.append(word.box)
            pieces.append(word.text[a:b])
            localized = False

    if not boxes:
        return (0, 0, 1, 1), False, False, ""
    out = boxes[0]
    for box in boxes[1:]:
        out = rect_union(out, box)
    return out, localized, partial, " ".join(p for p in pieces if p)


def _fuzzy_spans(unit: _Unit, keyword: str, threshold: float) -> list[tuple[int, int, float]]:
    """Sliding word-window fuzzy match (absorbs OCR typos)."""
    # Short keywords are too easy to confuse (VPN vs VPNX = 0.86); require exact.
    if len(keyword) < 5:
        return []
    kw_tokens = keyword.split()
    n = len(kw_tokens)
    if n == 0:
        return []
    candidates: list[tuple[int, int, float]] = []
    sizes = sorted({max(1, n - 1), n, n + 1})
    for size in sizes:
        if size > len(unit.words):
            continue
        for i in range(0, len(unit.words) - size + 1):
            window = " ".join(unit.word_norm[i:i + size])
            if not window:
                continue
            if len(window) < 0.6 * len(keyword) or len(window) > 1.8 * len(keyword):
                continue
            ratio = difflib.SequenceMatcher(None, keyword, window).ratio()
            if ratio >= threshold:
                candidates.append((i, i + size - 1, ratio))
    # Keep the best window per starting position, drop heavily overlapping ones.
    candidates.sort(key=lambda c: (-c[2], c[1] - c[0]))
    kept: list[tuple[int, int, float]] = []
    for c in candidates:
        if any(not (c[1] < k[0] or c[0] > k[1]) for k in kept):
            continue
        kept.append(c)
    return kept


def _blocks(lines: list[TextLine], max_gap_ratio: float = 0.9, max_lines: int = 3) -> list[list[int]]:
    """Group vertically adjacent lines so multi-line phrases can match."""
    order = sorted(range(len(lines)), key=lambda i: (lines[i].box[1], lines[i].box[0]))
    blocks: list[list[int]] = []
    for idx in order:
        placed = False
        for block in blocks:
            last = lines[block[-1]]
            cur = lines[idx]
            lx, ly, lw, lh = last.box
            cx, cy, cw, ch = cur.box
            gap = cy - (ly + lh)
            overlap = max(0, min(lx + lw, cx + cw) - max(lx, cx))
            min_w = max(1, min(lw, cw))
            if (
                len(block) < max_lines
                and -0.3 * lh <= gap <= max_gap_ratio * max(lh, ch)
                and overlap / min_w > 0.25
            ):
                block.append(idx)
                placed = True
                break
        if not placed:
            blocks.append([idx])
    return blocks


def find_matches(lines: list[TextLine], keywords: list[str], options) -> list[Match]:
    # Keep the user's original spelling for display, match on the normalised form.
    pairs: list[tuple[str, str]] = []
    for raw in keywords:
        norm = normalize_text(raw)
        if options.ignore_spaces:
            norm = norm.replace(" ", "")
        if norm:
            pairs.append((norm, " ".join(str(raw).split())))
    if not pairs or not lines:
        return []

    units: list[_Unit] = []
    for idx, line in enumerate(lines):
        words = line.words or [Word(line.text, line.box, line.score)]
        u = _build_unit(words, [idx] * len(words), [line.score] * len(words),
                         options.ignore_spaces)
        if u is not None:
            units.append(u)

    for block in _blocks(lines):
        if len(block) < 2:
            continue
        words: list[Word] = []
        ids: list[int] = []
        scores: list[float] = []
        for li in block:
            lw = lines[li].words or [Word(lines[li].text, lines[li].box, lines[li].score)]
            words.extend(lw)
            ids.extend([li] * len(lw))
            scores.extend([lines[li].score] * len(lw))
        u = _build_unit(words, ids, scores, options.ignore_spaces)
        if u is not None:
            units.append(u)

    matches: list[Match] = []
    for unit in units:
        for kw, kw_display in pairs:
            found: list[tuple[int, int, float]] = []
            for s, e in _exact_spans(
                unit.text, kw, options.word_boundary, short_suffix_only=len(kw) < 5
            ):
                found.append((s, e, 1.0))
            if not found and options.match_mode == "fuzzy":
                for wi, wj, ratio in _fuzzy_spans(unit, kw, options.fuzzy_threshold):
                    found.append((unit.spans[wi].start, unit.spans[wj].end, ratio))
            for s, e, mscore in found:
                box, localized, partial, matched_text = _match_geometry(unit, s, e)
                if box[2] <= 0 or box[3] <= 0:
                    continue
                wi = _word_index_at(unit, s)
                wj = _word_index_at(unit, max(s, e - 1))
                ids = sorted({unit.line_ids[i] for i in range(wi, wj + 1)})
                matches.append(Match(
                    keyword=kw_display,
                    line_ids=ids,
                    box=box,
                    text=matched_text,
                    match_score=float(mscore),
                    ocr_score=float(unit.score_for(wi, wj)),
                    localized=localized,
                    partial=partial,
                ))

    # Deduplicate overlapping detections (same phrase found via line + block unit).
    # Prefer the tighter box so a precise substring is not dropped in favour of
    # a whole-token detection of the same phrase.
    matches.sort(key=lambda m: (-m.confidence, rect_area(m.box)))
    kept: list[Match] = []
    for m in matches:
        dup = False
        for k in kept:
            iou = rect_iou(m.box, k.box)
            inter_ratio = 0.0
            ax1, ay1, aw, ah = m.box
            bx1, by1, bw, bh = k.box
            ix = max(0, min(ax1 + aw, bx1 + bw) - max(ax1, bx1))
            iy = max(0, min(ay1 + ah, by1 + bh) - max(ay1, by1))
            if m.box[2] * m.box[3] > 0:
                inter_ratio = (ix * iy) / (m.box[2] * m.box[3])
            if iou > 0.55 or inter_ratio > 0.85:
                dup = True
                break
        if not dup:
            kept.append(m)
    kept.sort(key=lambda m: (m.box[1], m.box[0]))
    log.debug("matcher: %d lines, %d units, %d raw -> %d matches",
              len(lines), len(units), len(matches), len(kept))
    return kept

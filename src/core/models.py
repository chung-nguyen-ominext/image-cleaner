"""Shared data model. Deliberately free of Qt / OpenCV UI dependencies."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

import numpy as np

Rect = tuple[int, int, int, int]  # x, y, w, h


def rect_area(r: Rect) -> int:
    return max(0, r[2]) * max(0, r[3])


def rect_union(a: Rect, b: Rect) -> Rect:
    x1 = min(a[0], b[0])
    y1 = min(a[1], b[1])
    x2 = max(a[0] + a[2], b[0] + b[2])
    y2 = max(a[1] + a[3], b[1] + b[3])
    return (x1, y1, x2 - x1, y2 - y1)


def rect_iou(a: Rect, b: Rect) -> float:
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    return inter / float(rect_area(a) + rect_area(b) - inter)


def rect_intersection_area(a: Rect, b: Rect) -> int:
    iw = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    ih = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return iw * ih


def rect_contains(outer: Rect, inner: Rect, margin: int = 0) -> bool:
    return (
        inner[0] >= outer[0] - margin
        and inner[1] >= outer[1] - margin
        and inner[0] + inner[2] <= outer[0] + outer[2] + margin
        and inner[1] + inner[3] <= outer[1] + outer[3] + margin
    )


def clip_rect(r: Rect, w: int, h: int) -> Rect:
    x1 = max(0, min(r[0], w))
    y1 = max(0, min(r[1], h))
    x2 = max(0, min(r[0] + r[2], w))
    y2 = max(0, min(r[1] + r[3], h))
    return (x1, y1, max(0, x2 - x1), max(0, y2 - y1))


def expand_rect(r: Rect, px: int, w: int, h: int) -> Rect:
    return clip_rect((r[0] - px, r[1] - px, r[2] + 2 * px, r[3] + 2 * px), w, h)


class RegionKind(str, enum.Enum):
    TEXT = "text"
    ELEMENT = "element"
    UNCERTAIN = "uncertain"


class ImageStatus(str, enum.Enum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    SKIPPED_UNCERTAIN = "skipped_uncertain"
    FAILED = "failed"


@dataclass
class Word:
    text: str
    box: Rect
    score: float = 1.0
    # Optional per-character boxes. Populated locally by OCR when column
    # projection can validate one glyph group per character; never guessed by
    # proportional splitting. Used to localise substrings inside a word.
    char_boxes: list[Rect] | None = None


@dataclass
class TextLine:
    text: str
    quad: list[tuple[float, float]]
    score: float
    tile_id: int = 0
    words: list[Word] = field(default_factory=list)

    @property
    def box(self) -> Rect:
        xs = [p[0] for p in self.quad]
        ys = [p[1] for p in self.quad]
        x1, y1 = int(np.floor(min(xs))), int(np.floor(min(ys)))
        x2, y2 = int(np.ceil(max(xs))), int(np.ceil(max(ys)))
        return (x1, y1, max(1, x2 - x1), max(1, y2 - y1))


@dataclass
class Match:
    keyword: str
    line_ids: list[int]
    box: Rect
    text: str
    match_score: float
    ocr_score: float
    # True when the box was derived from word/character geometry. False means
    # the span was only partially localisable and must be reviewed, not deleted.
    localized: bool = True
    # True when the match covers only part of a word (substring). Such regions
    # must not use the generous whole-word glyph search margin.
    partial: bool = False

    @property
    def confidence(self) -> float:
        return round(max(0.0, min(1.0, self.ocr_score * self.match_score)), 4)


@dataclass
class Region:
    id: int
    kind: RegionKind
    bbox: Rect
    confidence: float
    mask: np.ndarray | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    source: str = "ocr"
    match: Match | None = None
    # Per-region user override: None = follow the detected kind, "text" = only
    # keyword glyphs, "element" = the whole widget. The GUI exposes this so the
    # same detection can produce either accepted variant (see evidence/e1/e2).
    mask_mode: str | None = None
    # Per-region mask adjustment in pixels: >0 expands, <0 shrinks. The GUI
    # exposes this so the user can widen a too-tight mask or pull back an
    # over-aggressive one without changing detector thresholds.
    margin_px: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "enabled": self.enabled,
            "source": self.source,
            "mask_mode": self.mask_mode,
            "margin_px": self.margin_px,
            "text": self.match.text if self.match else None,
            "keyword": self.match.keyword if self.match else None,
            "evidence": {k: round(v, 3) if isinstance(v, float) else v
                         for k, v in self.evidence.items()},
        }


@dataclass
class AnalysisResult:
    image_path: str
    width: int
    height: int
    lines: list[TextLine] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def has_matches(self) -> bool:
        return bool(self.regions)

    @property
    def uncertain_regions(self) -> list[Region]:
        return [r for r in self.regions if r.kind is RegionKind.UNCERTAIN]

    def summary(self) -> dict[str, Any]:
        return {
            "image": self.image_path,
            "width": self.width,
            "height": self.height,
            "ocr_lines": len(self.lines),
            "regions": [r.summary() for r in self.regions],
            "warnings": self.warnings,
            "timings": {k: round(v, 3) for k, v in self.timings.items()},
        }


@dataclass
class ProcessReport:
    image_path: str
    status: ImageStatus
    output_path: str | None = None
    regions_total: int = 0
    regions_removed: int = 0
    regions_skipped: int = 0
    elapsed_ms: int = 0
    error: str | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "image": self.image_path,
            "status": self.status.value,
            "output": self.output_path or "",
            "regions_total": self.regions_total,
            "regions_removed": self.regions_removed,
            "regions_skipped": self.regions_skipped,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error or "",
        }

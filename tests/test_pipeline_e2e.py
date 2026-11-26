"""End-to-end pipeline / batch behaviour with a fake OCR engine (no model needed)."""

from __future__ import annotations

import copy
from pathlib import Path

import cv2
import numpy as np

from src.config.settings import ProcessOptions
from src.core.batch_processor import process_folder
from src.core.models import ImageStatus, TextLine, Word
from src.core.pipeline import Pipeline
from src.core.ui_element_detector import ElementCandidate


class FakeOCR:
    """Deterministic OCR stub: returns pre-baked lines in image coordinates."""

    name = "fake"

    def __init__(self, lines: list[TextLine]):
        self._lines = lines

    def recognize(self, bgr: np.ndarray) -> list[TextLine]:
        return [copy.deepcopy(line) for line in self._lines]


def _line(text: str, box: tuple[int, int, int, int], score: float = 1.0) -> TextLine:
    x, y, w, h = box
    quad = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return TextLine(text, quad, score, 0, [Word(text, box, score)])


def _make_image(path: Path, text: str = "VPN") -> None:
    """1280px wide so the pipeline does not upscale and OCR boxes stay 1:1."""
    img = np.full((400, 1280, 3), 255, np.uint8)
    cv2.putText(img, text, (100, 220), cv2.FONT_HERSHEY_SIMPLEX, 3.0, (10, 10, 10), 8)
    cv2.imwrite(str(path), img)


def _options(keywords: list[str], **kw) -> ProcessOptions:
    return ProcessOptions(keywords=keywords, **kw)


def _pipeline(lines: list[TextLine], keywords: list[str], **kw) -> Pipeline:
    return Pipeline(_options(keywords, **kw), ocr=FakeOCR(lines))


def test_not_found_writes_nothing(tmp_path: Path):
    src = tmp_path / "in.png"
    _make_image(src)
    pipe = _pipeline([_line("Hello", (100, 160, 300, 70))], ["VPN"])

    report = pipe.process(src, tmp_path / "out")

    assert report.status is ImageStatus.NOT_FOUND
    assert report.output_path is None
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.png"))


def test_success_removes_region_and_never_overwrites(tmp_path: Path):
    src = tmp_path / "in.png"
    _make_image(src)
    out_dir = tmp_path / "out"
    pipe = _pipeline([_line("VPN", (100, 150, 300, 90))], ["VPN"])

    first = pipe.process(src, out_dir)
    second = pipe.process(src, out_dir)

    assert first.status is ImageStatus.SUCCESS
    assert Path(first.output_path).exists()
    assert Path(second.output_path).exists()
    assert Path(first.output_path) != Path(second.output_path)
    assert src.exists()  # original untouched

    out = cv2.imread(first.output_path)
    assert out.shape == cv2.imread(str(src)).shape


def test_uncertain_region_is_skipped(tmp_path: Path, monkeypatch):
    src = tmp_path / "in.png"
    _make_image(src)
    pipe = _pipeline([_line("VPN", (100, 150, 300, 90))], ["VPN"])

    def fake_detect(bgr, text_box, match_boxes, other_boxes, options):
        return ElementCandidate(bbox=(80, 130, 340, 130), confidence=0.5, evidence={"x": 1.0})

    monkeypatch.setattr(pipe.detector, "detect", fake_detect)
    report = pipe.process(src, tmp_path / "out")

    assert report.status is ImageStatus.SKIPPED_UNCERTAIN
    assert report.regions_skipped == 1
    assert report.output_path is None


def test_batch_continues_after_corrupt_file(tmp_path: Path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _make_image(in_dir / "good.png")
    (in_dir / "broken.png").write_bytes(b"not really a png")

    pipe = _pipeline([_line("VPN", (100, 150, 300, 90))], ["VPN"])
    out_dir = tmp_path / "out"

    reports = process_folder(in_dir, out_dir, pipe.options, pipeline=pipe)

    statuses = {Path(r.image_path).name: r.status for r in reports}
    assert statuses["good.png"] is ImageStatus.SUCCESS
    assert statuses["broken.png"] is ImageStatus.FAILED
    assert (out_dir / "report.csv").exists()
    assert (out_dir / "report.json").exists()


def test_batch_empty_folder_returns_no_reports(tmp_path: Path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    reports = process_folder(in_dir, tmp_path / "out", _options(["VPN"]), pipeline=_pipeline([], ["VPN"]))
    assert reports == []


def test_output_format_png(tmp_path: Path):
    src = tmp_path / "in.jpg"
    _make_image(src)
    pipe = _pipeline([_line("VPN", (100, 150, 300, 90))], ["VPN"], output_format="png")

    report = pipe.process(src, tmp_path / "out")

    assert report.status is ImageStatus.SUCCESS
    assert report.output_path.endswith(".png")

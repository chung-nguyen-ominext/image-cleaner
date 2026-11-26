"""Batch processing over a folder of images."""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Callable, Iterable

from src.config.settings import ProcessOptions
from src.core.models import ImageStatus, ProcessReport
from src.core.pipeline import Pipeline
from src.utils.image_utils import SUPPORTED_EXTS

log = logging.getLogger("imagecleaner.batch")

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


def collect_images(folder: str | Path, recursive: bool = False) -> list[Path]:
    folder = Path(folder)
    it: Iterable[Path] = folder.rglob("*") if recursive else folder.iterdir()
    files = [p for p in it if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    return sorted(files, key=lambda p: str(p).lower())


def summarize(reports: list[ProcessReport]) -> dict[str, int]:
    counts = {s.value: 0 for s in ImageStatus}
    for r in reports:
        counts[r.status.value] += 1
    counts["total"] = len(reports)
    return counts


def write_reports(reports: list[ProcessReport], out_dir: str | Path, elapsed_s: float) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = [r.as_row() for r in reports]

    csv_path = out / "report.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["image"])
        writer.writeheader()
        writer.writerows(rows)

    json_path = out / "report.json"
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(elapsed_s, 2),
        "summary": summarize(reports),
        "results": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return csv_path, json_path


def process_folder(
    input_dir: str | Path,
    output_dir: str | Path,
    options: ProcessOptions,
    recursive: bool = False,
    progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
    debug_dir: str | Path | None = None,
    pipeline: Pipeline | None = None,
) -> list[ProcessReport]:
    files = collect_images(input_dir, recursive)
    if not files:
        log.warning("no supported images found in %s", input_dir)
        return []

    pipe = pipeline or Pipeline(options)
    reports: list[ProcessReport] = []
    t0 = time.perf_counter()

    for i, path in enumerate(files):
        if should_cancel is not None and should_cancel():
            log.info("batch cancelled by user at %d/%d", i, len(files))
            break
        if progress is not None:
            progress(i, len(files), path.name)
        report = pipe.process(path, output_dir, debug_dir=debug_dir)
        reports.append(report)
        log.info("[%d/%d] %s -> %s", i + 1, len(files), path.name, report.status.value)

    if progress is not None:
        progress(len(files), len(files), "done")

    elapsed = time.perf_counter() - t0
    counts = summarize(reports)
    log.info("batch finished in %.1fs: %s", elapsed, counts)
    if reports:
        write_reports(reports, output_dir, elapsed)
    return reports

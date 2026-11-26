"""Headless CLI: analyse and clean a single image or a folder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.config.paths import model_dir, user_log_dir
from src.config.settings import ProcessOptions, load_keyword_preset, normalize_keyword_list
from src.core.batch_processor import process_folder, summarize
from src.core.pipeline import Pipeline
from src.utils.image_utils import SUPPORTED_EXTS, load_image
from src.utils.logger import setup_logger


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="imagecleaner-cli",
        description="Remove user-defined text / UI elements from screenshots (offline).",
    )
    p.add_argument("--input", required=True, help="Image file or folder")
    p.add_argument("--output", required=True, help="Output folder (originals are never overwritten)")
    p.add_argument("--keywords", action="append", default=[], help="Keyword(s); repeat or comma-separated")
    p.add_argument("--keywords-file", help="JSON preset created by the GUI/CLI")
    p.add_argument("--conf", type=float, default=0.60, help="OCR/match confidence threshold (0-1)")
    p.add_argument("--match-mode", choices=["exact", "fuzzy"], default="fuzzy")
    p.add_argument("--engine", choices=["auto", "telea", "ns", "lama"], default="auto")
    p.add_argument("--element-policy", choices=["auto", "text_only"], default="auto")
    p.add_argument("--padding", type=int, default=2, help="Mask dilation in pixels")
    p.add_argument("--output-format", choices=["same", "png", "jpg"], default="same")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Analyse only, do not write images")
    p.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    p.add_argument("--debug", action="store_true", help="Write overlay/mask PNGs to <output>/debug")
    p.add_argument("--ocr-max-side", type=int, default=4096)
    return p


def _resolve_keywords(args) -> list[str]:
    kws = normalize_keyword_list(args.keywords)
    if args.keywords_file:
        kws = normalize_keyword_list(kws + load_keyword_preset(Path(args.keywords_file)))
    return kws


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logger(user_log_dir())

    keywords = _resolve_keywords(args)
    if not keywords:
        print("error: no keywords given (use --keywords or --keywords-file)", file=sys.stderr)
        return 2

    options = ProcessOptions(
        keywords=keywords,
        conf_threshold=args.conf,
        match_mode=args.match_mode,
        inpaint_engine=args.engine,
        element_policy=args.element_policy,
        padding=max(0, args.padding),
        output_format=args.output_format,
        ocr_max_side=args.ocr_max_side,
    )

    src = Path(args.input)
    out = Path(args.output)
    if not src.exists():
        print(f"error: input not found: {src}", file=sys.stderr)
        return 2

    if args.dry_run:
        pipe = Pipeline(options)
        files = [src] if src.is_file() else sorted(
            p for p in (src.rglob("*") if args.recursive else src.iterdir())
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
        )
        payload = []
        for f in files:
            result = pipe.analyze(f)
            payload.append(result.summary())
            if not args.json:
                print(f"\n{f}  ({result.width}x{result.height})")
                if not result.regions:
                    print("  no match")
                for r in result.regions:
                    print(f"  #{r.id} {r.kind.value:9s} conf={r.confidence:.2f} "
                          f"bbox={r.bbox} text={r.match.text if r.match else ''!r}")
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if src.is_file():
        pipe = Pipeline(options)
        report = pipe.process(src, out, debug_dir=(out / "debug") if args.debug else None)
        reports = [report]
    else:
        reports = process_folder(
            src, out, options, recursive=args.recursive,
            debug_dir=(out / "debug") if args.debug else None,
        )

    if args.json:
        print(json.dumps({"summary": summarize(reports),
                          "results": [r.as_row() for r in reports]}, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'IMAGE':40s} {'STATUS':18s} {'REMOVED':>7s} {'SKIPPED':>7s} {'MS':>6s}")
        for r in reports:
            print(f"{Path(r.image_path).name[:40]:40s} {r.status.value:18s} "
                  f"{r.regions_removed:7d} {r.regions_skipped:7d} {r.elapsed_ms:6d}")
            if r.error:
                print(f"    error: {r.error}")
        print(f"\nsummary: {summarize(reports)}")

    if model_dir().as_posix() not in str(Path.cwd()):
        print(f"\nmodels: {model_dir()}")
    failed = [r for r in reports if r.status.value == "failed"]
    return 1 if reports and len(failed) == len(reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())

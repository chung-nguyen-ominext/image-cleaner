"""Analysis + rendering pipeline (no Qt, no CLI)."""

from __future__ import annotations

import logging
import math
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

from src.config.settings import ProcessOptions
from src.core import mask_generator
from src.core.inpainting_engine import create_engine
from src.core.mask_refinement import refine_element_mask
from src.core.models import (
    AnalysisResult, ImageStatus, Match, ProcessReport, Region, RegionKind,
    TextLine, clip_rect, expand_rect, rect_area, rect_intersection_area, rect_iou,
)
from src.core.text_detector import find_matches, normalize_text
from src.core.ui_element_detector import UIElementDetector
from src.engines.ocr_engine import OCREngine, RapidOCREngine, crop_for_ocr
from src.utils.image_utils import (
    LoadedImage, load_image, mask_bbox, overlay_regions, save_image, unique_output_path,
)

log = logging.getLogger("imagecleaner.pipeline")


class Pipeline:
    def __init__(self, options: ProcessOptions, ocr: OCREngine | None = None,
                 inpaint_engine=None, detector: UIElementDetector | None = None) -> None:
        self.options = options
        self.ocr = ocr or RapidOCREngine(
            max_side=max(2048, min(2560, options.ocr_max_side))
        )
        self.detector = detector or UIElementDetector()
        self._inpaint = inpaint_engine

    # ------------------------------------------------------------------ analyze

    def analyze(self, path: str | Path, loaded: LoadedImage | None = None) -> AnalysisResult:
        t0 = time.perf_counter()
        loaded = loaded or load_image(path)
        result = AnalysisResult(str(path), loaded.width, loaded.height)

        t1 = time.perf_counter()
        lines = self._run_ocr(loaded.ocr_image())
        t2 = time.perf_counter()

        keywords = self.options.normalized_keywords()
        if not keywords:
            result.warnings.append("No keywords configured")
            result.lines = lines
            result.timings = {"load": t1 - t0, "ocr": t2 - t1, "match": 0.0,
                              "regions": 0.0, "mask": 0.0}
            return result

        matches = find_matches(lines, keywords, self.options)

        # Recall fallback: OCR often merges a solid-colour button into one blob and
        # returns garbage for the white label inside it. If a keyword was not found
        # in the main pass, OCR the saturated UI blocks separately.
        missing = self._missing_keywords(matches, keywords)
        t_extra = 0.0
        if missing:
            tex = time.perf_counter()
            extra = self._ocr_colored_blocks(loaded.bgr)
            if extra:
                merged = self._dedupe_lines(lines + extra)
                alt = find_matches(merged, keywords, self.options)
                if len(alt) > len(matches):
                    log.debug("colour-block OCR added %d match(es)", len(alt) - len(matches))
                    lines, matches = merged, alt
            t_extra = time.perf_counter() - tex
        result.lines = lines
        t3 = time.perf_counter()

        result.regions = self._build_regions(loaded.bgr, lines, matches)
        t4 = time.perf_counter()

        for region in result.regions:
            region.mask = mask_generator.build_region_mask(loaded.bgr, region, self.options)
        t5 = time.perf_counter()

        if result.uncertain_regions:
            result.warnings.append(
                f"{len(result.uncertain_regions)} uncertain region(s) need review before removal"
            )
        result.timings = {"load": t1 - t0, "ocr": t2 - t1, "match": t3 - t2,
                          "regions": t4 - t3, "mask": t5 - t4}
        log.info("analyze %s: %d lines, %d regions (%.2fs)",
                 Path(path).name, len(lines), len(result.regions), t5 - t0)
        return result

    def _build_regions(self, bgr: np.ndarray, lines: list[TextLine], matches: list[Match]) -> list[Region]:
        opts = self.options
        h, w = bgr.shape[:2]
        regions: list[Region] = []
        red_mask, red_boxes = self._red_components(bgr)
        for idx, match in enumerate(matches):
            if match.confidence < opts.conf_threshold or not match.localized:
                reason = "low_conf" if match.confidence < opts.conf_threshold else "not_localized"
                region = Region(
                    id=idx, kind=RegionKind.UNCERTAIN,
                    bbox=clip_rect(match.box, w, h),
                    confidence=match.confidence, source="ocr", match=match, enabled=False,
                    evidence={"reason": 1.0, "match_conf": match.confidence, "reason_code": reason},
                )
                regions.append(region)
                continue

            match_boxes = [lines[i].box for i in match.line_ids if 0 <= i < len(lines)]
            other_boxes = [
                ln.box for i, ln in enumerate(lines)
                if i not in match.line_ids
                and not self._overlaps_any(ln.box, match_boxes)
                and not self._is_text_noise(ln)
            ]
            cand = self.detector.detect(bgr, match.box, match_boxes, other_boxes, opts)

            if (cand is not None
                    and self._red_annotation_spans(bgr, cand.bbox, red_mask, red_boxes)):
                # The text sits inside a red annotation that spans a much wider
                # row: the candidate is a piece of that frame, not a button.
                # Removing the whole candidate would cut the frame and card.
                region = Region(
                    id=idx, kind=RegionKind.TEXT,
                    bbox=clip_rect(match.box, w, h),
                    confidence=min(match.confidence, cand.confidence),
                    source="ocr", match=match, enabled=True,
                    evidence={**cand.evidence, "red_annotation": 1.0,
                              "element_conf": cand.confidence},
                )
            elif (cand is not None and cand.source == "snap"
                    and cand.confidence < opts.element_hi):
                # `snap` just picks the nearest straight edges; below the
                # auto-approval threshold it is often accidental chrome around
                # a text line. Remove only the keyword (TEXT); the user can
                # still choose "Element" per region.
                region = Region(
                    id=idx, kind=RegionKind.TEXT,
                    bbox=clip_rect(match.box, w, h),
                    confidence=min(match.confidence, cand.confidence),
                    source="ocr", match=match, enabled=True,
                    evidence={**cand.evidence, "weak_snap": 1.0,
                              "element_conf": cand.confidence},
                )
            # A large, sharp-cornered, saturated flat area is usually a panel /
            # coloured container, not a button. Removing the whole container
            # leaves a page-coloured hole; text-only is the safe default.
            elif (cand is not None and cand.confidence >= opts.element_hi
                    and self._looks_like_container(bgr, cand, match)):
                region = Region(
                    id=idx, kind=RegionKind.TEXT,
                    bbox=clip_rect(match.box, w, h),
                    confidence=min(match.confidence, cand.confidence),
                    source=cand.source, match=match, enabled=True,
                    evidence={**cand.evidence, "container": 1.0,
                              "element_conf": cand.confidence},
                )
            elif cand is not None and cand.confidence >= opts.element_hi:
                region = Region(
                    id=idx, kind=RegionKind.ELEMENT, bbox=clip_rect(cand.bbox, w, h),
                    confidence=min(match.confidence, cand.confidence), source=cand.source,
                    match=match, enabled=True, evidence=dict(cand.evidence),
                )
            elif cand is not None and cand.confidence >= opts.element_lo:
                region = Region(
                    id=idx, kind=RegionKind.UNCERTAIN, bbox=clip_rect(cand.bbox, w, h),
                    confidence=cand.confidence, source=cand.source, match=match, enabled=False,
                    evidence={**cand.evidence, "element_conf": cand.confidence},
                )
            else:
                region = Region(
                    id=idx, kind=RegionKind.TEXT, bbox=clip_rect(match.box, w, h),
                    confidence=match.confidence, source="ocr", match=match, enabled=True,
                    evidence={"element_conf": cand.confidence if cand else 0.0},
                )
            regions.append(region)
        return regions

    @staticmethod
    def _red_components(bgr: np.ndarray):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168))
               & (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 60)).astype(np.uint8)
        if int(red.sum()) < 12:
            return red, []
        n, _labels, stats, _ = cv2.connectedComponentsWithStats(red, 8)
        boxes = []
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < 12:
                continue
            boxes.append((
                int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]),
                area,
            ))
        return red.astype(bool), boxes

    @staticmethod
    def _red_annotation_spans(bgr: np.ndarray, bbox, red_mask, red_boxes) -> bool:
        h, w = bgr.shape[:2]
        x, y, bw, bh = clip_rect(bbox, w, h)
        area = bw * bh
        if area <= 0 or red_mask is None:
            return False
        red_fraction = float(red_mask[y:y + bh, x:x + bw].mean())
        if red_fraction < 0.05:
            return False
        for rx, ry, rw, rh, ra in red_boxes:
            if min(x + bw, rx + rw) <= max(x, rx) or min(y + bh, ry + rh) <= max(y, ry):
                continue
            if ra <= 2.5 * area:
                continue
            if rw > 1.8 * bw or rh > 1.8 * bh:
                return True
        return False

    @staticmethod
    def _looks_like_container(bgr: np.ndarray, cand, match: Match) -> bool:
        """Detect a large saturated flat panel with sharp corners.

        This deliberately does NOT try to read the user's mind: it only catches
        the obvious container case (a coloured panel with text directly on it).
        Rounded pills and bordered buttons keep the ELEMENT path, and the GUI
        still lets the user switch policy per region.
        """
        h_img, w_img = bgr.shape[:2]
        x, y, w, h = clip_rect(cand.bbox, w_img, h_img)
        if w < 8 or h < 8:
            return False

        roi = bgr[y:y + h, x:x + w]
        inside = np.ones((h, w), bool)
        tx, ty, tw, th = clip_rect(match.box, w_img, h_img)
        px1, py1 = max(0, tx - x - 2), max(0, ty - y - 2)
        px2 = min(w, tx - x + tw + 2)
        py2 = min(h, ty - y + th + 2)
        if px2 > px1 and py2 > py1:
            inside[py1:py2, px1:px2] = False
        pixels = roi[inside]
        if pixels.shape[0] < 64:
            return False

        fill = np.median(pixels, axis=0)
        sat = int(cv2.cvtColor(np.uint8([[fill]]), cv2.COLOR_BGR2HSV)[0, 0, 1])
        if sat <= 70:
            return False

        close = np.max(np.abs(roi.astype(np.int16) - fill.astype(np.int16)), axis=2) <= 25
        if float(close[inside].mean()) < 0.85:
            return False

        # Sharp rectangle corners are filled; rounded pills have page colour there.
        corners: list[float] = []
        for cx, cy in ((x + 2, y + 2), (x + w - 3, y + 2),
                       (x + 2, y + h - 3), (x + w - 3, y + h - 3)):
            patch = bgr[max(0, cy - 1):cy + 2, max(0, cx - 1):cx + 2]
            if patch.size == 0:
                continue
            corners.append(float((
                np.max(np.abs(patch.astype(np.int16) - fill.astype(np.int16)), axis=2) <= 25
            ).mean()))
        if not corners or float(np.mean(corners)) < 0.75:
            return False

        text_area = max(1, tw * th)
        return (w * h) >= 8 * text_area

    @staticmethod
    def _overlaps_any(box, boxes) -> bool:
        return any(rect_iou(box, b) > 0.2 for b in boxes)

    @staticmethod
    def _is_text_noise(line: TextLine) -> bool:
        """Pure-symbol OCR output (e.g. a lone '_' over a button) is not content."""
        return sum(ch.isalnum() for ch in line.text) == 0

    @staticmethod
    def _missing_keywords(matches: list[Match], keywords: list[str]) -> list[str]:
        norm = lambda s: s.casefold().strip()  # noqa: E731
        found = {norm(m.keyword) for m in matches}
        return [k for k in keywords if norm(k) not in found]

    def _ocr_colored_blocks(self, bgr: np.ndarray) -> list[TextLine]:
        """Second OCR pass over saturated blocks (buttons/badges) only."""
        import cv2

        h_img, w_img = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        sat, val = hsv[:, :, 1], hsv[:, :, 2]
        blocks = ((sat > 70) & (val > 45)).astype(np.uint8)
        if int(blocks.sum()) < 200:
            return []
        blocks = cv2.morphologyEx(blocks, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(blocks, connectivity=8)

        min_area = max(240, int(0.0002 * h_img * w_img))
        rects: list[tuple[int, int, int, int, int]] = []
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                          int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
            if w < 20 or h < 10 or h > 0.5 * h_img:
                continue
            aspect = w / float(h)
            if aspect > 20.0 or aspect < 0.8:
                continue
            if w * h > 0.3 * float(h_img * w_img):
                continue
            rects.append((area, x, y, w, h))
        rects.sort(reverse=True)

        out: list[TextLine] = []
        for _, x, y, w, h in rects[:8]:
            # Tight crops make the detector treat the whole block as one text line,
            # so keep generous surrounding context.
            margin = max(12, int(0.5 * min(w, h)))
            x1, y1 = max(0, x - margin), max(0, y - margin)
            x2, y2 = min(w_img, x + w + margin), min(h_img, y + h + margin)
            crop = bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            scale = 1.0
            if max(crop.shape[:2]) < 640:
                scale = min(2.0, 640.0 / max(crop.shape[:2]))
                crop = crop_for_ocr(crop, scale)
            for line in self.ocr.recognize(crop):
                inv = 1.0 / scale
                line.quad = [((px + x1) * inv, (py + y1) * inv) for px, py in line.quad]
                for word in line.words:
                    wx, wy, ww, wh = word.box
                    word.box = (int(round((wx + x1) * inv)), int(round((wy + y1) * inv)),
                                max(1, int(round(ww * inv))), max(1, int(round(wh * inv))))
                # Keep only lines that actually sit on this colour block.
                lx, ly, lw, lh = line.box
                cx = max(0, min(lx + lw, x + w) - max(lx, x))
                cy = max(0, min(ly + lh, y + h) - max(ly, y))
                if lw * lh <= 0 or (cx * cy) / float(lw * lh) < 0.5:
                    continue
                line.tile_id = -1
                out.append(line)
        return out

    # ---------------------------------------------------------------------- ocr

    def _run_ocr(self, bgr: np.ndarray) -> list[TextLine]:
        h, w = bgr.shape[:2]
        max_side = max(h, w)
        views: list[tuple[np.ndarray, int, int, float]] = []

        if max_side <= self.options.ocr_max_side:
            scale = 1.0
            if max_side < self.options.ocr_min_side:
                scale = min(self.options.ocr_upscale_limit, self.options.ocr_min_side / max_side)
            views.append((crop_for_ocr(bgr, scale), 0, 0, scale))
        else:
            tile = min(self.options.ocr_max_side, 4096)
            overlap = max(32, self.options.tile_overlap)
            for y in self._grid_positions(h, tile, overlap):
                for x in self._grid_positions(w, tile, overlap):
                    views.append((bgr[y:y + tile, x:x + tile], x, y, 1.0))

        all_lines: list[TextLine] = []
        for tile_id, (view, ox, oy, scale) in enumerate(views):
            lines = self.ocr.recognize(view)
            inv = 1.0 / scale
            for line in lines:
                line.tile_id = tile_id
                line.quad = [((px + ox) * inv, (py + oy) * inv) for px, py in line.quad]
                for word in line.words:
                    wx, wy, ww, wh = word.box
                    word.box = (int(round((wx + ox) * inv)), int(round((wy + oy) * inv)),
                                max(1, int(round(ww * inv))), max(1, int(round(wh * inv))))
                    if word.char_boxes:
                        word.char_boxes = [
                            (int(round((cx + ox) * inv)), int(round((cy + oy) * inv)),
                             max(1, int(round(cw * inv))), max(1, int(round(ch * inv))))
                            for cx, cy, cw, ch in word.char_boxes
                        ]
                all_lines.append(line)

        if len(views) > 1:
            all_lines = self._dedupe_lines(all_lines)
        return all_lines

    @staticmethod
    def _grid_positions(total: int, tile: int, overlap: int) -> list[int]:
        if total <= tile:
            return [0]
        step = max(1, tile - overlap)
        n = max(2, math.ceil((total - overlap) / step))
        return sorted({int(round(i * (total - tile) / (n - 1))) for i in range(n)})

    @staticmethod
    def _dedupe_lines(lines: list[TextLine]) -> list[TextLine]:
        kept: list[TextLine] = []
        for line in sorted(lines, key=lambda ln: -ln.score):
            norm = normalize_text(line.text)
            dup = False
            for other in kept:
                if rect_iou(line.box, other.box) > 0.30 and normalize_text(other.text) == norm:
                    dup = True
                    break
            if not dup:
                kept.append(line)

        # Drop detector junk (e.g. a lone "_" spanning a whole button) that sits
        # on top of a real text line: it would otherwise block element detection.
        def alnum(text: str) -> int:
            return sum(ch.isalnum() for ch in text)

        out: list[TextLine] = []
        for line in kept:
            if alnum(line.text) < 2:
                covered = any(
                    other is not line
                    and alnum(other.text) >= 2
                    and rect_intersection_area(line.box, other.box) / max(1, rect_area(other.box)) > 0.5
                    for other in kept
                )
                if covered:
                    continue
            out.append(line)
        return out

    # ------------------------------------------------------------------- render

    def render(self, loaded: LoadedImage, result: AnalysisResult,
               regions: list[Region] | None = None) -> tuple[np.ndarray, np.ndarray, str]:
        regions = result.regions if regions is None else regions

        # Render each region separately. A single union mask makes the background
        # estimator see several containers at once (gray card + blue box), so one
        # colour wins and the other region is restored with the wrong background.
        out = loaded.bgr.copy()
        union_mask = np.zeros(loaded.bgr.shape[:2], np.uint8)
        engine = self._inpaint or create_engine(self.options)
        self._inpaint = engine
        last_engine = "none"
        rendered = 0

        for region in regions:
            if not region.enabled:
                continue

            protected = np.zeros(loaded.bgr.shape[:2], bool)
            refined = None
            # Post-detection refinement: keep unrelated text/icons intact. This
            # changes only masks, never detection boxes or region classification.
            if (getattr(self.options, "refine_mask", True)
                    and mask_generator.region_is_element(region, self.options)):
                refined = refine_element_mask(
                    loaded.bgr, region, result.lines, self.options
                )
                mask = refined.mask.astype(np.uint8) * 255
                protected = refined.protected.astype(bool)
            else:
                mask = mask_generator.build_region_mask(
                    loaded.bgr, region, self.options
                )

            region.mask = mask
            box = mask_bbox(mask)
            if box is None:
                continue
            fill_hint = mask_generator.region_text_fill(
                loaded.bgr, region, self.options
            )

            # Inpaint only a padded crop around the mask. The background model,
            # guide sanitizer and engine no longer scan the whole 4K image for
            # every region (the annulus/band need at most ~20-30 px).
            bx, by, bw, bh = box
            pad = max(32, int(0.25 * max(bw, bh)))
            x1, y1 = max(0, bx - pad), max(0, by - pad)
            x2 = min(loaded.width, bx + bw + pad)
            y2 = min(loaded.height, by + bh + pad)
            crop = loaded.bgr[y1:y2, x1:x2].copy()
            mask_crop = mask[y1:y2, x1:x2]
            protected_crop = protected[y1:y2, x1:x2]
            filled = engine.inpaint(crop, mask_crop, protected=protected_crop)
            if fill_hint is not None:
                filled = filled.copy()
                filled[mask_crop > 0] = fill_hint
            sel = mask_crop > 0
            if sel.any():
                target = out[y1:y2, x1:x2]
                target[sel] = filled[sel]
                out[y1:y2, x1:x2] = target
            if refined is not None:
                sel = refined.restore_pixels
                if sel.any():
                    out[sel] = refined.restore_values[sel]

            union_mask = cv2.bitwise_or(union_mask, mask)
            last_engine = (
                getattr(engine, "last_engine", None)
                or getattr(engine, "name", "unknown")
            )
            rendered += 1

        if rendered == 0:
            return loaded.bgr.copy(), union_mask, "none"
        return out, union_mask, last_engine

    # ------------------------------------------------------------------ process

    def process(self, path: str | Path, out_dir: str | Path,
                debug_dir: str | Path | None = None) -> ProcessReport:
        path = Path(path)
        t0 = time.perf_counter()
        report = ProcessReport(str(path), ImageStatus.FAILED)
        try:
            loaded = load_image(path)
            result = self.analyze(path, loaded)
            report = self.write_result(loaded, result, out_dir, debug_dir=debug_dir)
        except Exception as exc:  # noqa: BLE001 - one bad image must not kill a batch
            report.error = f"{type(exc).__name__}: {exc}"
            log.exception("failed to process %s", path)
        report.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return report

    def write_result(
        self,
        loaded: LoadedImage,
        result: AnalysisResult,
        out_dir: str | Path,
        debug_dir: str | Path | None = None,
        regions: list[Region] | None = None,
    ) -> ProcessReport:
        """Render an existing analysis and write it. Shared by CLI and GUI.

        `regions` lets the GUI pass an explicit, user-approved selection. When it
        is omitted the safe default applies: enabled regions only, and regions
        classified UNCERTAIN are never removed.
        """
        path = Path(loaded.path)
        report = ProcessReport(str(path), ImageStatus.FAILED)
        report.regions_total = len(result.regions)
        if regions is not None:
            actionable = [r for r in regions if r.enabled]
        else:
            actionable = [
                r for r in result.regions
                if r.enabled and r.kind is not RegionKind.UNCERTAIN
            ]
        actionable_ids = {id(r) for r in actionable}
        uncertain = [
            r for r in result.regions
            if r.kind is RegionKind.UNCERTAIN and id(r) not in actionable_ids
        ]
        if uncertain:
            log.warning(
                "%s: %d uncertain region(s) left untouched: %s",
                path.name, len(uncertain),
                [(r.bbox, round(r.confidence, 2)) for r in uncertain],
            )

        if not result.regions:
            report.status = ImageStatus.NOT_FOUND
            return report

        if not actionable:
            report.status = ImageStatus.SKIPPED_UNCERTAIN
            report.regions_skipped = len(uncertain)
            return report

        # Rebuild masks with the current options (the user may have changed
        # padding or dragged a box since analysis).
        for region in actionable:
            region.mask = None

        out_img, mask, engine_name = self.render(loaded, result, actionable)
        if mask_bbox(mask) is None:
            report.status = ImageStatus.NOT_FOUND
            return report

        suffix = path.suffix
        fmt = None
        if self.options.output_format == "png":
            suffix, fmt = ".png", "PNG"
        elif self.options.output_format == "jpg":
            suffix, fmt = ".jpg", "JPEG"
        elif self.options.output_format == "same":
            # Follow the output extension, not a possibly mismatched container
            # format from the source file (e.g. WebP bytes saved as `.png`).
            # This keeps `.png` output truly lossless.
            fmt = {
                ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
                ".bmp": "BMP", ".webp": "WEBP",
                ".tif": "TIFF", ".tiff": "TIFF",
            }.get(suffix.lower())
        out_path = unique_output_path(Path(out_dir), path.stem, suffix)
        unchanged = np.array_equal(out_img, loaded.bgr)
        same_format = fmt is None or fmt.upper() == loaded.fmt.upper()
        if unchanged and same_format:
            try:
                shutil.copyfile(path, out_path)
            except OSError as exc:
                log.warning("could not copy unchanged image %s: %s", path, exc)
                save_image(loaded, out_img, out_path, fmt=fmt,
                           jpeg_quality=self.options.jpeg_quality)
        else:
            save_image(loaded, out_img, out_path, fmt=fmt,
                       jpeg_quality=self.options.jpeg_quality)

        try:
            src_size = path.stat().st_size
            out_size = out_path.stat().st_size
            if out_size > src_size:
                log.warning(
                    "%s: saved file is larger than the source (%.1f KB -> %.1f KB); "
                    "lossless encoding can grow palette or lossy inputs",
                    path.name, src_size / 1024, out_size / 1024,
                )
        except OSError:
            pass

        if debug_dir is not None:
            dbg = Path(debug_dir)
            dbg.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(dbg / f"{path.stem}_overlay.png"),
                        overlay_regions(loaded.bgr, result.regions))
            cv2.imwrite(str(dbg / f"{path.stem}_mask.png"), mask)

        report.status = ImageStatus.SUCCESS
        report.output_path = str(out_path)
        report.regions_removed = len(actionable)
        report.regions_skipped = len(uncertain)
        log.info("processed %s -> %s (engine=%s, %d region(s))",
                 path.name, out_path.name, engine_name, len(actionable))
        return report

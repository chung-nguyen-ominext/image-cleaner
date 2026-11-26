# ImageCleaner – Technical Design (Phase 1)

> Status: **Phase 1 proposal (historical)**. All performance/size numbers are estimates
> and are marked `[verify]` if they need to be re-measured in Phase 1.
> Date: 2026-11-25 · Reference environment: Ubuntu 24.04, Python 3.12.3, 8 cores, 31 GB RAM, **no GPU**.
>
> **Phase 8 update (2026-11-26)**: the render architecture has moved to **per-region**,
> frame/container classifier, protected icon and preview = saved. See
> `docs/STATUS.md` sections 14–15 and `docs/OPTIMIZATION_PLAN.md`.

## Decisions locked in (2026-11-25)

| # | Item | Decision |
|---|---|---|
| D1 | OCR | **RapidOCR 3.x + onnxruntime CPU**, with an `OCREngine` interface for plugging in more backends |
| D2 | Inpainting | **Bundle LaMa ONNX (~207 MB) as the default**, Telea/NS fallback + fast preview |
| D3 | Mask editing MVP | **Region toggle + box drag/resize + padding slider** (no brush/polygon) |
| D4 | Batch + UNCERTAIN regions | **Skip, do not erase, write `SKIPPED_UNCERTAIN` to the report** |
| D5 | Packaging | `--onedir`, `opencv-python-headless`, separate artifact per OS |
| D6 | Output format | Keep the original format (JPEG q=95, PNG lossless), never overwrite, option to export PNG |

---

## 0. Current environment state (actually verified)

| Item | Result | Impact |
|---|---|---|
| OS | Ubuntu 24.04.3 LTS | Linux can be built locally |
| Python | 3.12.3 | Satisfies `>=3.11` |
| pip / venv | **not available** (`python3 -m pip` fails) | Needs `python3-venv` (sudo password) **or** use `uv` (recommended) |
| Tesseract | **not available** | Choosing Tesseract means installing the binary + tessdata → heavier packaging burden |
| cv2 / numpy / PySide6 / onnxruntime | **not installed** | Installed in Phase 2 |
| GPU | none | The whole pipeline must be CPU-only |
| Project folder | empty, not a git repo yet | `git init` + skeleton in Phase 2 |
| Network | available | Only used to install deps / download models **at build time**; runtime must be offline |

Conclusion: the environment is sufficient for the MVP, but **an environment setup step is needed** (uv or apt python3-venv)
before coding.

---

## 1. Overall architecture

### 1.1 Principles

1. **Two separate phases**: `analyze()` (read-only, returns detected regions) and `render()` (inpaint + write file).
   → Allows preview/mask editing without re-running OCR, and lets batch run a "dry-run" first.
2. **No hidden state**: every decision (which region, what kind, confidence, reason) lives in
   `AnalysisResult` → displayable in the UI, testable, loggable.
3. **Fail-safe**: doubt = do not erase. The original image is never overwritten.
4. **Pluggable engines**: OCR and Inpainting behind interfaces; swapping backends does not change the pipeline.
5. **Safety invariant**: outside the mask, the output must be **pixel-identical** to the original (`np.array_equal`) — covered by a unit test.

### 1.2 Pipeline

```
Options(keywords, thresholds, policy, padding, engine)
   │
   ▼
[1] ImageLoader        read bytes → BGR uint8, EXIF orientation, alpha, ICC/DPI metadata
   │
   ▼
[2] Preprocess         tile if the image is large (>2560px) · upscale if small (<1000px)
   │
   ▼
[3] OCREngine          List[TextLine{text, quad, box, score, tile}]
   │                   (dedupe tile overlap by IoU + text similarity)
   ▼
[4] TextMatcher        normalize → exact/fuzzy → List[Match{line_ids, span, box, score}]
   │
   ▼
[5] RegionProposer     for each Match: text mask + UI element probing (cascade) → Region
   │                   Region{kind: TEXT|ELEMENT|UNCERTAIN, bbox, mask, confidence, evidence}
   ▼
[6] MaskGenerator      union masks, dilate, feather, clamp to image bounds
   │
   ▼
[7] Review (UI/CLI)    toggle / move / resize / padding / change kind / auto-policy
   │
   ▼
[8] InpaintEngine      per-region crop → inpaint → feathered blend
   │
   ▼
[9] Writer + Report    write new file (no overwrite), CSV/JSON summary, log
```

### 1.3 Data / thread flow (UI)

- GUI thread: drawing + receiving signals only.
- `QThread` worker: analyze and render (2 separate job kinds, each with a cancel flag).
- Batch: processes **images sequentially** (low RAM, easy progress reporting, one failing image does not kill the batch);
  can be parallelized with `ProcessPoolExecutor` later if needed.
- Signals: `progress(file_idx, total, stage)`, `image_done(result)`, `log(level, msg)`, `finished(summary)`.

### 1.4 Data model (dataclasses, no Qt dependency)

```text
TextLine   : text, quad[[x,y]×4], box(x,y,w,h), score, tile_id
Match      : keyword, line_ids, char_span, box, text_score, match_score
Region     : id, kind, bbox, quad?, mask(np.uint8|None), confidence,
             evidence(dict), source('ocr'|'manual'), enabled(bool)
AnalysisResult : image_path, width, height, lines, matches, regions,
                 warnings[], timings{}
ProcessOptions : keywords[], conf_threshold, match_mode, element_policy,
                 padding_px, mask_mode, inpaint_engine, output_format, ...
ProcessReport  : status(SUCCESS|NOT_FOUND|SKIPPED_UNCERTAIN|FAILED),
                 regions_total/removed, output_path, error, elapsed_ms
```

---

## 2. Technology choices

### 2.1 OCR — comparison and decision

| Criterion | Tesseract 5 (`pytesseract`) | PaddleOCR | **RapidOCR (ONNX)** |
|---|---|---|---|
| Offline | ✅ | ✅ | ✅ |
| Cross-platform packaging | ❌ separate binary + tessdata per OS, Gatekeeper/DLL | ❌ paddlepaddle ~500MB+, builds often fail | ✅ pure pip wheel, model bundled in the wheel |
| Python 3.12 | ✅ | ⚠️ | ✅ |
| Word-level box | ✅ (`image_to_data`) | line-level | line-level |
| UI screenshot / dense text quality | average (threshold-sensitive, poor with blurry/low-contrast text) | good | good (PP-OCR det/rec) |
| Size | ~30–50MB/OS | >500MB | 27MB wheel `[verify: model bundled]` |
| Runtime deps | external binary | paddle | onnxruntime (~25MB) — **shared with LaMa** |

**Decision: RapidOCR 3.x + onnxruntime CPU** as the primary engine.
Reasons: (a) no external binary → simple PyInstaller, matching the "nothing extra to install" requirement;
(b) onnxruntime is **reused** for LaMa → one fewer large dependency; (c) stable on all 3 OSes;
(d) better quality than Tesseract on screenshots.

Trade-off: RapidOCR returns **line-level** boxes, with no word boxes.
→ Compensated by the pixel-level mask design (section 4), so mask **accuracy does not depend** on word boxes.
→ The `OCREngine` interface is kept so Tesseract (word boxes) can be plugged in as a secondary backend if needed.

**Handling large/small images** (the "many sizes, resolutions" requirement):
- max-side > 2560 → tile 2×2 or 3×3, 15% overlap, dedupe lines by IoU + text similarity.
- max-side < 1000 → upscale ×2 (INTER_CUBIC) before OCR, then map coordinates back to the original image.

### 2.2 Matching — Dynamic Text Detection

Normalization (in order, each step can be toggled):

1. Unicode **NFKC** (full-width → ASCII, ligatures).
2. `casefold()` → case-insensitive.
3. Punctuation/typography normalization: `’‘“”–—` → `'`, `"`, `-`.
4. **Collapse whitespace** (every `\s+` → 1 space) → "tolerate differences in whitespace".
5. Optional: remove whitespace entirely (OCR often inserts spaces between letters) — off by default.

Algorithm:
- **Exact**: find every substring occurrence in the normalized text; by default require
  **word boundaries** (adjacent characters not alnum) so `VPN` does not match `VPNClient`.
- **Fuzzy** (on by default, configurable threshold): sliding token window, matched with
  `difflib.SequenceMatcher` (or `rapidfuzz` if one extra ~1MB dep is acceptable) → ratio ≥ 0.85.
  Catches OCR errors like `ConfidentiaI`, `Conf idential`.
- **Multi-line**: group lines into blocks by vertical/horizontal distance, join with a space → match phrases
  spanning 2 lines (common in watermarks/2-line labels).
- **Multiple occurrences**: scan everything, do not stop at the first match.
- `match_score` = match ratio; `region confidence` = `min(OCR score of the lines) × match_score`.
- Default threshold 0.60; **below the threshold is still shown in the preview** but in a "low-confidence,
  deselected by default" state → the user decides (per requirement 3.6).

No keyword is hardcoded; the keyword set comes from the UI/preset JSON and may be empty → the CLI reports a clear error.

### 2.3 Inpainting — decision

| Engine | Flat background quality | Gradient/textured background | CPU speed | Size | Verdict |
|---|---|---|---|---|---|
| OpenCV Telea | good, sharp | poor (smudges, "drags" color) | 0.05–0.3s | 0 | **used for small masks / flat backgrounds / instant preview** |
| OpenCV NS | similar, smoother | poor | similar | 0 | fallback |
| **LaMa ONNX** (`Carve/LaMa-ONNX`, ~207MB) | good | **best available for offline CPU** | 0.6–2.5s / 512 crop `[verify]` | 207MB | **default for large regions / complex backgrounds** |
| Diffusion (SD inpaint) | very good | very good | 30–120s/image CPU | >2GB | ❌ too heavy for batch + packaging |
| PatchMatch | good | fair | slow, no standard wheel | – | ❌ |

**Decision**: an `InpaintEngine` interface with 4 backends `telea | ns | lama | auto`.
`auto` picks per region: small mask area + low background variance → Telea; otherwise → LaMa.
Missing LaMa model → automatically fall back to Telea + warning (no crash).

LaMa technical details:
- Run **per crop** (bbox + pad ~32–64px, expanded to a square, resized to 512×512) instead of resizing the whole image
  → preserves background detail, much faster on 4K images.
- Regions larger than 512 in the original image → split into overlapping tiles then blend.
- Blend back with a feathered mask (distance transform / Gaussian) so no seam is visible.
- `[verify]` the actual ONNX input shape (fixed 512 or dynamic) — check right in Phase 2, it affects the crop pipeline.

### 2.4 Why `opencv-python-headless`

PySide6 + `opencv-python` (the GUI build) conflict over Qt plugins → the classic error
`Could not load the Qt platform plugin "xcb"`. Using **headless** avoids it entirely and is also ~20MB lighter.
RapidOCR 3.x already declares `opencv-python-headless` → consistent.

---

## 3. UI element (button) detection — the hardest part

There is no DOM/accessibility tree in a screenshot, so being "100% correct" with heuristics is **impossible**.
Strategy: **cascade multiple evidence sources + scoring + conservative thresholds + mandatory review when in doubt**.

### 3.1 Candidate generation (5 independent sources)

For each `Match` with box `T`:

- **A. Fill-region (flood fill)**: take the background color around the text (ring around `T`), flood fill in
  **Lab** space with a ΔE tolerance. If the text sits on a uniform fill (flat button) → the component containing `T` is a candidate.
- **B. Contour containment**: Canny (automatic threshold from the median) → morphological close → `findContours(RETR_TREE)`
  → contours that **contain** `T` (with margin) and whose area is within `[1.2×, 12×]` of the text area.
- **C. Gradient snapping**: from the 4 sides of `T`, scan 1D profiles outward, find the strongest edge gradient
  **consistent over ≥70% of the edge length** → 4 lines → a rectangle.
- **D. Background-difference**: estimate the local background (median of the outer ring) → diff mask → component containing `T`.
- **E. Rounded-rect fit**: from the mask of A/B, `minAreaRect`, check fill-ratio and corner radius.

Merge duplicate candidates (IoU > 0.8) before scoring.

### 3.2 Scoring (0..1, weighted)

| Feature | Meaning | Good direction |
|---|---|---|
| `containment` | candidate contains `T` + margin of 2–0.5× text height | high |
| `tightness` | `area(cand)/area(T)`, expected 1.4–8 (peak 2–4) | in range |
| `rectangularity` | `contour_area / minAreaRect_area` | ≥ 0.80 |
| `fill_uniformity` | standard deviation in Lab inside (excluding text pixels) | low |
| `border_evidence` | gradient along the candidate border, 4-side consistency | high |
| `text_coverage` | text area / candidate area | 3–25% |
| `shape_prior` | aspect ratio 0.8–8, reasonable size relative to the image | in range |
| **`no_unrelated_text`** | the candidate **does not contain other OCR lines** that are not part of the match | **mandatory** |

`no_unrelated_text` is the **most important guard**: if a candidate "swallows" other text
→ it is a card/panel/sidebar, **not a button** → reject or heavily penalize.
Also reject candidates that touch image borders too much or cover >35% of the image.

### 3.3 Three-level decision

```
conf ≥ θ_hi (0.75)                 → ELEMENT  : mask = whole element + dilate
θ_lo (0.45) ≤ conf < θ_hi          → UNCERTAIN: show preview, WAIT for user confirmation
conf < θ_lo                        → TEXT     : mask = text pixels + small dilate
```

- `θ_hi/θ_lo` are configurable; **calibrate on the test image set** (synthetic + real images provided by the user).
- Batch mode default: `UNCERTAIN` → **skip, do not erase**, record in the report (`SKIPPED_UNCERTAIN`).
  The user can choose "auto-approve" for specific images in the UI.
- This is how the "do not auto-erase when uncertain" requirement is implemented.

### 3.4 Known limitations (stated upfront, not hidden)

- Gradient/glassmorphism/complex-photo-background buttons: the cascade may fail → falls back to `TEXT` or `UNCERTAIN`.
- Buttons with icon + text, toolbar rows, tabs: usually `UNCERTAIN` → the user drags the box.
- Elements without clear borders (text links, breadcrumbs) cannot be handled → as intended: only erase the text.
- Upgrade path (beyond 2 days): a UI element detection model (ONNX, e.g. OmniParser/YOLO fine-tune)
  — needs data + time, out of MVP scope.

---

## 4. Mask Generation

Two modes:

1. **`text` (pixel-level)** — default when there is no element:
   - Crop box + small pad → Otsu/adaptive threshold (both light/dark directions, pick the direction that yields a reasonably "sparse" mask)
   - Morphological close (3×3) to connect strokes, remove small specks
   - Dilate 1–2px + feather 2px
   - If the mask is too empty (<2% pixels) or too dense (>85%) → fall back to **full bbox** (safer to erase too much than to miss strokes).
   - Advantage: keeps the background between strokes (gradient/patterned backgrounds) → matches the "only erase the text region" requirement.
2. **`element`** — mask = contour/fill of the element (fitted to a rounded rect), dilate 2–3px, clamped to the image.

Additional:
- Global **padding** (slider, default 2px) + per-region padding.
- **Toggle each region** (checkbox) and **drag/resize boxes** in the preview.
- Union masks per image; **clip** to the borders; store masks as `uint8 0/255`.
- Alpha channel: inpaint RGB, keep alpha unchanged (for transparent PNGs).
- All mask operations are based on **original image coordinates**; the preview only scales when drawing (avoids drift from zoom/DPI).

---

## 5. Source code structure (proposed, close to the brief plus a few necessary additions)

```text
image-cleaner/
├── pyproject.toml                  # metadata + deps + entrypoint `imagecleaner`
├── requirements.txt                # pinned versions for builds
├── README.md
├── docs/
│   ├── DESIGN.md                   # this document
│   ├── USAGE.md
│   └── BUILD.md
├── models/
│   ├── manifest.json               # file name + sha256 + license + source
│   └── lama/lama_fp32.onnx         # do NOT commit (downloaded at build); the manifest is committed
├── src/
│   ├── main.py                     # GUI entry
│   ├── cli.py                      # CLI entry (Day 1 gate)
│   ├── config/
│   │   ├── settings.py             # option dataclasses, defaults, JSON load/save
│   │   └── paths.py                # app dir, model dir, user config dir (3 OSes)
│   ├── core/
│   │   ├── models.py               # shared dataclasses
│   │   ├── image_loader.py
│   │   ├── text_detector.py        # normalize + match + OCR orchestration
│   │   ├── ui_element_detector.py  # cascade + scoring
│   │   ├── mask_generator.py
│   │   ├── inpainting_engine.py    # registry + auto policy + crop/blend
│   │   ├── pipeline.py             # analyze() / render() (wires the steps together)
│   │   └── batch_processor.py
│   ├── engines/
│   │   ├── ocr_engine.py           # Protocol + RapidOCREngine (+ optional TesseractEngine)
│   │   ├── opencv_inpainting.py
│   │   └── lama_inpainting.py
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── preview_widget.py       # zoom/pan, box overlay, drag/resize, toggle
│   │   ├── region_list_widget.py   # region list + confidence + kind
│   │   ├── keyword_panel.py        # manage + save/load presets
│   │   └── worker.py               # QThread jobs
│   └── utils/
│       ├── logger.py               # console + rotating file + Qt signal handler
│       ├── image_utils.py          # read/write, metadata, feather, mask ops
│       └── geometry.py             # IoU, scale, clamp
├── packaging/
│   ├── imagecleaner.spec
│   ├── linux/build_appimage.sh
│   ├── windows/build.ps1
│   └── macos/build_dmg.sh
├── scripts/
│   ├── fetch_models.py             # download + verify sha256 (runs at build, not runtime)
│   └── gen_test_images.py          # generate synthetic test images
├── tests/
│   ├── fixtures/                   # synthetic images + real images (cleared for sharing)
│   ├── test_matcher.py
│   ├── test_element_detector.py
│   ├── test_mask_safety.py         # pixels outside the mask are unchanged
│   └── test_pipeline_e2e.py
└── .github/workflows/build.yml     # 3-OS matrix
```

Differences from the brief: added `core/models.py`, `core/pipeline.py`, `cli.py`, `config/paths.py`,
`packaging/`, `scripts/`, `docs/`. Reasons: separate state from the UI, provide a CLI entry for automated testing,
and consolidate packaging logic for CI reuse.

---

## 6. Offline operation strategy

1. **Models are only downloaded at build time**: `scripts/fetch_models.py` downloads LaMa (+ RapidOCR if not bundled in the wheel),
   verifies **sha256** against `models/manifest.json`, and places them in `models/`.
2. **Model lookup order at runtime**:
   `IC_MODEL_DIR` → the folder next to the executable (`sys._MEIPASS` for onefile / `_internal/` for onedir)
   → `~/.config/ImageCleaner/models` → `./models`.
3. **No network at runtime**: no telemetry, no auto-update, no API calls.
   If a model is missing → the AI engine's `is_available() == False` → fall back to OpenCV + a clear warning in the UI/log.
4. **Real offline test**: run the pipeline under `unshare -n` (Linux) and in CI with the network step
   already finished before the tests → ensures there are no hidden network calls.
   ⚠️ Specific risk: RapidOCR may try to download models externally if misconfigured → **must be verified** in Phase 2.
5. Keyword presets, log, settings: stored in the user config dir (`QStandardPaths.AppConfigLocation`),
   never in the install folder (read-only on macOS/Windows Program Files).

---

## 7. Three-platform build strategy

PyInstaller **does not cross-compile** → use a GitHub Actions matrix, as in the brief.

| OS | Runner | Artifact | Notes |
|---|---|---|---|
| Linux | `ubuntu-22.04` | `*.tar.gz` (onedir) + `*.AppImage` | Build on **22.04** (glibc 2.35) so it runs on older distros; avoid building on 24.04 (glibc 2.39). AppImage needs FUSE, document the `--appimage-extract-and-run` fallback |
| Windows | `windows-2022` | `*_win64.zip` (onedir), optional Inno Setup `.exe` | Bundle VC runtime, Qt plugins; headless startup test in CI |
| macOS | `macos-13` (x86_64) + `macos-14` (arm64) | `.app` inside `.dmg` | Build **per arch** (universal2 + onnxruntime is error-prone); unsigned → document `xattr -dr com.apple.quarantine` / right-click Open |

Packaging decisions:
- **`--onedir`** (not onefile): fast startup, models live outside the exe so they are easy to verify/sha256,
  easier debugging. Linux additionally ships an AppImage built from the onedir folder.
- `excludes`: `torch`, `paddle`, `tensorflow`, `matplotlib`, `tkinter`, `PySide6.QtWebEngine*`,
  `PySide6.Qt3D*`, tests, `pip`.
- `datas`: `models/`, `rapidocr` config/yaml/onnx, assets/icon.
- `hiddenimports`: `onnxruntime.capi._pybind_state`, `rapidocr` submodules, `cv2`.
- Size estimate `[verify]`: PySide6 ~120MB + opencv ~40MB + onnxruntime ~25MB + rapidocr 27MB
  + numpy/misc ~40MB + LaMa 207MB ≈ **~450MB**, zip ~350–400MB. Within GitHub artifact limits.
- **Signing/notarization**: out of scope for 2 days → state clearly in the README that the build is unsigned.
- CI must run: `pytest` + a pipeline smoke test on synthetic images (headless `QT_QPA_PLATFORM=offscreen`).
- Separate artifact per OS + `sha256sums.txt` checksums.

Honest about verification: the current machine can only build/test **Linux**.
Windows/macOS will be at the "build + smoke test passes on CI" state, **not runtime-tested**
→ this will be stated exactly that way in the report, without claiming "full support".

---

## 8. Two-day plan (MVP)

### Day 1 — Core (≈10h, gated)

| Hours | Task | Output |
|---|---|---|
| 0.0–0.5 | setup (uv/venv, deps, `git init`, skeleton, logging, settings) | repo runs `python -m src.cli --help` |
| 0.5–2.0 | `image_loader`, `image_utils`, `gen_test_images` (synthetic: flat/gradient/dark backgrounds, rounded-corner buttons, text on photo) | a set of 8–10 test images |
| 2.0–4.0 | `ocr_engine` (RapidOCR) + tiling/upscale + dedupe; `text_detector` matcher | matcher tests pass; OCR boxes correct on fixtures |
| 4.0–6.0 | `ui_element_detector` cascade + scoring + θ calibration | confidence table on fixtures, with the `no_unrelated_text` guard |
| 6.0–7.5 | `mask_generator` (text-pixel + element) + Telea/NS + LaMa crop/blend | first output images |
| 7.5–9.0 | `pipeline` + `cli.py` end-to-end + pixel safety test | CLI processes one image with arbitrary keywords |
| 9.0–10.0 | buffer + Telea vs LaMa comparison + record actual measurements | Phase 1 report |

**Gate Day 1**: the CLI processes ≥6/8 test images; 100% of pixels outside the mask are unchanged; no unrelated
text is erased; LaMa runs on CPU in acceptable time.

### Day 2 — Batch + UI + Build (≈10h)

| Hours | Task |
|---|---|
| 0.0–1.5 | `batch_processor`: folder walk, jpg/jpeg/png filter, collision-safe writer, CSV/JSON report, log/image, one failing image does not stop the batch |
| 1.5–4.0 | PySide6 main window: keyword panel (+save/load preset), input/output folder, options, counters, progress, log viewer |
| 4.0–5.5 | `preview_widget`: zoom/pan, box overlay colored by kind/confidence, toggle, drag/resize, padding slider, change kind, Analyze/Process buttons |
| 5.5–6.5 | worker thread + cancel + `UNCERTAIN` state (blocks auto-delete, shows a warning) |
| 6.5–7.5 | **early PyInstaller Linux build** + run the packaged build (offline, no network) |
| 7.5–8.5 | `.github/workflows/build.yml` 3-OS matrix + smoke test + artifacts; try Windows/macOS builds on CI |
| 8.5–9.5 | README/USAGE/BUILD, screenshots, clearly note what has/has not been verified |
| 9.5–10.0 | buffer |

### Cut from the MVP (if short on time, in order)
1. Brush/polygon mask painting → box editing only.
2. Windows Inno Setup installer → portable zip only.
3. Tesseract backend.
4. Multi-line keyword matching.
5. AppImage → tar.gz only.
6. macOS both arches → prioritize arm64.

---

## 9. Testing

**Image set**: synthetic generation (controlled ground truth) + **3–5 real screenshots from the user are needed**
(important: synthetic does not represent real UIs, dark mode, 125%/150% scaling).

Mandatory cases:
1. Flat, rounded-corner button, white text on a blue background → erase **the whole button**.
2. Standalone text on a gradient background → erase only the text, keep the gradient.
3. Multiple occurrences of the same keyword (3–5 places) → erase all.
4. Keyword not present → `NOT_FOUND`, image unchanged (byte-identical for PNG).
5. Other text in the same image → **unaffected**.
6. A button containing other text (card/panel) → **not** erased entirely (guard).
7. 4K image → tiling works.
8. Dark mode, low-contrast text → low confidence → `UNCERTAIN`.
9. Batch of 30 images including 1 corrupt file → batch completes, report is correct.
10. Keyword presets save/load correctly, case-insensitive.
11. Offline: block the network → still runs (Telea) and reports the missing model (LaMa) correctly.

**Metrics**: precision/recall of erase regions on fixtures; % of pixels changed outside the mask = 0 (hard assert);
time/image; peak RAM.

---

## 10. Risks & mitigations

| # | Risk | Level | Mitigation |
|---|---|---|---|
| 1 | Element detector erases too much (swallows cards/panels) | **High** | `no_unrelated_text` guard, conservative thresholds, `UNCERTAIN` requires confirmation, batch skips uncertain, preview by default |
| 2 | OCR performs poorly on small/blurry/dark-mode text | High | Tiling + upscale, low confidence → show but do not auto-erase, allow adding boxes manually |
| 3 | LaMa ONNX wrong shape / slow / poor quality | Medium | Verify early on Day 1; Telea fallback; measure real timings; if bad → OpenCV default and LaMa optional |
| 4 | PyInstaller missing hidden imports (onnxruntime, rapidocr yaml, Qt plugins) | **High** | Build Linux **early** (mid Day 2), not at the end; smoke test in CI; `--collect-data rapidocr` |
| 5 | macOS unsigned → Gatekeeper blocks; universal2 fails | Medium | Build per arch, README explains how to open; no notarization promise |
| 6 | Linux glibc/AppImage FUSE | Medium | Build on 22.04; also provide tar.gz |
| 7 | Runtime network calls (RapidOCR downloading models) | Medium | `unshare -n` test, explicit model_root_dir config, clear log |
| 8 | JPEG re-encode loses quality | Medium | Keep original format with quality 95 + `optimize=False`; optional PNG export; documented clearly |
| 9 | 2 days is not enough for complex mask editing | High | Cut scope per section 8; box editing only in the MVP |
| 10 | 4K images with many regions → RAM/time | Low–Med | Process images sequentially, crop-based LaMa, OCR tiling |
| 11 | No real screenshots for testing | **High** (quality) | Ask the user for 3–5 images right in Phase 2; otherwise quality is only guaranteed on synthetic data |

---

## 11. Definition of done (maps to the 13 acceptance criteria)

| # | Criterion | How to demonstrate |
|---|---|---|
| 1 | Enter arbitrary keywords | UI demo + preset JSON |
| 2 | No hardcoded VPN | grep the source, test with other keywords |
| 3 | OCR detects correctly | output images + overlay |
| 4 | Distinguish standalone text vs text inside a button | `kind` table in the preview |
| 5 | Erase the whole button | test case 1 |
| 6 | Acceptable background restoration | before/after images, Telea vs LaMa comparison |
| 7 | Other regions unaffected | pixel-equality test outside the mask |
| 8 | Preview + confirm | UI demo |
| 9 | Batch | run 30 images + report |
| 10 | Desktop UI | PySide6 app |
| 11 | Offline | `unshare -n` test + packaged build |
| 12 | 3-OS build | CI artifacts (Linux runtime verified, Win/mac CI build only) |
| 13 | Documentation | README/USAGE/BUILD |

> **Phase 9 update (2026-11-26)**: acceptance `evidence/e1–e2`, per-region policy, wipe compare, drag-drop and I5 symmetry. See `docs/STATUS.md` section 16.

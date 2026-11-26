# ImageCleaner – Project status

> Measurement date: 2026-11-25 · Phase 8/P1 update: 2026-11-26 · Phase 9: 2026-11-26 · Environment: Ubuntu 24.04, Python 3.12.3, 8 cores, 31 GB RAM, **no GPU**
> Reproduction commands: see the end of each section. Every figure below is an **actual run result**, not an estimate.
>
> - **Phase 2 (core MVP): PASS** — sections 1–8 below.
> - **Phase 3 (GUI + packaging): PASS on Linux** — section 9 below.
> - **Phase 4–5 (matcher/UI + color-bleed prevention): PASS** — sections 10–11.
> - **Phase 6 (real background reconstruction): PASS** — section 12.
> - **Phase 7 (container + icon protection): PASS on Linux** — section 13.
> - **Phase 8 (per-region + frame/container + UI fixes): PASS core** — section 14.
> - **Phase 8 P1 (ROI/OCR speed): PASS** — section 15.
> - **Phase 9 (evidence e1/e2 + per-region policy + I5 + P2 UI): PASS** — section 16; **101 tests**.
> - **Phase 9.1 (no anti-alias outline left on upscaled images, e3): PASS** — section 16.5.
> - **Phase 10 (per-region margin + e4 weak snap + lossless save): PASS** — section 17; **105 tests**.
> - **Phase 11 (UI polish: label padding, dim placeholder): PASS** — section 18; **108 tests**.
> - **Phase 12 (hide placeholder on focus, dropdown/spinbox arrows): PASS** — section 19; **112 tests**.
> - **Phase 13 (labels with (i) badge + hover/click tooltip): PASS** — section 20; **112 tests**.
> - **Phase 14 (keep English terms, info icon, labels next to controls): PASS** — section 21; **113 tests**.
> - **Phase 15 (grey icon, tooltip only on the icon, form to the left, only 3 hints): PASS** — section 22; **113 tests**.
> - **Phase 16 (QtAwesome icon library + responsive advanced options layout): PASS** — section 23; **114 tests**.
> - **Phase 17 (clearer buttons, Original/Result segmented control, action hints, badge + filter for needs-review regions): PASS** — section 24; **118 tests**.
> - **Phase 18 (quick zoom bar + splitter spacing): PASS** — section 25; **120 tests**.
> - **Phase 19 (icon next to label + no flicker when switching images): PASS** — section 26; **122 tests**.
> - **Phase 20 (preview renders quietly in the background, no full-UI flicker): PASS** — section 27; **124 tests**.
> - **Phase 21 (keep the Result button label while rendering): PASS** — section 28; **125 tests**.
> - **Phase 22 (disable the Result button when no image is selected): PASS** — section 29; **126 tests**.
> - **Phase 23 (rename the project to ImageCleaner): PASS** — section 30; **126 tests**.

## 1. Phase 2 gate conclusion

| Item | Result | Note |
|---|---|---|
| Pipeline analyze → render → write file | **Pass** | `analyze()` is separated from `render()` so the GUI can preview |
| Text detection by user keyword | **Pass** | 7/7 fixtures, no hardcoded keyword/coordinates/size |
| Distinguish standalone text vs text inside buttons/UI | **Pass** | 6/6 element cases; 1 case where the guard deliberately falls back to TEXT |
| Erase the right element without erasing other content | **Pass** | Visual check on the composite image + pixel-invariance test |
| Invariant “pixels outside the mask are unchanged” | **Pass** | `np.array_equal(...)` True on **7/7** fixtures |
| Batch + CSV/JSON report + continue on image error | **Pass** | 4 files (1 broken file), 5.0 s, batch does not stop |
| Never overwrite the original image | **Pass** | Automated test + output folder check |
| Unit/integration tests | **Pass** | `30 passed` (0.4 s) |
| Real user image | **Pass** | VPN badge + red note erased, clock/battery/wifi/“No SIM” intact |

**Phase 2 gate: PASS.** Cleared to move on to Phase 3 (GUI + packaging), with the limits listed in §6.

## 2. Per-fixture results

Fixtures generated with `scripts/gen_test_images.py` (synthetic, not real images).

| Fixture | Detected region | Expected | Actual | Engine | Invariant |
|---|---|---|---|---|---|
| `btn_filled.png` | `Upgrade now` → ELEMENT 0.97 (221×57); `VPN` → TEXT | Erase the whole blue button, keep “Cancel” | Correct, button disappears entirely | flat-fill | ✅ |
| `text_gradient.png` | `Confidential` → TEXT 1.00 | Erase the text on a gradient background, leave no ghost | Correct, gradient background seamless | gradient-fill | ✅ |
| `multi_occurrence.png` | 4× `Internal Use Only` → TEXT; `VPN` → ELEMENT 0.75 | Erase all 5, keep the 4 subtitle lines | Correct | flat-fill | ✅ |
| `dark_mode.png` | `VPN` → ELEMENT 0.83 (151×49); `Confidential` → TEXT | Erase the whole dark pill, keep “Confidential” | Correct, no bleeding into the dark background | flat-fill | ✅ |
| `panel_guard.png` | `VPN` → TEXT 1.00 | **Do not** erase the card containing other content | Correct (guard `_swallows_other_text`) | flat-fill | ✅ |
| `big_4k.png` (3840×2160) | `VPN` → ELEMENT 0.83 (601×101); 6× `Confidential` → TEXT | Erase the whole large button, keep the 6 labels | Correct, all 7 regions | flat-fill | ✅ |
| `no_match.png` | — | `NOT_FOUND`, write no file | Correct | — | ✅ |

Analyze time: 0.68–0.97 s for images ≤ 1 MP; **5.73 s** for a 4K image (because OCR splits into tiles).
Render: 0.00–0.12 s (flat/gradient fill); LaMa on its own ~**5 s/region** (see §6).

## 3. Real user image

`/home/chungntk/Downloads/image_cleaner/image_vpn.png` (VPN badge + arrow/red note):

- 1 ELEMENT region erased, engine `flat-fill`, image keeps its size.
- 3× zoom check: badge and red note gone; “No SIM”, wifi signal, time, battery **intact**.
- Pixels outside the mask unchanged.

## 4. Batch

Input: 3 fixtures + `broken.png` (junk file, not an image). Command:
`python -m src.cli --input batch_in --output batch_out --keywords "VPN, Upgrade now, Confidential"`

| File | Status | Erase regions | Error |
|---|---|---|---|
| `broken.png` | `failed` | 0 | `UnidentifiedImageError` (written to the report, batch continues) |
| `btn_filled.png` | `success` | 2 | — |
| `dark_mode.png` | `success` | 2 | — |
| `no_match.png` | `not_found` | 0 | — |

Total **5.03 s**; summary: `success=2, not_found=1, skipped_uncertain=0, failed=1, total=4`.
`report.csv` + `report.json` are written in the output folder.

## 5. Automated tests

```
$ .venv/bin/python -m pytest -q
30 passed in 0.39s
```

- `tests/test_matcher.py` (16): case-insensitive, whitespace, word boundary, fuzzy ≥5 characters, multi-line blocks, multiple occurrences, preserving the user's original spelling, keyword list normalization.
- `tests/test_mask_safety.py` (6): correct composite bits outside the mask, ELEMENT/TEXT mask within the allowed box, disabled/no regions ⇒ empty mask, binary mask.
- `tests/test_pipeline_e2e.py` (8): not-found writes no file, no overwrite, uncertain skipped, batch continues after a broken file, empty folder, PNG export.

## 6. Known limitations (honest)

1. **LaMa has not been visually validated on complex backgrounds.** The LaMa path produced the correct shape/range (outputs 0..255) but every current fixture falls into flat-fill/gradient-fill, so LaMa quality on real photos (textured backgrounds, skewed images) **has no evidence**. A real image set is needed for evaluation.
2. **LaMa is slow at ~5 s/region** (CPU, 512×512 crop). Images with many regions + LaMa enabled will be slow; the GUI must run in the background and show progress.
3. **Classification thresholds were learned from synthetic fixtures.** Element confidence (0.6/0.85) and evidence coefficients were tuned on generated images; varied real images may land in UNCERTAIN more often. Safety policy: UNCERTAIN ⇒ do not erase.
4. **No UI to review UNCERTAIN regions yet.** Currently only log/report; Phase 3 must let the user toggle and confirm before writing.
5. **Manual mask editing is design-only** (toggle + drag/resize box + padding). Not coded yet.
6. **Not packaged yet.** No `.spec`, no CI workflow, not tested on Windows/macOS. No claim of support for platforms not actually built.
7. `ProcessOptions.uncertain_action` exists but is unused (the policy is always “skip”).
8. Cannot recognize heavily stylized/rotated text > ~15°; OCR depends on RapidOCR.

## 7. Next work (Phase 3)

1. **PySide6 GUI** (`src/ui/`):
   - Keyword input + preset, file/folder selection.
   - Analyze in a worker thread (QThread), no UI freeze.
   - Region list: on/off, kind, confidence, keyword; preview with boxes.
   - Padding slider, engine selection; before/after preview.
   - UNCERTAIN regions shown clearly, **off by default**, the user decides.
   - Batch: progress + log + summary; save each image or save all.
2. **Packaging**: PyInstaller `--onedir`, bundle LaMa + RapidOCR models, `opencv-python-headless`.
3. **CI**: GitHub Actions builds separately for Linux (ubuntu-22.04), Windows, macOS (arm64/x86). Only claim platforms actually built + smoke-tested.
4. **Validation**: run the GUI on Linux, export images, check visually; add real images to evaluate LaMa.

## 8. Reproduction commands

```bash
cd /path/to/image-cleaner

# tests
.venv/bin/python -m pytest -q

# evaluate the 7 fixtures (script outside the repo, in /tmp)
.venv/bin/python /tmp/opencode/sc/eval.py

# batch
.venv/bin/python -m src.cli --input /tmp/opencode/sc/batch_in \
  --output /tmp/opencode/sc/batch_out --keywords "VPN, Upgrade now, Confidential"

# real image
.venv/bin/python -m src.cli --input /home/chungntk/Downloads/image_cleaner/image_vpn.png \
  --output /tmp/opencode/sc/real --keywords "VPN" --debug
```

---

# 9. Phase 3 – GUI & packaging

## 9.1 Phase 3 gate conclusion

| Item | Result | Evidence |
|---|---|---|
| PySide6 GUI runs | **Pass (Linux)** | Starts offscreen, no exception; minimum size 1294×700 |
| Keyword / preset input | **Pass** | 3 builtin presets + user presets (save/read JSON) |
| Add images / recursive folder | **Pass** | Smoke test loads 3 images |
| Background analysis with progress & Cancel | **Pass** | 3 images → `success` statuses, 7 regions, ~5.7 s (offscreen) |
| Toggle regions, UNCERTAIN off by default | **Pass** | GUI test: "Enable all" does not enable UNCERTAIN; manual tick → approved as Text/Element |
| Drag/resize box + padding | **Code done, not tested with a real mouse** | `regionEdited` signal changes the bbox + invalidates the mask (automated test) |
| Preview result | **Pass** | Render worker returns an image; 720 px preview pixmap, correct engine reported |
| Save each image / save all | **Pass** | Smoke: saves 2 images + `report.csv` / `report.json`, summary `success=2, not_found=1` |
| Linux onedir bundle | **Pass** | `pyinstaller --noconfirm --clean imagecleaner.spec` succeeds, 690 MB |
| Selftest inside the bundle (no Python needed) | **Pass** | OCR reads 5 lines correctly (`Account settings`, `Upgrade now`, `VPN`…), LaMa model found |
| Automated tests | **Pass** | `34 passed` (30 core + 4 GUI) |
| Windows / macOS | **Not verified** | CI workflow only; cannot build on this Linux machine |

## 9.2 Headless smoke test (real GUI, offscreen)

Script `/tmp/opencode/gui_smoke.py` drives `MainWindow` with timers:
add 3 fixtures → enter 4 keywords → `Analyze` → wait for the worker → `Preview result`
→ turn off preview → `Save all` → wait for the worker.

```
entries: btn_filled=analyzed(2), multi_occurrence=analyzed(5), no_match=no_match(0)
task_label: Analyzed 3 images · 7 regions
preview_btn: checked=True, pixmap=720px, mask overlay present, scale=0.85, fit=True
Result: Success 2 / Not found 1 / Skipped 0 / Errors 0 (0.2 s)
saved dir: btn_filled.png, multi_occurrence.png, report.csv, report.json
```

Comparing the GUI-saved images with the originals (numpy): size preserved, some pixels changed
(14,190 px and 15,239 px) — i.e. the images were really processed, not copied.

## 9.3 PyInstaller bundle

- Command: `pyinstaller --noconfirm --clean imagecleaner.spec`
- Result: `dist/ImageCleaner-app/` (two executables `ImageCleaner`, `imagecleaner-selftest` + `_internal/`).
- Size: **690 MB**. Mostly: LaMa 199 MB, PySide6 121 MB, OpenCV libs 93 MB, cv2 79 MB, onnxruntime 35 MB, RapidOCR 31 MB (including 3 OCR models ~32 MB).
- Run the selftest from another directory (`cd /tmp`) to prove it does not depend on cwd/source:

```
image: btn_filled.png (720x360)
OCR lines: 5
text: Account settings | Manage your subscription and billing. | Upgrade now | Cancel | VPN
model dir: .../dist/ImageCleaner-app/_internal/models
lama model: .../dist/ImageCleaner-app/_internal/models/lama/lama_fp32.onnx (yes)
SELFTEST OK
```

- The GUI in the bundle starts offscreen and runs for 12 s without error (deliberate timeout, not a crash).
- LaMa model bundled; RapidOCR models bundled. No runtime model download step.

## 9.4 Phase 3 limitations (honest)

1. **Windows/macOS not verified.** Workflow `.github/workflows/build.yml` already has 4 jobs
   (Linux ubuntu-22.04, Windows windows-2022, macOS-14 arm64, macOS-13 x86), but the Git repo
   has not been created/pushed, CI has not run. No claim that those builds work.
2. **No real mouse interaction testing** on the canvas (drag to move / resize corner / wheel zoom).
   The signal and bbox logic has automated tests; mouse operations need checking with a GUI on a real display.
3. **690 MB bundle** (no UPX compression). Can be reduced by dropping the cls model, excluding more PySide6 modules,
   or splitting LaMa into download-on-demand — but that loses “offline right after install”.
4. **Not signed/code-signed/notarized.** The macOS build will be blocked by Gatekeeper without signing; Windows may show a SmartScreen warning.
5. **LaMa still not visually evaluated on complex real images** (see §6.1).
6. The GUI runs `analyze()` over the whole list; a 4K image takes ~5.7 s/image. For large batches, use the CLI or wait.
7. `imagecleaner-selftest` only checks OCR + model paths, not inpainting in the bundle.

## 9.5 Phase 3 reproduction commands

```bash
cd /path/to/image-cleaner

# GUI from source (offscreen)
QT_QPA_PLATFORM=offscreen .venv/bin/python /tmp/opencode/gui_smoke.py

# tests (GUI runs offscreen automatically)
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q

# build
.venv/bin/pyinstaller --noconfirm --clean imagecleaner.spec

# selftest in the bundle, from outside the repo
cd /tmp
/path/to/dist/ImageCleaner-app/imagecleaner-selftest \
  /path/to/tests/fixtures/btn_filled.png
```

---

# 10. Phase 4 – exact keyword matching & compact UI

> Plan/tests/workflow: `docs/REFACTOR_PLAN.md`. Decisions D7–D9 were finalized with the user
> before coding. No commits between stages; every gate ran real commands.

## 10.1 Results

| Item | Result | Evidence |
|---|---|---|
| Reproduce the bug before coding | **Pass** | 18 tests red for the right reason, 39 pass (34 legacy + 5 new), fixture `substring_zza.png` + JSON ground truth |
| `abcxyzzza` + `zza` | **Pass** | 1 region, box=(251,51,94,37) vs ink ground truth (≤3 px); `abcxyz` and `KEEPTHIS` changed **0 px** |
| `VPN` vs `VPNX` | **Pass** | 0 matches for both exact and fuzzy (D7: short words are suffix-only) |
| Whole-word in a multi-word sentence | **Pass** | `Internal VPN enabled` + `VPN` → exactly 1 box (previously returned `[]`) |
| Fuzzy typo ≥5 characters | **Pass** | `Confidentlal` + `Confidential` still matches |
| Localization failure | **Pass** | Switched to UNCERTAIN, disabled, no file written |
| Single-screen UI | **Pass** | 8 visible controls (was 21), Advanced collapsed by default, min 835×653 |
| Non-modal validation | **Pass** | Button disabled + inline banner; smoke shows no modal spam |
| Automated tests | **Pass** | **58 passed** (34 old core + 17 matcher/pixel/real-OCR + 4 old GUI + 7 new UI) |
| 7 old fixtures | **Pass** | 7/7 `invariant=True`, classification unchanged |
| Real image `image_vpn.png` | **Pass** | 1 success; `No SIM`/`21:33`/`41%` bit-identical; alpha preserved |
| GUI smoke | **Pass** | analyze 3 images → 7 regions, preview, save 2 images + CSV/JSON report |
| Linux bundle | **Pass** | build exit 0, `SELFTEST OK` from `/tmp`, bundled GUI offscreen 12 s without error |

## 10.2 New / remaining limitations
- Localization uses column projection: fonts with touching glyphs, strong italics, CJK/Indic or a textured
  background may fail validation → UNCERTAIN (safe but the user should look again).
- Short keywords (<5) do **not** match prefix/infix (`VPNX` is deliberately rejected per D7).
- Real mouse on the canvas not tried yet; signal tests + headless smoke exist.
- Windows/macOS still not actually built (CI workflow only).

## 10.3 Reproduction commands
```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python /tmp/opencode/sc/eval_v2.py
.venv/bin/python -m src.cli --input tests/fixtures/substring_zza.png \
  --output /tmp/opencode/sc/sub_out --keywords "zza"
QT_QPA_PLATFORM=offscreen .venv/bin/python /tmp/opencode/gui_smoke.py
.venv/bin/pyinstaller --noconfirm --clean imagecleaner.spec
cd /tmp && <repo>/dist/ImageCleaner-app/imagecleaner-selftest \
  <repo>/tests/fixtures/btn_filled.png
```

---

# 11. Phase 5 – fixing color bleed during inpainting

## 11.1 Root cause confirmed experimentally
For a white-background image with a red border adjacent to the erase region and black text, the old 9 px ring region had
**a red border occupying 54.7%** → `_flat_fill` (threshold 65%) refuses → LaMa/Telea takes red pixels just outside the mask as source and pulls them inward:
```
before: auto → lama   mean BGR [239.7, 240.8, 252.8], min [4,5,203], 178 red px, std 50
        telea →       mean BGR [154.7, 154.7, 247.9], 1628 pink px, std 105
```
Also `_flat_fill` takes the **mean** of pixels “close to the chosen color” (±10 tolerance), so white
255 can become 252–253; `_plane_fill` uses least-squares and is often skewed by the red border.

## 11.2 Solution (no change to detection, no mask expansion)
New `src/core/background_restore.py`:
- `estimate_background()`:
  - background with a clear global color (white/grey): pick the cluster in the annulus **matching the global background color**
    and covering ≥3 mask edges → return model `flat`;
  - gradient background (scattered histogram): **RANSAC plane** (5 px threshold, 160 samples, fixed seed)
    + refit on inliers → return `plane`; slope≈0 is merged into `flat`.
- `apply_model()`: fill directly with the **exact background color** (median of inliers) or plane,
  without Telea/LaMa → no color-bleed source.
- `sanitize_guide()`: when the user disables flat-fill or the background is complex, only replace the pixels
  **near the mask** (8–14 px band) with the predicted background color before handing the image to the engine.
  Pixels outside the mask in the original are untouched; the composite stays bit-exact.
- `restore_masked()`: always uses the original image as base and only writes pixels inside the mask.

`inpainting_engine.py`: `AutoInpaint` and `GuardedInpaint` (for manually chosen telea/ns/lama)
share the same steps. No new dependency, still offline.

## 11.3 Measurements

| Case | Before | After |
|---|---|---|
| White + red border + black text, `auto` | lama, 178 red px, mean 239, std 50 | `flat-fill`, **255,255,255**, 0 red px, std 0 |
| `telea` / `ns` / `lama` chosen manually | 1059–1628 pink px | **pure white**, 0 red px |
| `flat_fill=False` (force the engine to run) | red spreads | sanitized guide → pure white |
| Grey background 128 | grey offset | exactly 128 |
| Gradient 30 / 100 levels | offset, red bleed | mean error 0.48 / 0.66 px, max 1 px |
| Fixture `bleed_white.png` via CLI | — | 1 success, 3855 px changed, **0 red px**, output only 2 colors |
| 7 old fixtures | 7/7 invariant | **7/7 invariant**, classification unchanged |
| Real image `image_vpn.png` | neighbours intact | still bit-identical, alpha preserved |
| Tests | 58 passed | **67 passed** (9 new color-bleed tests) |
| Linux bundle | SELFTEST OK (OCR) | SELFTEST OK + `inpaint: exact white via flat-fill` |

Render time increased slightly (0.01→0.25 s; 4K 0.12→0.72 s) because RANSAC only runs
when the background has no dominant color — this is the cost of an exact background color.

## 11.4 Reproduction commands
```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m src.selftest tests/fixtures/btn_filled.png
.venv/bin/python -m src.cli --input tests/fixtures/bleed_white.png \
  --output /tmp/opencode/sc/bleed_final --keywords VPN --debug
.venv/bin/pyinstaller --noconfirm --clean imagecleaner.spec
cd /tmp && <repo>/dist/ImageCleaner-app/imagecleaner-selftest \
  <repo>/tests/fixtures/btn_filled.png
```

---

# 12. Phase 6 – real background reconstruction (no color fill)

## 12.1 Cause of the previous issue
`AutoInpaint` had a shortcut: if `estimate_background()` returned model `flat`/`plane` it
**immediately returned** `apply_model()` — i.e. a flat/plane color fill, without calling the engine. With textured
backgrounds the model usually returned `none`, but the old `sanitize_guide()` **also bailed out** when there was no
model → the red border remained next to the mask and LaMa reconstructed it as red.

Measured on a textured background (waves + noise) against clean ground truth:
```
before: auto/lama MAE 69.54, max 206, output mean BGR [101.8,106.2,206.3] (red)
```

## 12.2 Correct fix
- **`src/core/background_restore.py`**:
  - `contaminated_band()` detects contaminated pixels **independently of the background model**:
    HSV saturation >55 or pixel darker than the local median by >25, in the band around the mask;
    dilated 3×3 to cover antialiasing.
  - `sanitize_guide()` uses `cv2.inpaint` on the contaminated band itself (with the target mask
    replaced by a neutral color) to obtain the nearby background — without touching the original outside the mask.
  - `restore_masked()` keeps only the engine output; snapping to the exact background color only when
    the background is perfectly flat, or the plane residual is ≤1 (mathematically exact gradient).
- **`inpainting_engine.py`**: remove the `flat-fill`/`gradient-fill` short-circuit entirely.
  Always run the real engine:
  - `flat`/`plane` background → Telea with sanitize (fast, exact, no color bleed);
  - `none` background (texture/photo) → **LaMa ONNX** reconstructs detail.
  `GuardedInpaint` applies the same guard for manually chosen `telea`/`ns`/`lama`.
- **`lama_inpainting.py`**: cache the ONNX session by path → no reloading 199MB every time.

## 12.3 Measurements

| Case | Before | After |
|---|---|---|
| Texture + red border + black text (vs clean image) | MAE 69.54, max 206, mean BGR [102,106,206] | **MAE 6.43, max 34**, std 7–8 vs clean 11, mean BGR [208,205,201] |
| White background + red border + text | 255 correct but via color fill | Engine Telea + snap → **255,255,255**, 0 red px |
| Gradient 30/100 levels | direct plane-fill | Telea + guard, mean error 1.9 / 3.5, max 6 / 10 |
| Real image `image_vpn.png` | neighbours intact | ROI `No SIM`/time/battery still bit-identical, badge clean |
| Fixture `bleed_white.png` CLI | — | 0 red px, output only 2 colors (white + text outside the region) |
| 7 old fixtures | invariant | **7/7 invariant=True** |
| 4K render | 20.8s (LaMa on every region) | **1.15s** (Telea when flat/gradient background) |
| Tests | 67 passed | **69 passed** (added texture + real screenshot) |
| Linux bundle | SELFTEST OK | build exit 0, `SELFTEST OK` |

## 12.4 Limits
- Very large/symmetric texture regions may still differ from the original detail by ~6–10 channel values;
  this is a limitation of offline CPU LaMa, not a color-fill bug.
- Users choosing `telea`/`ns` manually on a textured background will get smearing; it is recommended
  to leave engine `auto` (default) so LaMa is chosen automatically when texture is present.

---

# 13. Phase 7 – correct Container restoration & neighbouring object protection

## 13.1 Cause (reproduced)
- `background_restore.py` used a global prior: the outer color (black/white) was imposed on the annulus,
  discarding the grey card cluster (Δ128) / blue box (Δ225). Measurements before the fix:
  `card → (0,0,0)` instead of `(98,94,92)`; `blue → (255,255,255)` instead of `(235,99,37)`.
- `contaminated_band` flagged every saturated pixel → the blue container was treated as contaminated.
- Icon case: `ui_element_detector.py:239` merged contours into one rect; `mask_generator.py:84`
  filled the full rectangle → the icon inside the bbox was erased; `contaminated_band` also wiped the icon from the guide.

## 13.2 Fix
- **`estimate_background`**: drop the global prior, use a 1px anchor (next to the mask) + a growing annulus
  (3→5→8→10→14→20 px), choose the color with support ≥50% covering ≥3 edges. A saturated anchor is treated as an
  object (red badge on a white background) if a neutral color dominates the annulus. Fit the plane on the inlier set.
- **`sanitize_guide`**: drop the saturation rule; for flat/plane, contamination = deviation from the model >30;
  for texture only dark fringe, neutral uses the band median (not the whole-image median).
- **`mask_refinement.py` (new)**: runs after detection in `Pipeline.render`, ELEMENT only:
  - subtract OCR boxes that are not part of the match;
  - detect compact dark components as icons (no morph close to avoid merging with the button border),
    expand along components/radius around the icon center;
  - red annotations are kept in the mask (they are objects to erase) even when they cross the icon;
  - the icon part covered by the annotation is reconstructed by symmetry about the center, applied after compositing.
- `ProcessOptions.refine_mask=True`; the engine receives `protected=` so it does not wipe icons from the guide.
- Detection **unchanged** (only post-detection masking).

## 13.3 Results (vs the ground truth image)
| Case | Before | After |
|---|---|---|
| Button inside a grey card / black outer | restored `(0,0,0)` | **`(98,94,92)` matching the card**, outer bit-identical |
| Text on a blue box / white outer | restored `(255,255,255)` | **`(235,99,37)` matching the box** |
| Button + red line over the icon | icon lost ~99%, red remains | icon pristine **bit-identical**, red **0 px**, button erased cleanly |
| 7 old fixtures | 7/7 invariant | **7/7 invariant=True** |
| Real image `image_vpn.png` | neighbours intact | `No SIM`/time/battery bit-identical, alpha preserved |
| Tests | 69 | **77 passed** (8 regression tests added) |
| Linux bundle | — | build exit 0, `SELFTEST OK` |

Three-column comparison images (original / after detection / after restoration):
`/tmp/opencode/sc/restore_cases_v2/compare_*.png`.
Note: on synthetic fixtures, real detection chooses ELEMENT covering the whole blue box (case 2) or only TEXT (case 3);
to validate the intended user scenario, the comparison images use the region (bbox) described in the request —
this is the restoration layer, not a detection change.

## 13.4 Limits
- Icon pixels entirely under an opaque button are not present in the original image; they are only approximated by symmetry.
- The shared ONNX session cache wrapper does not reload the model when engines are created multiple times.

---

# 14. Phase 8 – container, frame and object protection optimization

> Plan/test matrix: `docs/OPTIMIZATION_PLAN.md`. The results below are real runs.

## 14.1 Scope of work

- **Per-region rendering**: `Pipeline.render()` no longer merges every mask into one union
  and calls the engine once. Each region has its own mask + background model and its own composite.
- **Frame guard**: `_swallows_other_text()` now only rejects when the foreign box lies **mostly inside**
  the candidate; an icon OCR'd as text but outside the frame no longer causes the button to be lost.
- **Container policy**: large colored panel, square corners, no border → defaults to `TEXT`
  (`_looks_like_container`). Rounded/bordered/less saturated buttons stay `ELEMENT`.
- **Object protection**:
  - dropped the “icon must be to the left of the text” condition;
  - added `_solid_compact_objects()` to detect light/colored icons at any position;
  - filter by size relative to text height, fill ratio, aspect, target intersection;
  - red annotation is kept only when it **intersects the target mask**, and does not re-add colored icons;
  - unrelated red badges are preserved.
- **Partial text mask**: localized substrings use glyph pixels within the char-box union,
  not the full rectangle; gaps between glyphs are preserved.
- **Preview = Saved**:
  - `_snapshot()` keeps `lines` so refinement behaves like the saved version;
  - overlay uses `refine_element_mask()` instead of the raw mask.
- **UI fixes**: Delete/context menu removes images; failed entries update the list; no modal on save.

## 14.2 Measurements

| Item | Result |
|---|---|
| Full test run | **92 passed** (77 old + 15 new: 8 Stage R + 7 T-lite/UI extensions) |
| `multi_container` shared render | grey card `(98,94,92)`, blue box `(235,99,37)`, both correct |
| Preview vs saved | identical masks, `KEEP` bit-identical |
| Case 3 real detection | `ELEMENT`, icon pristine bit-identical, red **0 px** |
| Case 2 real detection | defaults to `TEXT`, text ROI `(235,99,37)` |
| Icon variants | left/right/above/light-on-dark/colored all protected |
| Unrelated red badge | not added to the mask |
| Partial text mask | density 0.60 (was 1.00), glyph gaps not erased |
| 7 old fixtures | 7/7 `invariant=True` |
| Real image `image_vpn.png` | 1 success; `No SIM`/time/battery bit-identical; alpha preserved |
| 4K `big_4k` (before P1) | analyze 5.54 s; render **2.44 s** for 7 regions; after P1 see §15 |

## 14.3 Limits / next work

1. **Per-region speed (addressed in §15)**: before P1, 4K render grew from 0.61 s → 2.44 s because
   each region ran many full-image operations. P1 added ROI cropping + fast `mask_bbox` +
   single-view OCR; preview cache/downscale remains for P2.
2. **Symmetry (addressed in §16)**: I5 uses PCA + mirror residual; fully occluded pixels
   are still approximate only.
3. **P2 UI (partly done in §16)**: split/wipe (right mouse), drag-drop,
   per-region policy exist. Thumbnail/status badge and clipboard remain.
4. **Container heuristic**: large colored/square panel → TEXT; square colored buttons may also be
   classified TEXT. The per-region UI will be where the user changes it back.
5. **Windows/macOS** still not actually built.

## 14.4 Reproduction commands

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python scripts/gen_test_images.py
.venv/bin/python /tmp/opencode/sc/eval_v9.py
.venv/bin/python -m src.cli --input tests/fixtures/multi_container.png \
  --output /tmp/opencode/sc/p8 --keywords VPN --debug
```

---

# 15. Phase 8 P1 – speed optimization

## 15.1 Changes

- **Engine runs on the ROI only**: `Pipeline.render()` crops the image around the mask
  (`pad = max(32, 0.25 * max(w, h))`) then calls `estimate_background`,
  `sanitize_guide`, inpainting. No more full 4K scan per region.
- **`_red_annotation()` processes only a ±3×text_height crop** instead of whole-image HSV.
  Still gathers all red components connected to the target; unrelated red badges are still rejected.
- **`mask_bbox()` uses `cv2.findNonZero` + `boundingRect`** instead of `np.where`
  (25 ms → 0.6 ms on a 4K mask).
- **4K single-view OCR**: `ocr_max_side` defaults to **4096**, engine caps at 2560.
  Images ≤4096 are no longer split into 2 tiles; larger images tile at 4096.
  CLI `--ocr-max-side` defaults to 4096.
- Matching, mask safety and offline behavior unchanged.

## 15.2 Measurements

| Item | Before P1 | After P1 |
|---|---:|---:|
| `big_4k` analyze (7 regions) | 7.4–8.3 s | **~3.55 s** |
| `big_4k` render (7 regions) | 2.44 s | **~0.30–0.45 s** |
| Tests | 92 | **92 passed** |
| 7 old fixtures | 7/7 invariant | **7/7 invariant=True** |
| Case 1 grey card | correct | correct `(98,94,92)` |
| Case 2 blue box | correct | correct `(235,99,37)` |
| Case 3 icon + red | correct | icon pristine bit-identical, red **0 px** |
| Real image `image_vpn.png` | neighbours intact | still bit-identical, alpha preserved |

## 15.3 Remaining

- P2 remaining: thumbnail/status badge, clipboard; preview cache/downscale.
- I5 now has PCA/mirror residual in §16 (best-effort).
- The second OCR pass for small images can still cost ~0.7 s when the keyword is actually absent.
- The Linux bundle was rebuilt in Phase 9 (`SELFTEST OK`); Windows/macOS not actually built.
- Windows/macOS not actually built.


---

# 16. Phase 9 – acceptance of evidence/e1–e2, per-region policy, I5 and P2

## 16.1 Input and expectations

- `evidence/e1.png`: (a) erase both the red border and all content inside;
  (b) erase only the `VPN` glyphs, keeping the red border and `Add` / `Configuration...`.
- `evidence/e2.png`: standalone `VPN` (heading + row label) erased completely;
  the blue `VPN` button has 2 modes: (a) erase only the white text, keep the blue box;
  (b) erase the whole blue button.
- Criteria: pixels around the target **bit-identical**; background after erasing **matches the color**
  of the background before erasing; no blur/bleed.
- Ground truth: `evidence/e1.json`, `evidence/e2.json`.
- Tests: `tests/test_evidence_acceptance.py` (5 tests, including the e2 auto policy).

## 16.2 Technical changes

- **`Region.mask_mode`** (`None | "text" | "element"`): per-region policy override.
  `region_is_element()` is used consistently in mask, refine and GUI overlay.
  The CLI still uses `--element-policy auto|text_only` for the whole image.
- **Flat background around glyphs**: `_flat_word_fill()` takes the mode of the 1px ring, drops the red
  annotation pixels, and only accepts when the cluster is large enough and spread ≤ 8. Gradient/texture
  does not go this way (the real engine handles it).
- **Exact glyph mask**: `_glyph_mask_from_fill()` estimates the ink color from the word box,
  keeps only pixels closer to the ink than to the background, and only keeps components touching the word.
  This prevents the red border or the card band outside a blue icon from being pulled into the mask.
- **Exact fill**: `region_text_fill()` returns the flat background color; the pipeline overwrites exactly
  the masked pixels after the engine → the background matches the color exactly, independent of Telea/LaMa.
- **Context-aware red annotation**: a red component is treated as the target's annotation only
  when its span is not larger than ~1.5× the target. The red frame enclosing a whole row (e2) no longer
  turns the `VPN` label into `ELEMENT` and is not cut when erasing.
- **Icon protection**: components on the match line itself (`own_text`) are not treated as icons;
  background components that are too large are rejected. So e1 can erase both `Add`/`Configuration...`
  without “wrongly protecting” glyphs.
- **I5 symmetry**: PCA axes + mirror residual + hidden-half reconstruct score;
  mirror only when `score ≥ 0.75` and reconstruct ≥ 0.40. Asymmetric icons are not fabricated.
- **P2 UI**: **Erase mode** column (Auto / Text only / Whole block) per region; wipe comparison
  by **right-mouse drag** in Result mode (no extra visible control);
  drag-and-drop images/folders into the window.

## 16.3 Acceptance results (real runs)

| Item | Result |
|---|---|
| `e1` auto (ELEMENT) | red **2050 → 0 px**; card-background text region `(25,25,23)` error **0.0** |
| `e1` text-only | red **2050 → 2050 px**; `Add`, `Configuration...`, status, Back **bit-identical**; `VPN` region card background (mean error 0.048, p95 0) |
| `e2` auto | blue button **503 → 0 blue px**; row background error **0.0**; red frame + `Not Connected` bit-identical |
| `e2` text-only | blue button still **541 blue px**; background inside the icon mean error **0.01**, max 3; red frame intact |
| Full test run | **105 passed** |
| 7 old fixtures | 7/7 `invariant=True` |
| 3 user cases | grey card `(98,94,92)`, blue box `(235,99,37)`, icon pristine + red 0 |
| Linux bundle | rebuilt in Phase 9, `SELFTEST OK` |

Acceptance images saved under `evidence/results/auto/` and `evidence/results/text_only/`
(each folder has `e1.png`, `e2.png`).

## 16.4 Remaining

- Thumbnail/status badge in the image list and pasting images from the clipboard (P2).
- Preview still renders at full resolution on every click of “Result”; cache/downscale not done.
- The second OCR pass for small images still ~0.7 s when the keyword is absent.
- I5 is best-effort on symmetric icons; fully occluded pixels remain approximate only.
- Windows/macOS not actually built on this machine.


---

# 16.5 Phase 9.1 – fix leftover text outline after erasing (`evidence/e3.png`)

## Cause

`e3.png` is an already-processed image (or a heavily anti-aliased upscaled image): after erasing,
a **grey/dark outline** remains around the old text. There are three independent causes:

1. **The mask only covers the ink core**: anti-alias pixels at the edge that are closer to the background color than
   the text color are dropped; if the image was upscaled, the anti-alias halo is wider than `pad=2`, so some remains.
2. **The OCR box is narrower than the ink** on high-DPI images: the glyph crop is only `word+4px` wide,
   and does not include the full halo on the left/right.
3. **Lines duplicated from the color OCR pass** (IoU 0.34–0.45) were not deduped (old threshold 0.5),
   creating 2 overlapping regions; the secondary region had no flat fill, so the engine filled the wrong color
   into the middle of the blue icon.

## Fix

- **Halo growth**: after the ink mask, grow by at most `grow = clamp(0.12*text_h, 2, 6)`
  into adjacent pixels whose color differs from the background (`d_fill > 3`); exclude all red/red-border pixels
  (including dark anti-alias) so the annotation border is not eaten.
- **Crop by text size**: `dx = max(pad+2, 4, 0.28*text_h)`,
  `dy = max(2, 0.08*text_h)` — wide enough for the anti-alias halo on 2x/3x images.
- **Dedupe duplicate lines**: lower the IoU threshold from 0.50 → 0.30 (same text) so the color OCR pass
  does not create duplicate regions.
- Flat fill still applies to the whole mask (including the halo), so the background matches the color exactly.

## Measurements (e3 reproduced by upscaling e2 2x with Lanczos/Bicubic/Nearest)

| Variant | Title `VPN` | Row label `VPN` | Red frame / `Not Connected` |
|---|---:|---:|---|
| Lanczos 2x | max error **5** | max error **3** | bit-identical |
| Bicubic 2x | max error **0** | max error **4** | bit-identical |
| Nearest 2x | max error **0** | max error **7** | bit-identical |

Before the fix: the row label still had **156–245** pixels (white/grey outline); the title still had a dark halo.
After the fix: no pixel deviates from the background by > 7; the outline is gone.

New test: `test_scaled_antialiased_text_leaves_no_outline` in
`tests/test_evidence_acceptance.py`. Total at that time: **101 passed**; after Phase 10: **105 passed**.

Note: `e3.png` is only an old output image whose original text is gone; the text cannot be reconstructed from it.
The fix was accepted by re-running on the original layout upscaled 2x (same symptom).


---

# 17. Phase 10 – mask scope adjustment, e4 weak snap, lossless save

## 17.1 Requirements

- Allow **expanding/shrinking** the mask scope of the selected region (not just whole-image padding).
- `evidence/e4.png`, keyword `VPN`: there is a `snap` candidate with confidence **0.631**; it needs to be verified
  and the `VPN` in “Proton VPN” must not be missed.

## 17.2 Changes

- **`Region.margin_px`**: number of pixels to expand (+) or shrink (-) per region.
  - ELEMENT: expand/shrink the bounding box directly before creating the mask.
  - TEXT: added to the padding when dilating; if the total is negative, erode the glyph mask.
  - GUI: **Padding** column in the region table (`-12..+12 px`, default 0). Changes update
    the overlay/preview immediately; detection is untouched.
- **Weak snap → TEXT**: a `snap` candidate with `confidence < element_hi` (0.75) is no longer
  pushed to `UNCERTAIN` (which is skipped by default). It is handled as **TEXT**, still erasing the keyword;
  the user can manually choose “Whole block” if desired.
- **Lossless save by file extension**: `write_result()` picks the format from the extension
  (`.png → PNG`, `.jpg → JPEG`…) instead of `loaded.fmt`. Fixes the case where `e4.png` is actually
  WebP bytes: previously it saved a `.png` file but the WebP lossy content changed pixels across
  the whole image; now PNG lossless, non-erased regions bit-identical.

## 17.3 e4 results

| Item | Result |
|---|---|
| Region “Proton VPN” (previously `snap 0.631`) | **TEXT, enabled**, evidence `weak_snap=1.0` |
| 5 occurrences of `VPN` | all erased; no `UNCERTAIN` left |
| Background of the “Proton VPN” region after erasing | mean deviation 1.02, max 7 (bright highlight) |
| `Proton`, `Speak`, `Otherwise`, `End If`, chip `Connected`, `Search Actions` | **bit-identical** |
| Saved file | **real PNG**, lossless; regions remain bit-identical |
| Full test run | **105 passed** |

Result image: `evidence/results/e4.png`.

## 17.4 Remaining

- Margin is currently ±12 px in the GUI; the range can be increased if larger values are needed.
- No whole-image margin input besides `--padding` (CLI).
- Windows/macOS still not actually built.


---

# 18. Phase 11 – UI polish

## 18.1 Changes

- **Labels no longer touch the edge**: `QLabel` has `background: transparent` and left/right padding
  so the dark background block of the `QWidget` does not hug the text; the input block grid
  and Advanced options have `contentsMargins(12, 8, 12, 8)`; the image list panel has
  margin `(8, 4, 8, 4)`; the status bar has 8 px left padding.
- **Dim placeholder, easy to distinguish**: added `PLACEHOLDER = #6f7789` and
  `dim_placeholder(widget)` / `apply_theme(app)` in `src/ui/theme.py`, assigned to
  `QPalette.PlaceholderText` for the keyword and output folder fields. Typed text stays bright
  `#e7e9ee`, so the placeholder no longer blends with real content.
- The keyword box is 52 px tall so both lines are visible when there are many keywords.
- No change to image processing or detection logic.

## 18.2 Verification

- Offscreen capture with the real theme: labels offset from the left edge, dim grey placeholder, typed text bright and clear.
- Added 3 GUI tests: labels, placeholder dimmer than real text, label/panel padding.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **108 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 19. Phase 12 – placeholder on focus + dropdown/spinbox arrows

## 19.1 Changes

- **Hide placeholder while typing**: `HintPlainTextEdit` (keyword field) hides the placeholder
  as soon as the field gets focus; it only reappears when the field is empty **and** loses focus. So the hint
  no longer competes for space with the text being typed.
- **Dropdown arrow**: the old QSS only styled `QComboBox::drop-down` without
  `down-arrow`, so Qt drew an empty area. Added `ArrowComboBox`, which draws a 2 px chevron at the
  right corner (brighter on hover), used for Keyword preset, Engine, Erase region,
  Matching and the Erase mode combo in the region table.
- **Increment/decrement arrow**: `ArrowSpinBox` draws up/down chevrons itself; the QSS reserves 22 px
  on the right (`padding-right: 28px`) and makes `up-button`/`down-button` transparent.
  Applied to whole-image Padding and the per-region **Padding** column (padding width 84 px).
- Region table row height defaults to 34 px so the spinbox/chevron is not clipped.
- **Labels with (i) badge**: `HintLabel` adds a small badge next to the label; hover/click shows a
  tooltip with an explanation (output folder, keyword preset, engine, erase region, matching, padding, keyword).
- No external image/icon files; all arrows are drawn with `QPainter`.

## 19.2 Verification

- Offscreen capture: all 4 dropdowns and 2 spinboxes show arrows; combos/spins inside the
  region table also display.
- Tests: focus the keyword field → `placeholderText()` empty; lose focus while empty → the hint
  returns; with text → the hint stays hidden. A pixel test verifies the chevron is actually drawn.
- Added tests for `HintLabel` (badge present, hover/click shows tooltip).
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **112 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 20. Phase 13 – (i) badge and tooltips for labels

> Phase 14 replaces the `(i)` text with an info icon and aligns labels next to controls — see section 21.

## 20.1 Changes

- Added `HintLabel` in `src/ui/controls.py`: a label with a soft-colored **(i)** badge.
  - Hover the badge → tooltip shows immediately.
  - Click the badge → tooltip shows at the cursor.
  - The label text itself also has a native tooltip on hover.
- Attached badges to labels that have explanations:
  **Keyword**, **Output folder**, **Keyword preset**, **Engine**, **Erase region**,
  **Matching**, **Padding**.
- QSS adds `QLabel#hint_badge` (color `MUTED`, 12 px); no external icons/assets.
- No change to image processing logic.

## 20.2 Verification

- Offscreen: (i) badge shown next to each label; tooltip appears on hover/click.
- Added 2 GUI tests: badge/tooltip exist on the right labels; both hover and click call
  `QToolTip.showText` with the hint content.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **112 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 21. Phase 14 – technical terms, info icon, label spacing

## 21.1 Changes

- **Keep technical terms in English** instead of translating:
  - `Offline`, `Padding`, `Engine`, `Preset`, `Element`, `Text`, `Mask`.
  - Region kind: **Element / Text** (keep “Needs review” for the pending-review state).
  - Policy: “Auto (text/element)” / “Text only”; per region: “Text only” / “Element whole”.
  - Preset dialogs and the status bar reuse these terms.
- **Info icon instead of the `(i)` text**: `HintLabel` uses the standard Qt icon
  `SP_MessageBoxInformation` (16×16), still hover/click to show the tooltip; the label text has a
  native tooltip.
- **Label next to the control**: removed `addStretch` inside `HintLabel`, set
  `sizePolicy = Maximum`; form labels are right-aligned so the badge/icon sits next to the
  dropdown/input (~8 px by grid spacing) instead of being pushed far away.
- No change to image detection/erasing logic.

## 21.2 Verification

- Offscreen: info icon shown next to labels; labels “Engine”, “Preset”, “Padding”,
  “Offline”, “Show mask” use the correct terms; label–control spacing is tight.
- GUI tests updated: English terms, pixmap icon instead of text, right-aligned labels and
  `sizePolicy=Maximum`, hover/click tooltips.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **113 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 22. Phase 15 – grey icon, tooltip only on the icon, form to the left

## 22.1 Changes

- **Single grey info icon**: dropped the standard Qt icon (blue), draws a circle + dot
  + bar with `QPainter` in `#9a9a9a`, 16×16; no external assets.
- **Tooltip only on the icon**: removed `setToolTip` from the text part of `HintLabel`; only hovering/clicking the
  icon shows the tooltip. Hovering the label text no longer triggers a tooltip.
- **Only 3 labels have hints**: **Keyword**, **Engine**, **Padding**. The Output folder,
  Keyword preset, Erase region, Matching labels go back to plain `QLabel`, no icon,
  no tooltip. The tooltips of the policy combo and the per-region “Padding” spinbox are also removed.
- **Form to the left**: added stretch to the empty right column (`setColumnStretch(4, 1)`),
  the “Choose…” button and checkbox are left-aligned in their cells; labels remain right-aligned in the narrow column so
  controls start at ~x=134 instead of ~x=257 as before, no longer pushed to the middle of the screen.
- No change to image processing logic.

## 22.2 Verification

- Offscreen: grey icon next to exactly 3 labels; labels/controls near the left edge; “Log” stays on the right.
- GUI tests: exactly 3 `HintLabel`s; icon pixmap is monochrome grey; label text has no tooltip;
  other labels have no tooltip; column stretch = 1 and `engine_combo.x < 220`.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **113 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 23. Phase 16 – QtAwesome icon library + responsive advanced layout

## 23.1 Icon library

- Added dependency **QtAwesome 1.4.2** (`gui` extra) — a set of Font Awesome/Material/
  Phosphor icons packaged in the wheel, rendered offline, no network calls.
- `src/ui/icons.py`: maps semantic names (`info`, `chevron_down`, `folder`, `save`,
  `analyze`, `cancel`, `settings`, `log`, `original`, `result`, `remove`) to
  QtAwesome glyphs, with a `QPixmap` cache for `paintEvent`.
- Replaced all hand-drawn shapes:
  - info badge → `fa5s.info-circle` in grey `#9a9a9a`.
  - combo arrow → `fa5s.chevron-down` (brighter on hover).
  - spinbox arrow → `fa5s.chevron-up/down`.
- Attached icons to buttons: Choose image, Folder, Delete, Advanced, Original, Result, Analyze,
  Save image, Cancel, Choose…, Save preset…, Log.
- `imagecleaner.spec`: `collect_data_files("qtawesome")` + hidden imports
  `qtawesome`, `qtpy` to bundle the fonts.

## 23.2 Advanced options layout

- Dropped the fixed grid, switched to stretchable `QHBoxLayout` rows:
  - Row 0: **Output folder** + single-line path taking the full width + Choose button.
  - Row 1: **Keyword preset** (left) — **Engine** (right).
  - Row 2: **Erase region** (left) — **Matching** (right).
  - Row 3: **Padding** + Show erase region (left) — Log (right).
- Labels min width 96 px, right-aligned; controls stretch (`stretch=1`, combo max 420 px).
- Vertical spacing 10 px, 24 px between the two groups; windows from 1000–1400 px still do not clip text.
- Path length 785 px at 1000 px and 1129 px at 1400 px (previously ~430 px).

## 23.3 Verification

- Offscreen captures at 1000/1200/1400 px: long path renders correctly, right column flush to the edge, no overflow.
- GUI tests: output path > 800 px, left column < 220 px, both right controls end at the same edge,
  labels right-aligned, icons from `qtawesome` are not null.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **114 passed**.
- Bundle rebuild: QtAwesome fonts present in `_internal/qtawesome/fonts`; `SELFTEST OK`.

---

# 24. Phase 17 – clarifying actions and needs-review regions

## 24.1 Input folder button

- `Folder…` → **`Image folder…`**, with tooltip “Choose the folder containing input images
  (original images are kept intact)” so it is not confused with the output folder.

## 24.2 Segmented Original / Result

- The two buttons use objectName `segment_left` / `segment_right`, adjacent (spacing 0),
  segmented QSS: `ACCENT` background, dark text, dark icon for the **checked** state.
- After analysis finishes → automatically switch to **Result** (wait for the worker to finish so it is not
  blocked by the busy state).
- Selecting an analyzed image in the list → opens **Result** by default; the user clicks
  **Original** to see the original.
- Preview render has a guard `(path, result_btn.isChecked())`: an old result does not overwrite
  a new image if the user switches image/mode while rendering.

## 24.3 Hints when the Analyze button is disabled

- `task_label` shows an orange hint:
  - “Choose an image and enter keywords, then click Analyze to start.”
  - “Choose an image to start.” / “Enter keywords, then click Analyze.”
- When conditions are met, the label returns to “Ready” in grey.

## 24.4 Needs-review badge + filter

- Removed the caption line; `UNCERTAIN` regions get a yellow badge **“Needs review”** right in the **Kind**
  column (tooltip explaining to tick the Erase checkbox to approve).
- The Kind column has a fixed width of 122 px; the item below is left empty so the badge does not overlap text.
- Added a **“Show needs-review regions only”** checkbox in the table header; when on, it hides all regions
  that do not need review, keeping the badge. Untick to see all.
- Table title shows `N regions · M enabled · X needs review`.
- Approving a region (ticking Erase) → the badge is replaced by the normal `Text`/`Element` label.

## 24.5 Verification

- New GUI tests: folder button name, hints when conditions are missing, segmented defaults to Result
  and switches back to Original, badge + filter + region approval.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **118 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 25. Phase 18 – quick zoom bar + splitter spacing

## 25.1 Zoom bar

- Added a control cluster next to the Original/Result segmented buttons:
  - **Fit** (`fa5s.expand`) → `preview.fit_to_view()`.
  - **−** → zoom × 1/1.25.
  - Current percentage label (`zoom_label`).
  - **+** → zoom × 1.25.
  - **100%** → `preview.zoom_to(1.0)`.
- Icons from QtAwesome; `−/+` buttons 32 px square; tooltip on each button.
- Zoom buttons are disabled when there is no image, enabled when `PreviewCanvas` has a pixmap.
- `zoomChanged` still updates the percentage label as before.

## 25.2 Splitter spacing

- Wrapped the work panel in a widget with margin `(6, 2, 6, 2)` → the “Preview” and
  “Detected regions” groups no longer touch the vertical/horizontal splitter bar.
- Wrapped `preview_box` (6 px bottom margin) and `regions_box` (6 px top margin) separately
  before adding them to the vertical splitter → the two groups are separated and do not touch the handle.
- The image list panel keeps an 8 px margin; total distance to the work panel ~14 px.

## 25.3 Verification

- New GUI tests: zoom buttons toggle with image, +/− change the scale, 100% is exactly 1.0,
  Fit re-enables fit mode; work panel and both wrappers have margins ≥ 6 px.
- Updated control budget test: the zoom/segmented/filter groups are secondary controls,
  the main workflow still ≤ 12 items.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **120 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 26. Phase 19 – icon next to label, no flicker when switching images

## 26.1 Info icon next to the label

- `HintLabel`: the text part uses `QSizePolicy.Maximum` and a stretch is added **after** the badge,
  so leftover space no longer sits between the text and the icon.
- Text–icon spacing fixed at 4 px; measured on “Padding”, “Engine”,
  “Keyword” all 0–6 px.

## 26.2 No flicker when switching images

- `_show_entry()` no longer resets to “Original” before loading a new image. The old order caused
  the sequence *result A → original A → original B → result B*; now only *image B → result B*.
- `_auto_show_result()` returns `bool` and if the **Result** button is already on it calls render
  directly without toggling the button off/on (the cause of the state flicker).
- Images with no enabled regions or not analyzed still fall back to “Original” as usual.
- Preview render still has a path/mode guard so an old result does not overwrite a new image.

## 26.3 Verification

- GUI tests: icon–label spacing ≤ 6 px; switching between analyzed images does not emit
  `toggled(False)` on the Result button, but still calls render exactly once.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **122 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 27. Phase 20 – quiet background preview rendering

## 27.1 Cause of full-UI flicker

- Every time an image is selected, the result preview was built through the old `_start_worker(..., quiet)`,
  i.e. it shared the global busy state:
  - disable Analyze / Save / add image / Result button,
  - show Cancel button + progress,
  - change the task label and the wait cursor.
- Rendering takes only ~0.3 s, so these changes toggled continuously → the whole UI flickered,
  and the Analyze button looked as if it had just been pressed.

## 27.2 Fix

- Added a separate worker channel `_preview_worker`:
  - `_start_worker(..., quiet=True)` redirects to `_start_preview_worker()`.
  - Does not call `_set_busy()`, does not disable buttons, does not show progress/cancel, does not
    change the global task label; only changes the Result button text to “Rendering…”.
  - Preview errors only log + banner, no dialog.
- Analysis/save workers (not quiet) request the running preview to stop; closing the app
  also waits for the preview to stop.
- Render jobs return `None` when cancelled; `_on_preview_rendered` ignores stale payloads
  (guard by path + Result button state).
- The **Result** button keeps its state when switching images, no toggling
  Original → Result.

## 27.3 Verification

- Real script with the pipeline: select an analyzed image → `_busy=False`, Analyze button
  still enabled, Cancel button hidden, progress unchanged; the result image is set after rendering.
- GUI tests: the quiet worker does not call `_set_busy`; switching images uses `quiet=True`.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **124 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 28. Phase 21 – keep the Result button label

## 28.1 Changes

- The **Result** button no longer changes its text to “Rendering…” while the preview is rendering.
- Also removed resetting it back to “Result” after rendering finishes (the label does not change, so it is unnecessary).
- Preview error handling uses `result_btn.isChecked()` instead of comparing text;
  analysis/save errors no longer depend on the button label.
- All other behavior unchanged: quiet background rendering, path guard, keeping the Result state
  when switching images.

## 28.2 Verification

- GUI tests: when preview rendering starts, `result_btn.text()` is still “Result”
  and does not contain “Rendering”.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **125 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 29. Phase 22 – disable Result when no image is selected

## 29.1 Changes

- Added `_update_view_buttons()`: the **Result** button is enabled only when
  - there is a selected entry,
  - the image is loaded (`_loaded is not None`),
  - not in the busy state.
- Call this function when:
  - the window is initialized,
  - `_show_entry()` (with an image or `None`),
  - a busy task finishes.
- The **Original** button is always enabled to view the image/placeholder; when the image selection is cleared, the app returns to Original.
- During analysis/saving, the Result button stays disabled as before.

## 29.2 Verification

- GUI tests: initially Result is disabled; select an image → enabled; clear the selection (`_show_entry(None)`)
  → disabled again and Original is selected.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **126 passed**.
- Bundle rebuild + `SELFTEST OK`.

---

# 30. Phase 23 – ImageCleaner project name

## 30.1 Rationale

- The old name was easily mistaken for screen cleaning, while the actual function is erasing
  text/UI elements from **screenshots** via inpainting.
- Chose **ImageCleaner**: matches the subject being processed (images), short, memorable; package
  `imagecleaner`, display name `ImageCleaner`.

## 30.2 Scope

- Metadata: `pyproject.toml` (`name = "imagecleaner"`, new description),
  entry points `imagecleaner`, `imagecleaner-cli`, `imagecleaner-selftest`.
- Build: `imagecleaner.spec`, exe `ImageCleaner`, selftest `imagecleaner-selftest`,
  bundle `dist/ImageCleaner-app`; CI artifact `ImageCleaner-*`.
- GUI: window title, `QApplication` application/display/organization name.
- Config/log: `APP_NAME = "ImageCleaner"`, logger namespace `imagecleaner.*`,
  log file `imagecleaner.log`.
- Environment variables: `IC_CONFIG_DIR`, `IC_MODEL_DIR`.
- No legacy aliases or migration code with the old name.

## 30.3 Verification

- Directly verified `IC_CONFIG_DIR`, default config folder `ImageCleaner`,
  `IC_MODEL_DIR`; no separate test added since it is outside the project spec.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **126 passed**.
- Editable reinstall creates exactly 3 `imagecleaner*` commands; `imagecleaner-cli --help` OK.
- Bundle rebuilt per the new spec; `dist/ImageCleaner-app/imagecleaner-selftest` → **SELFTEST OK**.

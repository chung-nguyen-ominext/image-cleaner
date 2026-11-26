# Phase 4 refactor plan – exact keyword detection & simple UI

> Status: **Phase 4 historical document** (D7–D9 still in effect).
> Phase 8 (2026-11-26) adds per-region render, frame/container, protected icon and speed P1;
> see sections 14–15 of `docs/STATUS.md` and `docs/OPTIMIZATION_PLAN.md`.
> Date: 2026-11-25 · Measurement environment: Ubuntu 24.04, Python 3.12, RapidOCR 3.10 CPU.
> 4 independent sub-agent investigations; every figure below was re-run to verify it.

## 1. Root cause of "over-matching / under-matching" bugs

### 1.1 Matcher joins words and loses boundaries (`src/core/text_detector.py`)
- `_build_unit()` uses `sep = ""` (line 85) when joining RapidOCR words. So
  `"Internal VPN enabled"` becomes `"internalvpnenabled"`:
  - `word_boundary=True` + `VPN` → **[]** (missed, even though `VPN` stands on its own).
  - `word_boundary=False` + `VPN` → still **[]** on the phrase above, but `VPNX` → **wrong match**.
- `_exact_spans()` returns exact character offsets `(s, e)`, but `_word_index_at()` +
  `box_for()` (lines 63–70, 108–112) **discard that offset and return the whole word's box**.
  With `abcxyzzza` + `zza`:
  - `word_boundary=True` → **[]**.
  - `word_boundary=False` → 1 match but `box=(0,0,81,20)` = **the entire `abcxyzzza`**.
- Fuzzy only rescues strings ≥5 chars (`_fuzzy_spans`, lines 138–139), so `zza` has no fallback path;
  conversely `Confidential` fuzzily matches into `Confidentiality` with ratio 0.89 → erases the whole 212 px word.
- Dedupe prefers the larger box when a small box lies >85% inside a large one (lines 255–274) →
  the sub-box extracted later gets removed by the old box itself unless this is fixed.

### 1.2 OCR only provides word boxes, not trustworthy character boxes
- `src/engines/ocr_engine.py:37-44` enables `Global.return_word_box=True`, does not enable
  `return_single_char_box`.
- RapidOCR merges `abcxyzzza` into **1 word** (no spaces, CTC cannot split it).
  Enabling `return_single_char_box=True` makes the cells **inferred from CTC column index + average width**,
  not from pixels: the measured `zza` box is ~19 px wider than the real glyph on the upscaled image, and cells overlap.
  => Do not use as the final source of coordinates.
- Splitting the word box evenly by character count is also wrong with proportional fonts (glyph width 45–54 px,
  spacing 5–16 px).
- **Column projection on pixels** inside the word box gives the correct result on synthetic images:
  9 stable column groups at thresholds 20–80; `zza` → `x=[420,581]` vs ground truth `[421,579]`.

### 1.3 Erase regions can spill onto other elements (`ui_element_detector.py`, `mask_generator.py`)
- The ELEMENT mask is a **filled rectangle** + padding, only clamped at Sobel >55; measured dark pill edges
  have gradient only 27 → padding spills 2 px onto the background. `_refine_bbox` moves edges toward the strongest
  Sobel within ±4 px, and can lock onto an adjacent glyph/icon edge.
- The `_swallows_other_text` guard uses the OCR **line box**; `pipeline.py:110-115` drops
  lines with IoU >0.2 against a match and lines made of unknown characters → neighbours merged by OCR become "invisible".
- The TEXT mask widens the crop by `max(pad+2, 0.25·h)` (usually 7 px) then takes **every glyph** in the crop;
  a correctly extracted box can also pull neighbouring glyphs into the mask.
- Fallback coverage `<0.004`/`>0.85` (lines 114–118) erases the whole box on textured backgrounds.
- The invariant `out[mask==0] == orig[mask==0]` now holds (hard composite), but **no test**
  checks that "pixels of other elements stay intact" when the input bbox is wrong.

### 1.4 Current UI (`src/ui/main_window.py`)
- ~21 controls always visible; 3 action buttons side by side; internal wording leaks into the UI
  ("Engine", "Erase region" = `element_policy`, "Source" = `ocr/color`), the "Preview result" toggle
  changes its label to "View original" but stays checked, log hidden, error/validation modals ~8 places,
  preview forced to ~290 px. For details see the sub-agent report (quoted in §2).

## 2. Proposed approach

### A. Matcher: exact matching + exact character coordinate extraction
1. **Fix the foundation)**: `sep = " "` in `_build_unit`; keep `_Span` on the joined text.
   → restores whole-word/multi-word matching with `word_boundary=True`.
2. **Allow controlled substring)**: for a match lying entirely inside 1 word,
   call `localize_subword(line, word, s, e)`:
   - deskew by `TextLine.quad`, estimate the local background with a 2 px border median,
     threshold `max(8, min(0.65·Otsu, 70))`, column projection inside the word box;
   - **require number of column groups == number of characters in the word** (mismatch → failure);
   - map `[s-word.start, e-word.start)` to group boundaries, clamp inside the word box, pad 1–2 px;
   - store the result in `Word.char_boxes` (new optional field).
3. **Safe fallback**: localization fails → `RegionKind.UNCERTAIN, enabled=False`
   with the word box (shown for the user to review), **never automatically erase the whole word**.
4. **Consistent threshold**: keyword <5 chars → **whole-token only** (reject `VPNX`/`VPN`, keep current behaviour);
   keyword ≥5 chars → substring allowed but localization must succeed, fuzzy raised to 0.90
   (`Confidentlal`=0.92 still passes, `Confidential` in `Confidentiality`=0.89 rejected).
5. Dedupe prefers the smaller box; mask margin for localized substring clamped at 2 px.

### B. Regions & mask: word-geometry is the boundary, pixels only refine
- INVARIANTS (assert in tests):
  - **I1** `mask ∩ dilate(any other WORD box, 2) = ∅` (use word boxes, not just line boxes).
  - **I2** `mask ⊆ matched_box + 2 px` (max padding 6 chosen by the user).
  - **I3** `np.array_equal(out[mask==0], orig[mask==0])`.
  - **I4** ELEMENT only enabled when `conf≥0.75, closed≥0.70, border≥0.45, ratio≤12`, passes I1; else UNCERTAIN.
  - **I5** TEXT glyph coverage within `[0.02,0.60]`; outside the range → UNCERTAIN, **no fallback that erases the whole box**.
- ELEMENT clamps edges according to `max(25, 0.5·side_max)` instead of a fixed 55 (measured: dark pill 27, solid button 447–552).
- Debug adds `<stem>_regions.json`, `<stem>_crop_<id>.png`, `<stem>_diff.png` to see spill-over onto neighbours right away.

### C. UI: 4-step wizard, at most ~8 visible controls per step
`1 Choose images › 2 Keywords › 3 Review › 4 Save`, a single primary button per state,
validation via disabled state (no modal), the post-save summary is a fixed card (no modal).
Advanced collapses into one place holding engine/padding/preset/log. Preview: segmented `Original | Result`,
the help row sits **outside** the canvas, still keeping drag/resize/zoom. UNCERTAIN is only erased after a per-region
"Agree to erase" button; there is no bulk-approve.

## 3. Test matrix (write BEFORE coding)

### A. Matcher unit (fake OCR, deterministic)
`test_exact_substring_at_start_is_localized` (`zzaxyz` + `zza`)
`test_exact_substring_in_middle_is_localized` (`abzzac` + `zza`)
`test_exact_substring_at_end_is_localized` (`abcxyzzza` + `zza` ← the user's case)
`test_substring_case_insensitive_localization` (`abcXYZZA`)
`test_substring_multi_occurrence_all_found` (3 occurrences within 1 word / across words)
`test_prefix_substring_VPNX_rejected_exact` + `test_infix_AVPNB_rejected_exact`
`test_fuzzy_disabled_for_keyword_under_5_chars` (including `fuzzy_threshold=0.5`)
`test_whole_word_in_multiline_still_matches` (`Internal VPN enabled` + `VPN`) ← currently FAIL
`test_multiword_exact_keyword_matches` (`internal use only`, exact) ← currently FAIL
`test_fuzzy_typo_over_5_chars_preserved` (`Confidentlal`)
`test_substring_fallback_is_uncertain_when_segmentation_fails`

### B. Pixel / E2E (FakeOCR + real OCR with markers)
`test_substring_removal_changes_only_zza_bbox` — changed pixels outside bbox = 0
`test_neighbouring_element_untouched` — ROI of another element bit-identical
`test_element_candidate_swallowing_neighbour_is_rejected`
`test_uncertain_substring_is_skipped` — status `skipped_uncertain`, no file written
`test_coordinate_roundtrip_after_upscale` (±2 px on the original image)
`test_tiling_boxes_are_original_coordinates` (4K)
`test_real_ocr_substring_box_precision` (`@pytest.mark.real_ocr`, ±2 px ground truth JSON)
`test_real_image_vpn_neighbours_intact` (`No SIM`, `21:33`, `41%` bit-identical)

### C. UI safety (offscreen)
`test_ui_control_budget` (≤12 interactive, ≤3 groupBox visible)
`test_analyze_disabled_until_images_and_keywords`
`test_uncertain_never_bulk_enabled` (keep the existing test)
`test_save_disabled_when_no_enabled_region`
`test_no_modal_spam` (≤1 QMessageBox/action, 0 for uncertain)
`test_result_view_hides_boxes`
`test_responsive_1366x768`
`test_worker_has_no_qt_objects`

## 4. Workflow & gates (no skipping steps)

| Stage | Command | Required gate |
|---|---|---|
| **R – Reproduce** | add 3 red tests (`zza`, exact box, `VPNX`), run `pytest -q tests/test_substring_exact.py` | 3 tests **fail for the right reason** + 34 existing tests still pass |
| **T – Test matrix** | `pytest --collect-only -q`; create fixture `substring_zza.png` + ground truth JSON | every test name in §3 is present, fixture+JSON exist, DejaVu font available |
| **I – Detection** | `pytest -q tests/test_matcher.py tests/test_mask_safety.py tests/test_substring_exact.py` | 100% green; **UI untouched** |
| **U – UI** | `QT_QPA_PLATFORM=offscreen pytest -q tests/test_gui.py tests/test_ui_safety.py` | UI green + 34 legacy tests green |
| **V – Verify** | full pytest + `/tmp/opencode/sc/eval.py` + CLI on a real image + GUI smoke | every `invariant=True`; neighbour ROIs bit-identical; smoke through `save_done` |
| **B – Bundle** | `pyinstaller --noconfirm --clean imagecleaner.spec` + selftest from `/tmp` | `SELFTEST OK`, bundled GUI runs offscreen for 12 s without errors |

## 5. Acceptance criteria (not subjective)
- `abcxyzzza` + `zza` → exactly 1 region, box = ground truth (±2 px real OCR), `abcxy` unchanged.
- `VPNX` + `VPN` → 0 matches in both exact and fuzzy.
- All fixtures: `out[mask==0] == orig[mask==0]`; changed pixels outside the expected bbox = 0.
- Real image `image_vpn.png`: shape 54×478, `No SIM` / `21:33` / `41%` ROIs bit-identical,
  alpha preserved, 1 `success`.
- UI: ≤12 visible controls, ≤1 modal/action, uncertain off by default, smoke `save_done` + report CSV/JSON.
- Full suite green, no added xfail to hide localization bugs.

## 6. Decisions already settled (2026-11-25)

| # | Decision | Consequence |
|---|---|---|
| D7 | **Controlled substring**: keywords ≥5 chars are allowed to sit inside 1 word; keywords <5 chars only match a substring when it is a **suffix of the token** (ends at a token boundary). Every substring must localize successfully (column projection) before it is auto-erased | `zza` at the end of `abcxyzzza` → exactly 3 glyphs; `VPN` at the start of `VPNX` → rejected; `AVPNB` → rejected |
| D8 | **Single-screen UI, cleaned up**: ≤8 visible controls + collapsed "Advanced options" panel; no wizard | Keep the `MainWindow` structure, trim widgets/rename labels/disabled states |
| D9 | **ELEMENT auto when evidence is strong** (`conf≥0.75, closed≥0.70, border≥0.45`), otherwise UNCERTAIN | Keep the current policy, add guard I1 |

> Next stage: **R (reproduce) → T (test matrix)**. Move to I only when the new tests
> fail for the right reason and the 34 existing tests are still green.

## 7. Stage R/T results (already run)

```
$ QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -m "not real_ocr"
18 failed, 39 passed, 1 deselected in 1.02s
```

- 39 pass = 34 existing tests + 5 new tests (prefix/infix rejected, fuzzy short disabled, typo preserved, whole-word VPN).
- 18 red: 11 matcher/pixel (`zza` missed or returns the whole word, `sep=""` loses the boundary),
  7 UI safety (control budget 21>12, no advanced toggle yet, save/analyze not disabled yet,
  validation modal, result view does not hide boxes yet, button 25px<32px).
- New fixture: `tests/fixtures/substring_zza.png` + `substring_zza.json` (ground truth 9 char boxes).
- **Gate R/T: PASS** → allowed to move to Stage I (core changes only, UI untouched).

## 8. Results of stages I → B (already run)

| Stage | Gate | Actual result |
|---|---|---|
| I – Detection | matcher + mask + substring green, UI untouched | `40 passed` fake OCR, then `51 passed` with real OCR |
| U – UI | UI safety green + legacy green | `11 passed` (4 old GUI + 7 new UI); full `58 passed` |
| V – Verify | 7 fixture invariants + real image + smoke | 7/7 `invariant=True`; `image_vpn.png` 1 success, `No SIM`/`21:33`/`41%` ROIs bit-identical, alpha preserved; smoke through `save_done`, 2 images + report |
| B – Bundle | build + selftest from outside the repo | build exit 0; `SELFTEST OK`; bundled GUI offscreen ran 12 s without errors |

Additional evidence for the `abcxyzzza` + `zza` case (real CLI, new fixture image):
```
changed bbox = (251, 51, 94, 37)  ·  3253 px changed within zza_bbox
prefix_bbox (abcxyz) changed = 0   ·   keep_bbox (KEEPTHIS) changed = 0
```
UI after D8: **8 visible controls** (was 21), min window 835×653, primary button 46 px.

### Main implementation changes
- `Word.char_boxes`, `Match.localized`, `Match.partial` (models).
- `src/core/char_localizer.py`: column projection inside the word box, validating 1 group/character,
  returns `None` when ambiguous → UNCERTAIN.
- `ocr_engine.py`: populate char boxes, fallback word = line when word_results mismatch; map coordinates on upscale/tile.
- `text_detector.py`: `sep=" "`, boundary policy D7 (short suffix only), `_match_geometry`
  uses char boxes for the partial span, dedupe prefers the smaller box.
- `mask_generator.py`: partial localized match uses the exact character bbox, **no padding/no glyph search**
  to avoid spilling onto adjacent characters.
- `pipeline.py`: `match.localized=False` → UNCERTAIN disabled; map char boxes per tile.
- `ui/`: 2-line input, collapsed Advanced panel, segmented `Original | Result`, hide boxes in result mode,
  validation via disabled state + inline banner, remove bulk buttons and fit/zoom buttons.

> **Phase 9 update (2026-11-26)**: accepted `evidence/e1–e2`, per-region policy, wipe compare, drag-drop and I5 symmetry. See section 16 of `docs/STATUS.md`.

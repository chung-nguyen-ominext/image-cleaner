# ImageCleaner Optimization Plan (Phase 8)

> Status: **Stage R + T-lite + I1–I5 + UI fixes + P1 + P2 (partial) + Phase 10 + Phase 11–23 (UI polish, ImageCleaner rename) done; 127 tests green**.
> Date: 2026-11-25 · Implementation update: 2026-11-26
> Baseline before Stage R: **77 passed**. Stage R: **8 failed, 77 passed** (as intended).
> Final result: **127 passed**; I1–I5 + UI fixes + P1 + evidence acceptance done;
> P2 still has thumbnail/status badge + clipboard + preview cache.
>
> This document fixes the order of work: **P0 correctness → P1 speed → P2 UI/UX**.
> All numbers below were measured read-only by scripts in `/tmp`, without changing the repo.

---

## 0. Goals and limits

### 0.1 Goals

1. **Pixel-correct**: erase the right object, restore the right container, keep unrelated
   objects (icons/text/other UI) untouched.
2. **Frame-correct**: distinguish `button/frame` from `container/panel`; don't merge icons/annotations
   into the frame; don't take a container for a button.
3. **Background-correct**: each region uses a **local** background, **stopping at the container border**,
   and doesn't take the page color outside.
4. **Preview-correct**: the preview image must be identical to the image that will be saved.
5. **Faster**: reduce repeated full-image operations, unnecessary second OCR pass, full-res preview.
6. **Clear UI**: the user knows which regions are erased, can choose "text only / whole block", and isn't spammed by modals.

### 0.2 Not doing in Phase 8

- Don't change D7 matching semantics (`zza`, `VPN` vs `VPNX`, substring).
- Don't add cloud/API/heavy dependencies; stay offline, CPU-only.
- Don't automatically erase UNCERTAIN regions.
- Don't overwrite the original image; keep the invariant `out[mask == 0] == orig[mask == 0]`.
- Don't claim Windows/macOS are verified without a real build.

### 0.3 Principles

- **Measure first, fix later**: every change must have a failing test or a corresponding measurement.
- **Don't widen the mask on gut feeling**; every erased pixel must have evidence.
- **Don't replace a region to be restored with a single color** if the background isn't truly uniform.
- **Fail-safe**: unsure → `UNCERTAIN`, don't erase.
- **Detection stays as is**; only add a validation/refinement step after detection.

---

## 1. Investigation results (reproduced, read-only)

| ID | Scenario | Measurement | Conclusion |
|---|---|---|---|
| R1 | Image with 2 different containers (gray card on black background + blue box on white background), rendered at the same time | Gray card `(98,94,92)` → `(219.4,160.3,135.8)`, mean error **77.2**; rendered separately exactly `0.0` | One shared mask/model for multiple regions is wrong |
| R2 | Image with `VPN` + text `KEEP` in the same button; compare preview with the saved version | Mask off by **2.856 px**; saved version keeps `KEEP`, preview erases `KEEP` to white | `_snapshot()` drops `lines` → different refinement |
| R3 | Button + icon + red annotation case, real detection | Correct candidate `(55,176,153,40)`, `conf=0.892`, `src=color` rejected because the icon OCRs as `"G"`; final result is `TEXT`, red still has **336 px** | Text guard wrongly blocks a correct frame |
| R4 | Text on a blue box, real detection | Detector returns `ELEMENT (60,140,341,71)` = the whole box; text ROI output becomes `(255,255,255)` instead of `(235,99,37)` | Container mistaken for a button; missing per-region policy |
| R5 | 4K `big_4k.png` | Analyze **7.4 s**, OCR 6.2 s, render 0.61 s | OCR is the main cost; render is still full-image |
| R6 | Add 1 keyword not present in the image | The `match` phase adds ~0.7–1.9 s due to the second OCR pass | The extra OCR pass runs too broadly |
| R7 | Current UI | 8 basic controls + `QListWidget` + `QTableWidget`; the `Delete` button is hidden, no Delete shortcut | Can't remove an image from the list |
| R8 | Save image UI | `QMessageBox.information` after every save | Modal spam, against D8 |
| R9 | Analyze-error UI | Failed entries aren't passed through `_update_list_item()` | The list doesn't show the error status |

---

## 2. Root causes (mapped to code)

### 2.1. One mask/model for all regions

- `src/core/pipeline.py:319-326`: merges every mask into one union `mask`, calls the engine **once**.
- `src/core/background_restore.py:79-189`: `estimate_background()` receives the union mask, the annulus
  surrounds the whole cluster → it sees many containers/pages at once.
- `src/core/background_restore.py:265-281`: `restore_masked()` snaps the whole mask to one color if
  `inlier_fraction >= 0.20`.

### 2.2. Rectangular mask, not a silhouette

- `src/core/mask_generator.py:78-85`: `ELEMENT` fills the full `full[y:y+h, x:x+w] = 255`.
- `src/core/mask_generator.py:109-116`: `TEXT` partial fills the whole rect of the char box.
- `src/core/mask_generator.py:124-128`: bad coverage → erase the whole box (dangerous fallback).

### 2.3. Refinement is still heuristic, not general

- `src/core/mask_refinement.py:47-110`: an icon is only protected if it is dark, low-saturation, compact,
  and sits **to the left** of the text; hard thresholds `area >= 40`, `5..160 px`, scope `±100/±60`.
- `src/core/mask_refinement.py:113-132`: red annotation takes every red component intersecting the ±20 px scope.
- `src/core/mask_refinement.py:135-159`: mirror icon through the centroid, dark pixels only, without checking
  whether the object is actually symmetric.
- `src/core/mask_refinement.py:166`: only runs for `ELEMENT`, not for `TEXT`.

### 2.4. Frame/container not distinguished

- `src/core/ui_element_detector.py:239`: `boundingRect(contour)` merges button + icon + annotation.
- `src/core/ui_element_detector.py:526-545`: `_swallows_other_text()` only protects OCR text,
  and rejects the whole frame if text/icon is mis-OCRed.
- `src/core/ui_element_detector.py:43-85`: perimeter evidence is averaged, doesn't check
  each side/corner/straightness.
- `src/core/ui_element_detector.py:415-458`: snap may latch onto icon/text edges.

### 2.5. Preview and save run two different paths

- `src/ui/main_window.py:94-103`: `_snapshot()` sets `clone.lines = []`.
- `src/core/pipeline.py:314`: `refine_element_mask(..., result.lines, ...)` needs lines.
- `src/ui/main_window.py:856-874`: overlay uses `build_region_mask()` directly,
  without refinement.

### 2.6. Speed: repeated full-image work

- `src/core/background_restore.py:203-235`: `contaminated_band()` creates gray + `medianBlur(31)`
  over the full image.
- `src/core/background_restore.py:46-50`: `_ring()` dilates the full mask several times.
- `src/core/mask_refinement.py:50-70,113-126`: HSV/gray/connectedComponents over the full image **per region**.
- `src/core/ui_element_detector.py:220-221`: `_refine_bbox()` converts the full image to gray + Sobel
  for each candidate.
- `src/core/pipeline.py:63-74,156-216`: second OCR pass when any keyword is missing.
- `src/core/pipeline.py:81-82` and `398-401`: build the mask at analyze, then reset and rebuild at render.

### 2.7. UI/UX

- `src/ui/main_window.py:169-171`: delete button hidden, no Delete shortcut.
- `src/ui/main_window.py:921-930`: failed entry `continue`s before updating the list.
- `src/ui/main_window.py:1159`: modal after saving.
- `src/ui/main_window.py:238-242`: the "text only / whole block" policy only lives in the global Advanced options.
- `src/ui/preview.py`: only toggles Original/Result, no split/wipe; `element/text` labels leak into the UI.
- `src/ui/main_window.py:277-280`: image list has no thumbnail/status badge.
- No drag-and-drop, no clipboard paste, no undo when dragging/resizing.

---

## 3. Proposed architecture

### 3.1. New render flow

```
AnalysisResult (detection unchanged)
        │
        ▼
[1] MaskPlan per region
    ├─ frame/container classification
    ├─ silhouette target
    ├─ protected objects (visible pixels)
    ├─ hidden/occluded parts + symmetry plan
    └─ policy: text_only | element | uncertain
        │
        ▼
[2] Group mask components by proximity/container
        │
        ▼
[3] For each group (ROI only)
    ├─ crop image + mask + protected
    ├─ local background model (edge-aware)
    ├─ sanitize guide
    ├─ engine inpaint
    └─ paste only inside mask
        │
        ▼
[4] Verify invariants
    ├─ out[mask==0] == orig[mask==0]
    ├─ visible protected pixels unchanged
    └─ preview mask == saved mask
        │
        ▼
[5] Composite + save
```

### 3.2. Extended data model (proposed, not yet coded)

Add metadata to `Region`/`MaskPlan` so the UI and tests can see the decision:

```text
MaskPlan
  region_id
  policy            text_only | element | uncertain
  target_mask       true silhouette of the object to erase
  protected_mask    pixels of other icons/text/UI that must be kept
  hidden_mask       occluded part of protected, needs restoring
  restore_plan      mirror | exemplar | inpaint | skip
  frame_evidence    border_side_coverage, corner_score, symmetry, ...
  mask_confidence
  notes
```

Extended `BackgroundModel`:

```text
kind: flat | plane | quadratic | texture | none
samples / clusters
confidence
residual / variance
predict(ys, xs)
```

Don't change the public `ProcessOptions` unless needed:
- `refine_mask` stays as is;
- add `container_policy` default `auto` (proposed);
- `flat_fill` should be removed or renamed to `exact_uniform_fallback`, default `False`.

### 3.3. Frame/container classifier

Input: `bgr`, `text_box`, `match_boxes`, `other_boxes`.

Steps:

1. **Background color seed**: color mode inside the text box, drop glyph pixels.
2. **Fill region**: region-grow in Lab with tolerance scaled by `text_height`,
   stop at strong edges (Sobel percentile) or the image border.
3. **Measure evidence**:
   - `border_side_coverage[4]`: whether each side has a continuous border;
   - `corner_score`: whether the 4 corners intersect;
   - `straightness[4]`: whether the border is straight/even;
   - `margin_symmetry`: left/right/top/bottom around the text;
   - `fill_uniformity`, `fill_vs_page_contrast`;
   - `area / text_area`, `size / text_height`;
   - `contains_other_components` (icon/text/other);
   - `aligned_with_card_edge`.

4. **Decision**:

| Condition | Result |
|---|---|
| Closed border on 4 sides, clear corners, relatively even padding, uniform fill | `ELEMENT` |
| Large fill, touches the card/panel edge, no surrounding border | `CONTAINER` → default `TEXT` |
| Another icon/object intersects the frame | `ELEMENT` + carve protected (don't reject) |
| Conflicting evidence | `UNCERTAIN` + UI lets the user choose |

5. **Important**: if the frame is correct but an icon confuses OCR, **don't reject the whole thing**.
   Turn the icon into a protected object and continue.

Thresholds must be a function of `text_height`/image size, not hardcoded px.

### 3.4. Silhouette mask + protected objects

1. `ELEMENT` mask = fill region + real border, light morphological close, only fill holes belonging to the element.
2. Subtract:
   - OCR word boxes not part of the match;
   - connected components of other icons/logos/UI;
   - red annotation **not connected** to the target;
   - pixels outside the true silhouette.
3. `TEXT` mask = glyph pixels in the char box, don't fill the whole rect.
4. If glyph pixels can't be determined → `UNCERTAIN`, don't fall back to erasing the whole box.
5. Always keep `protected` visible pixels bit-identical; only the `hidden` part may be restored.

### 3.5. Local background model (edge-aware)

Replace the geometric annulus with a **geodesic ring**:

- From the mask border, propagate through pixels with low gradient / same container.
- Stop at strong edges (card/box/page border).
- Cluster the colors; if there are multiple clusters, assign each mask pixel to the nearest border in the same container.
- Fit:
  - `flat` if variance is low;
  - `plane`/`quadratic` if there's a gradient;
  - `texture` if variance is high → patch statistics / PatchMatch / constrained LaMa.
- Don't use a single color for the whole mask if the mask spans multiple containers.
- Whole-mask `snap_uniform` is removed; direct fill is only allowed when the mask is entirely inside
  one verified uniform region and with an explicit flag.

### 3.6. Symmetry-aware completion (avoid losing symmetry)

1. Identify the protected object (icon/logo) and its **visible mask**.
2. Compute the real symmetry axis:
   - PCA on visible pixels;
   - or a given axis if the object is clearly geometric (circle/rounded rect).
3. Measure `mirror_residual` on the visible part:
   - if residual is low → symmetric object, mirroring allowed;
   - if high → **no mirror**; use exemplar/LaMa or mark UNCERTAIN.
4. Only mirror the **hidden part**, never overwrite visible.
5. Validate mirrored pixels:
   - same object/color/edge;
   - not taken from other text/background;
   - if confidence is insufficient → let the engine handle it or `skip`.
6. Don't mirror text/glyphs; only icon-like objects.

### 3.7. Preview = Saved

Extract a shared function:

```text
build_final_masks(image, regions, options) -> mask, protected, plans
render_with_plans(image, plans, options) -> output, engine
```

- `_snapshot()` keeps `lines` (or boxes) so refinement is identical.
- `_rebuild_mask_overlay()` uses exactly the `mask`/`protected` from `build_final_masks`.
- `render()` and `_on_view_mode()` use the same pipeline.
- Required tests: preview mask == saved mask; preview output == saved output bit-exact.

### 3.8. UI/UX spec (Phase 8)

Keep one screen, but with clear layering:

1. **Input**: Add images / Folder / Paste clipboard; keyword chips; preset.
2. **Preview**:
   - segmented `Original | Result | Compare`;
   - split/wipe slider;
   - zoom +/−/fit;
   - mask overlay exactly the one that will be erased;
   - protected objects shown in a different color;
   - undo/redo when dragging/resizing.
3. **Region review**:
   - cards instead of a table: crop thumbnail, text, kind, confidence;
   - toggle erasing each region;
   - segmented **"Text only | Whole block"** per region;
   - UNCERTAIN has an "Approve" button, no bulk approve.
4. **Save**:
   - inline banner (success/not_found/skipped/failed);
   - clear "Save this image" + "Save all";
   - "Open folder", "View report";
   - no modal unless quitting while running.
5. **Advanced**:
   - "Quality: Fast / Balanced / High" instead of Telea/NS/LaMa;
   - padding, preset, output, log;
   - hide internal jargon.
6. **File list**:
   - thumbnail + status badge;
   - Delete/context menu to remove an image;
   - drag-and-drop, Ctrl+V;
   - persist geometry/splitter/selected image.

### 3.9. Performance spec

| Change | Reason |
|---|---|
| Cache `gray`, `hsv`, Sobel, Canny, integral image per image | Detector/refinement don't recompute full-image |
| ROI processing for mask/background/sanitize/inpaint | 4K only processes the area around the mask |
| Drop duplicate mask build in `analyze()`/`write_result()` | Saves one pass per region |
| Second OCR pass only when a keyword is missing + there's a text-like block + limited crop | Saves 0.7–1.9 s/image |
| Cache OCR result in the GUI | Switching images doesn't re-OCR |
| Preview downscale/cache, debounce | Result toggle near-instant |
| Engine chosen by texture energy + mask area | Avoid LaMa for flat, avoid Telea for texture |
| LaMa crop keeps aspect ratio / tile overlap | More detail, less hallucination |

Proposed goals (re-measure, not a commitment):
- 4K analyze: 7.4 s → **< 4 s** on the same CPU;
- 4K flat-background render: 0.6 s → **< 0.25 s**;
- preview toggle: **< 150 ms** when cached;
- batch of 20 images: no model reload, no double OCR.

---

## 4. Test matrix — write before changing code

### 4.1. Reproduce (must be red with the current code)

These tests prove the bugs and must not be hidden with `xfail`.

| Test | Fixture / input | Assertion |
|---|---|---|
| `test_multi_region_local_backgrounds_are_independent` | image with 2 containers: gray card on black + blue box on white | render together: each ROI differs by ≤2 from the container color; separate render == combined render |
| `test_preview_snapshot_matches_saved_render` | image with `VPN` + `KEEP` in a button | `mask_preview == mask_saved`; `KEEP` ROI bit-identical in both |
| `test_frame_with_overlapping_icon_is_not_rejected` | `vpn_button_icon_annotation.png` | region is `ELEMENT`; bbox contains the button; icon visible bit-identical |
| `test_container_is_not_classified_as_button_by_default` | `blue_box_white_page.png` | default policy `text_only`; text ROI = `(235,99,37)`; box remains |
| `test_mask_overlay_uses_refined_mask` | image with icon + other text | overlay mask == real render mask |
| `test_failed_analysis_updates_list_item` | corrupt file | item text contains "error", tooltip has error |
| `test_delete_key_removes_selected_file` | 2 images | press Delete → list has 1 left, `_entries` has 1 left |
| `test_save_uses_inline_summary_not_modal` | save 1 image | `QMessageBox.information` is not called; banner has a summary |

### 4.2. Unit — frame/container

| Test | Assertion |
|---|---|
| `test_frame_classifier_filled_button` | button closed on 4 sides → `ELEMENT` |
| `test_frame_classifier_outline_button` | button with only a border → `ELEMENT` |
| `test_container_large_flat_area_is_text_only` | large box, no border → `TEXT` by default |
| `test_frame_rejects_union_with_icon_but_keeps_frame` | candidate merges icon → real frame still selected, icon goes into `protected` |
| `test_frame_asymmetry_guard` | text clearly offset to one side, candidate extends abnormally → not `ELEMENT` |
| `test_frame_thresholds_scale_with_text_height` | same layout at 1× and 2× scale → same decision |
| `test_perimeter_requires_all_sides` | missing 1 clear side → not auto `ELEMENT` |

### 4.3. Unit — mask/protected

| Test | Assertion |
|---|---|
| `test_element_mask_is_silhouette_not_rectangle` | mask doesn't cover corners outside the silhouette; area < bbox |
| `test_text_mask_is_glyph_pixels` | mask ⊂ glyph, doesn't cover the background between strokes |
| `test_protected_icon_right_of_text` | icon on the right is protected |
| `test_protected_icon_light_on_dark` | light icon on a dark background is protected |
| `test_protected_icon_colored` | colored icon is protected |
| `test_protected_icon_scaled_4k` | large 4K icon is protected |
| `test_red_annotation_connected_to_target_removed` | red connected to target → mask includes red |
| `test_unrelated_red_badge_is_kept` | red not connected to target → not erased |
| `test_other_ocr_text_never_masked` | other text bit-identical |
| `test_bad_text_coverage_becomes_uncertain` | coverage out of range → `UNCERTAIN`, box not erased |

### 4.4. Unit — background/inpainting

| Test | Assertion |
|---|---|
| `test_geodesic_ring_stops_at_container_edge` | samples don't cross the card/box border |
| `test_background_multiple_clusters` | mask over 2 color regions → each pixel takes the correct cluster |
| `test_no_snap_flat_fill` | texture/gradient isn't replaced by a single color |
| `test_texture_contamination_removes_colored_fringe` | red/colored fringe on texture isn't copied in |
| `test_sanitize_guide_does_not_fill_whole_mask` | only the source band is modified |
| `test_lama_preserves_aspect_and_resolution` | long/narrow crop isn't over-downscaled |
| `test_engine_choice_uses_texture_energy` | texture → LaMa; flat → Telea |
| `test_outside_mask_bit_identical` | every case |
| `test_visible_protected_pixels_bit_identical` | every icon case |

### 4.5. Unit — symmetry

| Test | Assertion |
|---|---|
| `test_symmetric_icon_mirror_only_hidden` | visible pixels unchanged; hidden filled |
| `test_asymmetric_icon_does_not_mirror` | no symmetric ghost created; `UNCERTAIN`/engine fallback |
| `test_mirror_uses_pca_axis` | tilted axis still correct |
| `test_mirror_does_not_use_text_or_background` | source pixels not belonging to the object are excluded |
| `test_mirror_low_confidence_is_skipped` | insufficient evidence → not written |

### 4.6. Integration / pipeline

| Test | Assertion |
|---|---|
| `test_multi_region_pipeline_matches_separate_render` | 2 containers, output identical to separate render |
| `test_preview_and_save_are_bit_identical` | GUI snapshot == save |
| `test_pipeline_real_detection_case3` | `ELEMENT` + icon visible intact + red target 0 |
| `test_pipeline_real_detection_case2_text_only` | blue box remains, text gone |
| `test_batch_continues_after_bad_image` | keep old test |
| `test_uncertain_never_written` | keep old test |
| `test_output_never_overwrites` | keep old test |

### 4.7. Performance (`perf` marker, not run by default)

| Test | Assertion |
|---|---|
| `test_analyze_4k_budget` | < 4 s on the reference machine |
| `test_render_4k_flat_budget` | < 0.25 s |
| `test_preview_cached_toggle_budget` | < 150 ms |
| `test_second_ocr_pass_only_when_needed` | image has enough keywords → `_ocr_colored_blocks` is not called |
| `test_mask_build_called_once_per_region` | no duplicate build in analyze/render |

### 4.8. UI safety

| Test | Assertion |
|---|---|
| `test_control_budget` | keep ≤12 interactive; state explicitly if list/table counts |
| `test_delete_and_context_menu` | can remove an image |
| `test_per_region_policy_control` | each region has `Text only / Whole block` |
| `test_split_compare_exists` | compare mode exists |
| `test_uncertain_never_bulk_enabled` | keep old test |
| `test_no_modal_on_save` | no modal |
| `test_failed_status_visible` | list item "error" |
| `test_preview_mask_matches_render_mask` | overlay == real mask |

### 4.9. New fixtures (deterministic)

Create with `scripts/gen_test_images.py` (DejaVu, fixed seed):

| Fixture | Content |
|---|---|
| `multi_container.png` + JSON | gray card on black + blue box on white |
| `frame_overlap_icon.png` + JSON | button + icon + red annotation |
| `container_text.png` + JSON | text on a large box, no border |
| `protected_icon_variants.png` + JSON | icon left/right/top/bottom, light/dark/colored |
| `red_annotation_unrelated.png` + JSON | unrelated red badge |
| `texture_container.png` + JSON | texture + red fringe |
| `preview_parity.png` + JSON | `VPN` + `KEEP` in the same button |
| `big_4k_multi.png` + JSON | multiple 4K regions |

---

## 5. Workflow and gates

| Stage | Work | Gate |
|---|---|---|
| **R – Reproduce** | add 8 tests §4.1 | all red for the right reason; 77 old tests still green |
| **T – Test matrix** | add fixtures + all tests §4.2–4.8 | `pytest --collect-only` has all names; fixtures + JSON exist |
| **I1 – Render per-region** | split mask plan, local background | R1 green; R2 partial |
| **I2 – Frame classifier** | container/frame + policy | R3, R4 green |
| **I3 – Silhouette/protected** | real mask + protected | icon/red tests green |
| **I4 – Preview parity** | shared pipeline | R2, overlay tests green |
| **I5 – Symmetry** | PCA + mirror validation | symmetry tests green |
| **U – UI** | Delete, status, inline save, per-region policy | UI tests green |
| **P – Performance** | ROI/cache/OCR/preview | perf tests pass |
| **V – Verify** | full suite + 7 fixtures + real image + CLI + GUI smoke | 100% green; invariants; neighbours bit-identical |
| **B – Bundle** | PyInstaller + selftest | `SELFTEST OK` |

Gate commands:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -m perf
.venv/bin/python -m src.cli --input tests/fixtures/multi_container.png \
  --output /tmp/opencode/sc/p8 --keywords VPN --debug
```

---

## 6. Acceptance criteria

### 6.1. Pixel

- Multi-container: each ROI differs by ≤2 (flat) / ≤8 (texture) from ground truth.
- `out[mask == 0]` bit-identical to the original image.
- Visible protected pixels bit-identical.
- Red annotation on the correct target: 0 px; unrelated red: kept as is.
- Other text/other icons: bit-identical.
- No more snapping the whole mask to a single color.

### 6.2. Frame

- Case 2: only text erased by default; blue box remains.
- Case 3: frame recognized, button + red erased, icon remains.
- Icons on the right/top/bottom, light/colored, 2×/4K scale are all protected.
- Don't reject a frame just because an icon OCRs as text.

### 6.3. Preview

- `preview_mask == saved_mask`.
- `preview_output == saved_output` bit-exact.
- Overlay uses exactly the mask that will be erased.

### 6.4. Speed

- 4K analyze < 4 s; flat render < 0.25 s; cached preview < 150 ms.
- Batch doesn't reload the model / double-OCR when unnecessary.

### 6.5. UI

- Images can be removed with Delete/context menu.
- Failed images show their status.
- No modal on save.
- "Text only / Whole block" exists per region.
- Split compare exists.
- No internal jargon exposed.

---

## 7. Risks and honest limitations

1. **Pixels fully occluded don't exist in the original image**: they can only be approximated; cannot be bit-exact.
2. **Symmetry isn't always right**: asymmetric icons, letter logos, cropped icons;
   must check the residual and fall back to `UNCERTAIN`/engine.
3. **Frame vs container is a semantic problem**: the same image can have two intents
   ("erase text" vs "erase the whole button"); per-region UI is mandatory, perfect auto isn't possible.
4. **OCR can still read an icon as text**: the classifier must handle overlap, not reject the frame.
5. **LaMa CPU** can be slow on large 4K/texture; needs re-measurement and an engine-selection threshold.
6. **Windows/macOS** still not really built; don't claim support until the CI artifact is verified.
7. **No new dependencies** in Phase 8; if PatchMatch/exemplar is needed, prefer implementing it
   with OpenCV/numpy or using the existing LaMa.

---

## 8. Decisions to settle before coding

| # | Decision | Proposed default |
|---|---|---|
| Q1 | Large containers (box/panel): erase text or the whole block by default? | **Text only**; the user can change it per region |
| Q2 | When an icon is occluded and symmetry confidence is insufficient | **Don't invent**; leave it to the engine/`UNCERTAIN` |
| Q3 | Remove the `flat_fill` snap entirely? | **Yes**, keep only the flagged exact-uniform fallback |
| Q4 | Do split/wipe compare in Phase 8? | **Yes**, P2 priority |
| Q5 | Cache loaded images in the GUI? | **Yes**, LRU 3–5 images |

---

## 9. Conclusion

Phase 8 is not "fixing colors" or "padding the mask". It is a small but correct architectural change:

> **from "one rectangle + one color model for the whole image"**
> to **"frame/container + silhouette + protected object + local background + per-region render"**.

Doing it in the right order R → T → I1 → I2 → I3 → I4 → I5 → U → P → V → B keeps the 77 old tests,
while fixing exactly the 3 user cases, matching preview to the saved version, and clearly improving 4K speed.

---

## 10. Stage R results (already run)

### 10.1. Added files

| File | Content |
|---|---|
| `tests/test_optimization_regressions.py` | 4 core tests: multi-region background, preview==save, frame+icon, container→text |
| `tests/test_optimization_ui.py` | 4 UI tests: refined overlay, failed status, Delete, inline save |
| `scripts/gen_test_images.py` | builders `multi_container`, `preview_parity` |
| `tests/fixtures/multi_container.png` + `.json` | 2 differently colored containers in 1 image |
| `tests/fixtures/preview_parity.png` + `.json` | `VPN` + `KEEP` in the same button |

### 10.2. Run results

```text
$ QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
8 failed, 77 passed
```

| Failing test | Actual reason |
|---|---|
| `test_multi_region_local_backgrounds_are_independent` | blue box restored to the gray card, error **65.7** |
| `test_preview_snapshot_matches_saved_render` | mask off by **1.606 px** |
| `test_frame_with_overlapping_icon_is_not_rejected` | frame rejected, returns `TEXT`, red remains |
| `test_container_is_not_classified_as_button_by_default` | blue box returned as `ELEMENT` |
| `test_mask_overlay_uses_refined_mask` | overlay off from refined mask by **1.119 px** |
| `test_failed_analysis_updates_list_item` | item is still `btn_filled.png`, no "error" |
| `test_delete_key_removes_selected_file` | Delete does nothing, list still has 2 |
| `test_save_uses_inline_summary_not_modal` | `QMessageBox.information` is still called |

### 10.3. Stage R gate

- [x] 8 new tests red **for the right reason** (not import/fixture errors).
- [x] 77 old tests still green.
- [x] New fixtures deterministic, with JSON ground truth.
- [x] No core/UI logic changed.
- [x] Stage T-lite (P0): icon variants, unrelated red, partial glyph mask.
- [x] Stage I1: per-region render + local background.
- [x] Stage I2: frame guard + container → TEXT.
- [x] Stage I3: protected icon in all positions/colors + red connectivity.
- [x] Stage I4: preview/save parity + refined overlay.
- [x] UI fixes: Delete, failed status, inline save.
- [x] P1: ROI engine, `_red_annotation` scope, `mask_bbox`, OCR single-view.
- [x] I5: PCA/mirror residual symmetry (`tests/test_symmetry_i5.py`).
- [x] P2: split/wipe via right mouse button, drag-drop, per-region policy.
- [ ] P2 remaining: thumbnail/status badge, clipboard, preview cache.


---

## 11. Phase 8 implementation results (updated 2026-11-26)

| Item | Status | Evidence |
|---|---|---|
| Stage R + T-lite | **Done** | 15 new tests; fixtures `multi_container`, `preview_parity`, `protected_icon_variants`, `unrelated_red_badge` |
| I1 per-region render | **Done** | multi-container correct for both the gray card and the blue box |
| I2 frame/container | **Done** | icon OCR "G" no longer rejects the button; blue box defaults to `TEXT` |
| I3 protected objects | **Done** | icon left/right/above/light/colored; unrelated red badge kept |
| I4 preview parity | **Done** | preview mask == saved mask; `KEEP` bit-identical |
| UI fixes | **Done** | Delete/context menu, failed status, no modal on save |
| P1 speed | **Done** | 4K analyze ~3.55 s; render ~0.30–0.45 s; 92 tests |
| I5 symmetry | **Done in §12** | PCA + mirror residual (`tests/test_symmetry_i5.py`) |
| P2 UI | **Partial in §12** | wipe/drag-drop/per-region policy done; thumbnail/clipboard/cache remain |

### 11.1. Final verification

```text
$ QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
92 passed
```

- 7 old fixtures: 7/7 `invariant=True`.
- `image_vpn.png`: `No SIM`/time/pin bit-identical; alpha preserved.
- Case 1 gray card: `(98,94,92)`; Case 2 blue box: `(235,99,37)`; Case 3: icon pristine,
  red 0 px.


---

## 12. Phase 9 results – evidence + P2/I5 (updated 2026-11-26)

| Item | Status | Evidence |
|---|---|---|
| Evidence e1 | **Done** | auto: red 2050→0, card background error 0; text-only: red kept, `Add`/`Configuration...` bit-identical |
| Evidence e2 | **Done** | auto: blue button 503→0 blue, row background error 0; text-only: 541 blue remain, icon background mean 0.01/max 3 |
| Per-region policy | **Done** | `Region.mask_mode` + "Erase mode" column; CLI `--element-policy` |
| Exact flat background | **Done** | `_flat_word_fill` + `_glyph_mask_from_fill` + `region_text_fill` |
| Context-aware red annotation | **Done** | frame row not cut; e2 `VPN` label still `TEXT` |
| I5 symmetry | **Done (best-effort)** | PCA + residual; asymmetric icon not mirrored |
| P2 UI | **Partial** | wipe compare (right mouse button), drag-drop, per-region policy |
| Test | **126 passed** | added 9 evidence + 2 symmetry + 2 GUI policy/margin + 21 GUI |
| Linux bundle | **Rebuild OK** | `imagecleaner-selftest` → `SELFTEST OK` |
| No anti-alias fringe left (e3) | **Done §12.1** | 2x Lanczos/Bicubic/Nearest: title max 5, row max 3–7; red frame bit-identical |

Reproduction commands:

```text
$ .venv/bin/python -m src.cli --input evidence/e1.png --output evidence/results/auto     --keywords "VPN,Upgrade now,Internal Use Only,Confidential" --element-policy auto
$ .venv/bin/python -m src.cli --input evidence/e2.png --output evidence/results/auto     --keywords "VPN,Upgrade now,Internal Use Only,Confidential" --element-policy auto
$ ... --element-policy text_only   # for the remaining 2 cases
```


### 12.1. Phase 9.1 – no text fringe left (e3)

- The ink mask is **grown into the anti-alias halo** (`d_fill > 3`, max 2–6 px),
  excluding red/red anti-alias pixels so it doesn't eat the annotation border.
- Glyph crop widened by text height: `dx = 0.28*text_h`, `dy = 0.08*text_h`.
- Dedupe duplicate lines with the same text: IoU **0.30** instead of 0.50.
- Measured on e2 at 2x zoom: title max error 5 / 0 / 0; row label 3 / 4 / 7
  (Lanczos / Bicubic / Nearest), red frame + `Not Connected` bit-identical.


### 12.2. Phase 10 – per-region margin + e4

- `Region.margin_px` + **Padding** column (`-12..+12 px`) in the GUI; applied to mask/render.
- Weak `snap` candidate (< 0.75) → **TEXT**, no more `UNCERTAIN` being skipped; e4 erases all
  5 `VPN` occurrences.
- Save by extension: `.png` always lossless PNG (e4 original was WebP bytes).
- e4: "Proton VPN" highlight background offset mean 1.02/max 7; `Proton`, `Speak`, `Otherwise`,
  `End If`, `Connected` chip, `Search Actions` bit-identical.

### 12.3. Phase 11 – UI polish

- Transparent `QLabel` + left/right padding; grid and image list panel have margins;
  status bar has left padding.
- Placeholder uses `QPalette.PlaceholderText = #6f7789` (`dim_placeholder`,
  `apply_theme`), clearly separated from typed text.
- 3 new GUI tests; total **108 passed**.

### 12.4. Phase 12 – placeholder on focus + arrows

- `HintPlainTextEdit`: hides the placeholder on focus, shows it again when empty + focus lost.
- `ArrowComboBox` / `ArrowSpinBox` draw the chevron themselves with `QPainter`, no asset needed;
  QSS reserves space for the drop-down and up/down buttons.
- 4 new GUI tests (placeholder, chevron pixels, (i) badge presence/hover); total **112 passed**.

### 12.5. Phase 13 – (i) badge + tooltips for labels

- `HintLabel`: label + **(i)** badge; hovering/clicking the badge shows the tooltip, the label text has
  a native tooltip.
- Applied to Keywords, Output folder, Keyword preset, Engine, Erase region, Matching,
  Padding.
- 2 new GUI tests; total **112 passed**.

### 12.6. Phase 14 – English terminology, info icon, label spacing

- Keep English: Offline, Padding, Element, Text; policy
  "Auto (text/element)" / "Text only"; per-region "Text only" / "Element".
- `(i)` badge → `SP_MessageBoxInformation` icon 16 px, still hovering/clicking the tooltip.
- `HintLabel` drops stretch, `sizePolicy=Maximum`; labels right-aligned next to the control.
- 1 new GUI test (label spacing/alignment); total **113 passed**.

### 12.7. Phase 15 – gray icon, tooltip only on the icon, form left-aligned

- Info icon drawn with QPainter in gray `#9a9a9a` (16×16), not Qt's colored icon.
- Label text alone has no tooltip; the tooltip is attached only to the icon. Only Keywords, Engine,
  Padding still have a hint.
- Stretch the right column (`setColumnStretch(4, 1)`) + left-align buttons/checkboxes so the form sits
  near the left edge; controls start at ~x=134 instead of ~x=257.

### 12.8. Phase 16 – QtAwesome + responsive advanced layout

- Add `qtawesome` (gui extra), `src/ui/icons.py` pixmap cache; replace every hand-drawn shape
  (info, combo/spin chevron) with Font Awesome icons; attach icons to the main buttons.
- Bundle spec `collect_data_files("qtawesome")` + hidden imports `qtawesome`, `qtpy`.
- Advanced options: flexible HBox rows; long single-line path across the full width; two left/right
  columns; spacing 10/24 px; combo max 420 px.
- 1 new GUI test (icon library) + layout test updates; total **114 passed**.

### 12.9. Phase 17 – clear actions + needs-review region badge

- `Image folder…` + tooltip to separate it from the output folder.
- Original/Result segmented control with an accent-colored active state; automatically opens Result after analysis
  and when selecting an analyzed image; preview render has a path/mode guard.
- Orange hint on `task_label` when images/keywords are missing.
- Yellow "Needs review" badge in the Kind column + "Show only needs-review regions" checkbox.
- 4 new GUI tests; total **118 passed**.

### 12.10. Phase 18 – zoom bar + splitter spacing

- Fit / − / + / 100% buttons next to the percentage label; QtAwesome icons; disabled when there's no image.
- Work panel has a 6 px margin; preview/regions have a 6 px wrapper margin so they don't stick to the splitter.
- 2 new GUI tests; updated secondary control budget; total **120 passed**.

### 12.11. Phase 19 – icon next to label, no flicker on image change

- `HintLabel`: label `Maximum` + stretch after the icon → icon 4 px from the text.
- `_show_entry` loads the new image before switching modes; `_auto_show_result` returns a bool and
  re-renders in place when Result is on, without toggling Original.
- 2 new GUI tests; total **122 passed**.

### 12.12. Phase 20 – silent background preview render

- Extract `_preview_worker`; `quiet=True` doesn't go through `_set_busy`, so it doesn't disable
  buttons, doesn't show progress/cancel, and doesn't change the task label.
- Analysis/save stops the running preview; closing the app waits for the preview to stop.
- The preview job returns `None` on cancel; guard on the Result button path/state.
- 2 new GUI tests; total **124 passed**.

### 12.13. Phase 21 – stable Result button label

- Don't change the Result button text when rendering the preview; don't set it again after render.
- Preview errors are based on the checked state, not on the text.
- 1 new GUI test; total **125 passed**.

### 12.14. Phase 22 – disable Result when no image is selected

- `_update_view_buttons()` enables/disables the Result button based on entry + loaded image + busy.
- Called on init, `_show_entry`, end of busy; Original is always enabled.
- 1 new GUI test; total **126 passed**.

### 12.15. Phase 23 – project name ImageCleaner

- The old name was easily mistaken for screen cleaning; rename metadata, entry point, spec, bundle,
  CI artifact, app title, logger, config dir.
- Use `IC_CONFIG_DIR` / `IC_MODEL_DIR`; keep no alias or migration for the old name.
- Check env/path directly; total **126 passed**.

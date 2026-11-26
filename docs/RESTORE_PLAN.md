# Thorough Fix Plan for Background Restoration (Phase 7)

> Status: **implemented and signed off — see docs/STATUS.md section 13**. Original proposal below. Root causes were
> reproduced by 3 sub-agents using real commands; figures in `docs/STATUS.md` section 13.
>
> **Phase 8 update (2026-11-26)**: per-region rendering, frame/container classifier,
> protected icons at every position/color, red connectivity, preview = saved, P1 speed.
> See `docs/STATUS.md` sections 14–15 and `docs/OPTIMIZATION_PLAN.md`.

## 1. Actual root causes

### Case 1 & 2 — incorrect reference background
- `src/core/background_restore.py:101-120`: `estimate_background()` computes a **global colour prior**
  over the whole image. In images where the black/white outer occupies 51% → the prior is imposed on the annulus.
- The flat branch **only considers clusters within ±12 of the prior** (`:111`), so the card's gray cluster
  (Δ128) and the box's blue cluster (Δ225) are rejected, even though they occupy 59%/77% of the annulus and cover 4 edges.
- `restore_masked()` (`:243-248`) snaps the entire mask to the wrong prior color → the engine cannot recover it.
- `sanitize_guide()` (`:226`) falls back to the **whole-image median**, and `contaminated_band()` (`:197`)
  marks **every saturated pixel** → the blue container is treated as contamination and neutral becomes white.

Measured:
```
gray card inside black outer : model = flat (0,0,0),  restored (0,0,0),  truth (128,128,128)
text on blue box             : model = flat (255,255,255), restored white, truth (220,90,30)
```

### Case 3 — mask erases the protected object as well
- `ui_element_detector.py:239` returns **a single boundingRect** for the merged contour (button + icon + annotation);
  `_swallows_other_text` (`:526-545`) only guards text that has an OCR box → icons have no OCR box, so they slip through.
- `pipeline.py:117-121` takes `cand.bbox` as `Region.bbox` unchanged.
- `mask_generator.py:84` `full[y:y+h, x:x+w] = 255` → **rectangular mask**, every icon pixel inside the
  bbox is marked for erasure; padding contributes only ≤43 px and is not the main cause.
- `image_utils.py:153` composites with a hard replacement; `background_restore.py:245-248` snaps, so it is lost permanently.
- `contaminated_band` also wipes 317 icon pixels from the guide → they cannot be reconstructed from structure.

## 2. Approach (without touching Detection)

### A. Localized background model
Replace the global prior with an **inner-ring anchor**:
1. `anchor = quantized_mode(ring(mask, r=3))` — this is the color immediately adjacent to the object (card/box).
2. On the annulus r=10..20, keep pixels with `max|pixel-anchor| <= 32` (2× the old tol, smaller than the
   outer↔container distance 128/225). Require support ≥0.15 and coverage of ≥3 edges.
3. Fit a plane on that set, trim residual ≤6; slope ≤2/100px → `flat`, else `plane`, else `none`.
4. Remove the global prior and global-median fallback entirely.

`sanitize_guide()`:
- Remove the saturation rule (a blue container is not contamination).
- For `flat/plane`: contamination = band pixels deviating from the model by >30; the temporary mask is filled with the **local model prediction**.
- For `none`: dark fringe only (`gray < medianBlur(gray,31)-25`), neutral = the non-dark band median,
  **no whole-image median**.

### B. Mask refinement after Detection, before Inpainting
New module `src/core/mask_refinement.py`, called in `Pipeline.render` (`pipeline.py:303-313`):
1. **Target silhouette**: from the OCR match box, take the color/fill component containing the text plus the related
   thin border frame; add the nearby red component (annotation). The mask is a silhouette, not a rectangle.
2. **Protected subtraction**: subtract OCR word boxes that do not belong to the match; subtract the compact dark component
   (icon) that has holes/circular shapes inside the bbox. Red annotation on the icon remains in the mask (it is the target).
3. **Arbitration**: red > button border > protected icon/text > ordinary dark.
4. **Reconstruction plan**: for circular icons, take the remaining visible dark pixels mirrored across the center to fill
   the part covered by the button; apply after compositing. Do not invent pixels for regions with no information.
5. Pass the cleaned guide to the engine via `engine.inpaint(image, mask, guide=...)` so the engine
   does not continue wiping the protected icon.

### C. Tests (written first)
`tests/test_background_restore_regressions.py` + 3 PNG/JSON fixtures:
- `gray_card_black_page`: gray card inside black outer → restored = gray, outer bit-identical.
- `blue_box_white_page`: black text on blue box → restored = blue.
- `vpn_button_icon_annotation`: button + red line over the icon → icon pristine bit-identical,
  red = 0, blue = 0, the covered part of the icon is reconstructed from symmetry.

Gate: new tests red → implement A → cases 1&2 green → implement B → case 3 green →
full suite + 7 fixture invariants + CLI `--debug` → 3 comparison images original/overlay/restored.

## 3. Known limitations
- Icon pixels lying entirely under an opaque button do not exist in the original image; they can only be approximated
  from symmetry/structure, not bit-exact. This will be stated explicitly in the report.
- Do not expand the mask arbitrarily; every mask change has evidence (component/OCR box/gradient).
- Still offline; no new dependencies.

> **Phase 9 update (2026-11-26)**: sign-off on `evidence/e1–e2`, per-region policy, wipe compare, drag-drop and I5 symmetry. See `docs/STATUS.md` section 16.

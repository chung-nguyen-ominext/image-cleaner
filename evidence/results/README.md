# `evidence/` acceptance results

The original images are never modified. Each folder is one CLI run:

```bash
.venv/bin/python -m src.cli --input evidence/e1.png --output evidence/results/auto \
  --keywords "VPN,Upgrade now,Internal Use Only,Confidential" --element-policy auto
.venv/bin/python -m src.cli --input evidence/e2.png --output evidence/results/auto \
  --keywords "VPN,Upgrade now,Internal Use Only,Confidential" --element-policy auto
# same with --element-policy text_only
```

| Image | Folder | Expected | Measured result |
|---|---|---|---|
| `e1.png` | `auto/` | Erase the red border + `Add VPN Configuration...` inside | red **2050 → 0 px**; card background `(25,25,23)` error **0.0** |
| `e1.png` | `text_only/` | Erase only the 3 glyphs `VPN`; keep the red border, `Add`, `Configuration...` | red **2050 → 2050 px**; words/status/Back **bit-identical**; `VPN` region card background (max error 0) |
| `e2.png` | `auto/` | Erase the whole blue `VPN` button; erase the `VPN` title + row label | blue **503 → 0 px**; row background error **0.0**; red frame + `Not Connected` bit-identical |
| `e2.png` | `text_only/` | Erase only the white text, keep the blue button; erase standalone `VPN` | blue **503 → 541 px**; background inside the icon mean **0.01**, max 3; red frame intact |

Ground truth: `evidence/e1.json`, `evidence/e2.json`.
Automated test: `tests/test_evidence_acceptance.py`.


## e3 – no anti-alias fringe

`e3.png` is an old output image that still had a grey fringe around the text. The fix was
verified by scaling `e2.png` 2x (Lanczos/Bicubic/Nearest) and running again: title `VPN`
background deviation at most **5/0/0**, row label **3/4/7**, red frame and `Not Connected`
bit-identical.
Test: `test_scaled_antialiased_text_leaves_no_outline`.


## e4 – weak snap + range adjustment

```bash
.venv/bin/python -m src.cli --input evidence/e4.png --output evidence/results   --keywords VPN
```

- Candidate `snap` conf **0.631** (the “Proton VPN” region) is now **TEXT** and is erased;
  no `UNCERTAIN` region is skipped anymore.
- 5/5 `VPN` occurrences are erased; the highlight background around “Proton VPN” deviates
  mean 1.02 / max 7.
- `Proton`, `Speak`, `Otherwise`, `End If`, chip `Connected`, `Search Actions`
  bit-identical.
- The saved `.png` is lossless PNG (the original e4 file was WebP bytes).
- The GUI has a **Padding** column (`-12..+12 px`) to expand/shrink the selected region's mask.

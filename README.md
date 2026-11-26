# ImageCleaner

**Offline** desktop tool that finds text/UI elements by user-entered keywords in screenshots,
then erases them with inpainting. No image uploads, no cloud calls, runs on CPU.

> Status: **Phase 23 – per-region policy + margin, `e1–e4` acceptance, UI polish; 127 tests passed**.
> Linux onedir bundle rebuilt in Phase 9 and `SELFTEST OK`.
> Windows/macOS built with native CI workflows (not run on this machine).
> See `docs/STATUS.md` (section 16), `docs/OPTIMIZATION_PLAN.md` and `docs/REFACTOR_PLAN.md`.

## Install from source

- Python 3.11+
- `models/lama/lama_fp32.onnx` (~199 MB, only needed for high-quality inpainting)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,gui]"      # core + pytest + PySide6 + QtAwesome
.venv/bin/python scripts/fetch_models.py   # download LaMa once (at build time)
.venv/bin/python -m src.ui.app             # run the GUI
```

You can install the pinned build/dev environment with:

```bash
.venv/bin/pip install -r requirements.txt
```

Without the LaMa model, the program still runs with Telea/NS (lower quality on complex backgrounds).
The model directory can be overridden with the `IC_MODEL_DIR` environment variable.

## GUI

One screen, only 8 visible controls:

1. **Choose images…** / **Folder…** → image list; `Delete` or right-click to remove an image from the list.
   Dragging images/folders into the window also works.
2. Enter keywords (one per line, or separated by `,`) → **Analyze**.
3. Region table: tick the "Erase" box to enable each region; **Needs review** regions are off by default
   and are only erased after you mark them yourself. The **Erase mode** column lets you choose
   **Auto / Text only / Whole block** for each region. The **Padding** column lets you **expand (+)** or
   **shrink (-)** the mask scope of the selected region by pixels (`-12..+12`). Preview has 2 modes:
   **Original | Result**; in Result mode, **drag with the right mouse button** to compare the wipe with the original image.
4. **Save images** (all; `Ctrl+S` saves the selected image) + `report.csv`/`report.json`.
   Never overwrites original images.

Advanced options (engine, padding, matching, preset, output folder, log) live in the
**Advanced options** button, collapsed by default. Validation uses disabled buttons + an inline banner,
not constant dialogs. Labels have left/right padding; the keyword placeholder uses a muted color
and hides on focus so it isn't confused with the text being typed. The **Keywords / Engine / Padding** labels have a **gray info icon**: only hovering/clicking
the icon shows the explanation. Dropdowns and spinboxes have clear arrow indicators.

Shortcuts: `Ctrl+O` add images, `Ctrl+R` analyze, `Ctrl+S` save, `Ctrl+F` fit to window.

### Keyword matching semantics (D7)
- Case-insensitive, extra whitespace is ignored; every occurrence is found.
- Keywords ≥5 characters can be inside a word (`Confidential` in `Confidentiality`) but
  only those exact glyphs are erased thanks to column projection on pixels.
- Keywords <5 characters only match the **whole token** or a **suffix** of the token:
  `zza` in `abcxyzzza` ✅, `VPN` in `VPNX` ❌.
- When no reliable character box can be extracted → **Needs review** region, not erased automatically.

### Acceptance samples `evidence/`

```text
evidence/e1.png   auto       → erases the whole red border + the content inside
evidence/e1.png   text_only  → erases only the VPN glyphs, keeps the red border + Add / Configuration...
evidence/e2.png   auto       → erases the whole blue VPN button
evidence/e2.png   text_only  → erases only the white text, keeps the blue box
evidence/results/{auto,text_only}/   # generated result images
```

All 4 cases check: pixels around the target are bit-identical, the background after erasing matches
the un-erased background color (zero error in flat regions), no blur. Test: `tests/test_evidence_acceptance.py`.

`evidence/e4.png` (keyword `VPN`): the `snap` candidate with conf 0.631 is now handled as **TEXT**
instead of `UNCERTAIN`; all 5 `VPN` occurrences are erased, `Proton`/`Speak`/`Otherwise`/`End If`/
the `Connected` chip/`Search Actions` bit-identical. The saved `.png` file is always lossless PNG.

Heavily zoomed/anti-aliased images (`evidence/e3.png`) used to leave a gray halo around text.
Glyph masks now automatically extend into the anti-alias halo (2–6 px, not into the red border), the crop
is widened by text height, and duplicate lines are deduped; 2x Lanczos/Bicubic/Nearest tests
no longer have pixels off the background by > 7.

## CLI

```bash
.venv/bin/python -m src.cli \
  --input screenshot.png --output out/ \
  --keywords "VPN, Internal Use Only"

# Whole folder (batch)
.venv/bin/python -m src.cli \
  --input screenshots/ --output out/ --keywords "Confidential" --recursive

# Analyze only, don't write images
.venv/bin/python -m src.cli --input a.png --output out/ --keywords "VPN" --dry-run
```

Main options: `--engine auto|telea|ns|lama`, `--element-policy auto|text_only`,
`--conf 0.6`, `--match-mode exact|fuzzy`, `--padding 2`, `--output-format same|png|jpg`,
`--debug` (writes overlay + mask).

## Using as a library

```python
from src.config.settings import ProcessOptions
from src.core.pipeline import Pipeline

options = ProcessOptions(keywords=["VPN", "Confidential"])
pipe = Pipeline(options)

result = pipe.analyze("shot.png")          # OCR + classification, does not modify the image
for region in result.regions:
    print(region.kind, region.bbox, region.confidence)

report = pipe.process("shot.png", "out/")  # analyze + render + write file
print(report.status, report.output_path)
```

`analyze()` is separate from `render()`; `write_result()` is the common write path for CLI and GUI
(accepts the list of regions already reviewed by the user).

## Packaging & installation

PyInstaller **does not cross-compile**: each OS must be built on that exact OS
(real machine, VM, or CI runner). On the machine currently running:

```bash
pip install -e ".[dev,gui]" pyinstaller
python scripts/fetch_models.py
python scripts/build_release.py
```

The script builds with `imagecleaner.spec`, runs `imagecleaner-selftest` inside the bundle
(no Python required), then creates the installers:

| OS | File to distribute | What the user does |
| --- | --- | --- |
| Windows x64 | `dist/ImageCleaner-Setup-0.1.0-x64.exe` | Download → run → Next → open from the Start Menu |
| Windows (portable) | `dist/ImageCleaner-0.1.0-windows-x64.zip` | Unzip → run `ImageCleaner.exe` |
| macOS Apple Silicon | `dist/ImageCleaner-0.1.0-macos-arm64.dmg` | Open the DMG → drag into Applications |
| macOS Intel | `dist/ImageCleaner-0.1.0-macos-x64.dmg` | Open the DMG → drag into Applications |
| Linux x64 | `dist/ImageCleaner-0.1.0-linux-x64.deb` | `sudo apt install ./ImageCleaner-0.1.0-linux-x64.deb` |
| Linux (portable) | `dist/ImageCleaner-0.1.0-linux-x64.tar.gz` | Unzip → run `ImageCleaner` |

The Windows installer uses Inno Setup (available on the `windows-2022` runner). macOS creates a
standard `.app` then packages it into a `.dmg`. Linux creates a `.deb` with a menu shortcut and
`/usr/bin/imagecleaner`.

The packages are **not code-signed/notarized**, so the first run may show a warning:
Windows SmartScreen → **More info → Run anyway**; macOS Gatekeeper → right-click
the app → **Open** (or `xattr -dr com.apple.quarantine /Applications/ImageCleaner.app`).

`selftest` is a verification **inside the bundle, with no Python required**: OCR with the bundled
model, prints the recognized text, and confirms the LaMa model path.

### Build all 3 OSes with GitHub Actions

The `.github/workflows/build.yml` workflow builds 4 packages on native runners:
`ubuntu-22.04`, `windows-2022`, `macos-15` (arm64), `macos-15-intel` (x64).
Push a `v*` tag to build and automatically create a GitHub Release with the installer files; or run
**Actions → build → Run workflow** then download the artifact (kept for 14 days).

macOS Intel uses `onnxruntime==1.23.0` because 1.24+ versions have dropped the macOS x86_64 wheel.
**Only the Linux artifact is built/tested on this machine**; Windows/macOS need CI
or a native build.

### GitLab CI

The repo also has `.gitlab-ci.yml`:

- **Linux + Windows**: use GitLab.com hosted runners; the **Free tier** is enough.
- **macOS Apple Silicon**: the hosted macOS runner (beta) is only available on the **Premium/Ultimate** plan,
  so the `macos-arm64` job is set to manual; click Play if your account has permission.
- GitLab.com **has no macOS Intel**; you need GitHub Actions or a real Intel machine.

Run: **CI/CD → Pipelines → Run pipeline**, or push a `v*` tag. Artifacts are in
each job (`linux-x64`, `windows-x64`, `macos-arm64`).

## Background restoration with real inpainting

- **Per-region rendering**: each region has its own mask + background model + composite.
  No more single union mask where the gray card and blue box fight over one background color.
- **Per-region policy**: `Auto / Text only / Whole block` (GUI) or `--element-policy`.
  "Text only" erases only the keyword glyphs; "Whole block" erases the entire UI element.
- **Exact flat background**: glyphs on flat cards/icons are filled with the correct background color
  (mode of the ring around the text, ignoring red annotation pixels), independent of the engine.
  Gradients/textures are still left to Telea/LaMa to reconstruct.
- **Contextual red annotation**: a red border enclosing the target is erased along with the target;
  a red frame enclosing a whole row is not cut and does not turn text labels into `ELEMENT`.
- **Frame/container**: large colored panels, square corners, no border → by default only erase text (`TEXT`);
  rounded/bordered buttons → erase the whole block (`ELEMENT`). Icons misread by OCR as text no longer
  cause a correct frame to be rejected.
- **Object protection**: icons are protected in every position (left/right/top), every color
  (dark/light/colored). Red annotation is only erased when connected to the target; unrelated red badges
  are kept as is. Visible pixels of icons are **bit-identical**.
- **Partial text**: localized substrings erase the exact glyph pixels within the char-box union,
  without painting the whole rectangle; gaps between glyphs are preserved.
- **Preview = Saved**: preview uses the exact same `lines` + refined mask as the saved output.
- Always run the inpainting engine (no color fill/cropping):
  - flat/gradient background → Telea cleans up the source region (fast);
  - texture/photo background → **LaMa ONNX** reconstructs missing details.
- Pixels adjacent to the mask that are contaminated by the red border/black text are cleaned **before being passed into the engine**,
  without expanding the mask and without changing the original image outside the erase scope.
- **Speed (P1)**: the engine only runs on the ROI around the mask; `mask_bbox` uses `findNonZero`;
  single-view 4K OCR (`ocr_max_side` defaults to 4096, engine cap 2560).
  Measured on `big_4k`: analyze ~3.55 s, render ~0.30–0.45 s for 7 regions.
- Verification: **127 tests**, including `tests/test_background_restore_regressions.py`,
  `tests/test_optimization_regressions.py`, `tests/test_optimization_ui.py`,
  `tests/test_evidence_acceptance.py`, `tests/test_symmetry_i5.py`,
  `tests/test_inpainting_color.py`. Fixtures `gray_card_black_page`, `blue_box_white_page`,
  `vpn_button_icon_annotation`, `multi_container`, `protected_icon_variants`,
  `unrelated_red_badge`, `preview_parity`. Keep the engine at `auto` (default).

## Safety behavior

- Never overwrite original images; output names auto-increment (`_1`, `_2`, …).
- Preserve image dimensions and the original format (JPEG q=95, PNG lossless).
- Pixels outside the mask **remain absolutely unchanged** (there is an invariance test).
- Uncertain regions (UNCERTAIN) **are not erased**; batch writes `skipped_uncertain`.
- Failed images don't stop the batch; errors are written to `report.csv` / `report.json`.

## Test

```bash
.venv/bin/python -m pytest -q
```

GUI tests are skipped automatically when PySide6 isn't installed; run offscreen (`QT_QPA_PLATFORM=offscreen`).
Regression tests with real screenshots are optional: set `IC_REAL_SCREENSHOT=/path/image.png`
to run them; without the file, tests skip automatically (CI stays green).

## Structure

```
src/core/       models, pipeline, matcher, element detector, mask, batch
src/engines/    OCR (RapidOCR) + inpainting (LaMa / OpenCV)
src/ui/         PySide6 GUI + preview canvas + background workers
src/config/     options, cross-platform paths
src/utils/      image IO, logging
tests/          matcher, mask safety, pipeline e2e, GUI logic
scripts/        generate test images, download models, build + package release
packaging/      icon + Inno Setup script for Windows
imagecleaner.spec   PyInstaller onedir (+ .app on macOS)
docs/           design + status reports
```

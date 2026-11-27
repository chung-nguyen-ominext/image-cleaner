# ImageCleaner

**Offline** desktop tool that finds text/UI elements by user-entered keywords in screenshots,
then erases them with inpainting. Runs on CPU.

## Download & install

Current release: **0.2.2** — see [CHANGELOG.md](CHANGELOG.md). Download from the
[Releases page](https://github.com/chung-nguyen-ominext/image-cleaner/releases);
file names use `<version>` for the release number.

| OS                  | Download                                  | Install                                                   |
| ------------------- | ----------------------------------------- | --------------------------------------------------------- |
| Windows x64         | `ImageCleaner-Setup-<version>-x64.exe`    | Run → Next → open from the Start Menu                     |
| Windows (portable)  | `ImageCleaner-<version>-windows-x64.zip`  | Unzip → run `ImageCleaner.exe`                            |
| macOS Apple Silicon | `ImageCleaner-<version>-macos-arm64.dmg`  | Open the DMG → drag into Applications                     |
| macOS Intel         | `ImageCleaner-<version>-macos-x64.dmg`    | Open the DMG → drag into Applications                     |
| Linux x64           | `ImageCleaner-<version>-linux-x64.deb`    | `sudo apt install ./ImageCleaner-<version>-linux-x64.deb` |
| Linux (portable)    | `ImageCleaner-<version>-linux-x64.tar.gz` | Unzip → run `ImageCleaner`                                |

All installers bundle the OCR and LaMa models; no model download or Python install is required.

Unsigned builds may warn on first run:

- **Windows**: **More info → Run anyway**
- **macOS 15+**: **System Settings → Privacy & Security → Open Anyway**
- **Older macOS**: right-click → **Open**

Or run `xattr -dr com.apple.quarantine /Applications/ImageCleaner.app`.

## Privacy & review

Everything runs on your computer; the app makes no network calls. OCR can miss text
(small, blurry or low-contrast), so **always check the saved images before sharing them**.

EXIF orientation is applied automatically; JPEG EXIF, ICC and DPI metadata are preserved.

## GUI

Toolbar: **Add images** / **Choose folder**, the keyword chip field, **Advanced** and the
**Analyze N images** split button (Analyze all images / Analyze this image only).

1. Add images or a folder; dragging files/folders into the window also works.
   `Delete` or right-click removes an image from the list.
2. Type a keyword and press Enter (or comma) to add it as a chip; `×` removes it.
3. **Analyze** shows the result immediately. Images, preview and region cards stay in sync;
   switching images only swaps the picture, keeping the Original/Result mode.
4. Region cards: tick a region to erase it, expand it to choose **Erase mode**
   (Blend background / Solid fill / Blur) and **Padding** (`-12..+12` px). **Needs review**
   regions stay off until you tick them.
5. **This image** / **Save all (N)** sit in the regions footer; `Ctrl+S` saves all.
   Use the trash button in the **Images** header to clear the list after saving.
   Errors are shown in **View log**; originals are never overwritten.

Side panels collapse to rails with their arrow buttons or `Ctrl+B`; panel widths are remembered.
**Compare** (or right-drag) wipes between the original and the result.

Advanced options (engine, padding, matching, preset, output folder, log) live in the
**Advanced** dialog.

Shortcuts: `Ctrl+O` add, `Ctrl+Enter` analyze, `Ctrl+S` save all, `Ctrl+B` panels, `Ctrl+0` fit,
`Ctrl+1` 1:1. On macOS use `⌘` or `Ctrl`.

### Keyword matching

- Case-insensitive; every occurrence is found.
- Keywords ≥5 characters can match inside a word, but only the matching characters are erased.
- Keywords <5 characters match a whole token or a suffix:
  `zza` in `abcxyzzza` ✅, `VPN` in `VPNX` ❌.
- If no reliable character box is found, the region becomes **Needs review** and is not erased.

## Safety behavior

- Originals are never overwritten; output names auto-increment (`_1`, `_2`, …).
- Dimensions and format are preserved.
- Saves are lossless when the format allows it: unchanged images are copied as-is, indexed PNG
  stays indexed when possible, PNG is optimized, and `.webp` output is lossless.
- JPEG stays lossy (q=95). If the saved file is larger than the source, the log explains why.
- Pixels outside the mask remain unchanged.
- Uncertain regions are skipped; GUI errors appear in **View log**.
- CLI batch errors are written to `report.csv` / `report.json` and do not stop the batch.

## CLI

Requires a source install (see [Install from source](#install-from-source)).

```bash
python -m src.cli --input screenshot.png --output out/ --keywords "VPN, Internal Use Only"

# Whole folder (batch)
python -m src.cli --input screenshots/ --output out/ --keywords "Confidential" --recursive

# Analyze only, don't write images
python -m src.cli --input a.png --output out/ --keywords "VPN" --dry-run
```

Options: `--engine auto|telea|ns|lama`, `--element-policy auto|text_only`, `--conf 0.6`,
`--match-mode exact|fuzzy`, `--padding 2`, `--output-format same|png|jpg`, `--debug`.

Batch runs write `report.csv` / `report.json` to the output folder.

## Development

### Install from source

- Python 3.11+

Create and activate the virtual environment once:

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate

# Windows
python -m venv .venv
.venv\Scripts\activate
```

Then:

```bash
pip install -e ".[dev,gui]"
python scripts/fetch_models.py
python -m src.ui.app
```

When running from source, `scripts/fetch_models.py` downloads LaMa once.
Without LaMa, the app still runs with Telea/NS (lower quality); set `IC_MODEL_DIR` to use another model directory.

Pinned build/dev environment:

```bash
pip install -r requirements.txt
```

### Using as a library

```python
from src.config.settings import ProcessOptions
from src.core.pipeline import Pipeline

pipe = Pipeline(ProcessOptions(keywords=["VPN", "Confidential"]))
result = pipe.analyze("shot.png")          # detect only
report = pipe.process("shot.png", "out/")  # detect + save
```

`analyze()` is read-only; `process()` writes the result.

### Packaging & build

Build on each target OS with the virtual environment active:

```bash
pip install -e ".[dev,gui]" pyinstaller
python scripts/fetch_models.py
python scripts/build_release.py
```

The script creates the installers in `dist/` and runs `imagecleaner-selftest` on the bundle.

### Test

```bash
python -m pytest -q
```

GUI tests are skipped without PySide6. Set `IC_REAL_SCREENSHOT=/path/image.png` for the optional
real-image regression tests.

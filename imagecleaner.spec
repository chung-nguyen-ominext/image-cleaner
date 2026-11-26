# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build: GUI + offline self-test, with all models bundled.

Build:  python scripts/build_release.py
Output: dist/ImageCleaner-app/  (ImageCleaner + imagecleaner-selftest)
        dist/ImageCleaner.app/  (macOS only, for Finder / DMG)
"""

import importlib
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
icon = ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.png")
rapidocr_pkg = Path(importlib.import_module("rapidocr").__file__).resolve().parent

datas = []
# OCR runtime: config + word-level models shipped in the wheel.
for f in rapidocr_pkg.glob("*.yaml"):
    datas.append((str(f), "rapidocr"))
for f in (rapidocr_pkg / "models").glob("*.onnx"):
    datas.append((str(f), "rapidocr/models"))
# LaMa inpainting model (downloaded once at build time).
lama = ROOT / "models" / "lama" / "lama_fp32.onnx"
if lama.is_file():
    datas.append((str(lama), "models/lama"))
else:
    print("WARNING: models/lama/lama_fp32.onnx missing; LaMa will be unavailable in the build")

# QtAwesome bundles its Font Awesome fonts as package data.
datas += collect_data_files("qtawesome")

hiddenimports = collect_submodules("rapidocr") + [
    "onnxruntime", "cv2", "PIL", "numpy",
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "qtawesome", "qtpy",
]

excludes = [
    "tkinter", "matplotlib", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQuick3D",
]

a = Analysis(
    ["src/ui/app.py", "src/selftest.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

scripts = {s[0]: [s] for s in a.scripts}
gui_exe = EXE(
    pyz, scripts["app"], [],
    name="ImageCleaner",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(icon),
)
selftest_exe = EXE(
    pyz, scripts["selftest"], [],
    name="imagecleaner-selftest",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,
)

coll = COLLECT(
    gui_exe, selftest_exe,
    a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="ImageCleaner-app",
)

# macOS: also produce a real .app bundle for Finder / drag-to-Applications.
# BUNDLE is a no-op on other platforms, but keep it conditional so the spec
# stays explicit about the extra output.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ImageCleaner.app",
        icon=str(icon),
        bundle_identifier="com.imagecleaner.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "ImageCleaner",
            "CFBundleDisplayName": "ImageCleaner",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
        },
    )
    # COLLECT inherits console=True from the last EXE (the selftest), which
    # would make BUNDLE emit LSBackgroundOnly and suppress the normal GUI
    # app behaviour. Force the .app itself to be windowed.
    app.console = False



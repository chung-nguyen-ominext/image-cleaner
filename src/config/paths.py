"""Cross-platform path resolution (source run, PyInstaller onedir/onefile, user config)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ImageCleaner"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory that holds bundled resources (models, assets)."""
    if is_frozen():
        # onefile: sys._MEIPASS ; onedir: directory of executable
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _config_base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def user_config_dir() -> Path:
    override = os.environ.get("IC_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return _config_base() / APP_NAME


def user_log_dir() -> Path:
    d = user_config_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_dir() -> Path:
    """Resolution order: IC_MODEL_DIR -> bundled -> user dir -> ./models."""
    env = os.environ.get("IC_MODEL_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
    candidates = [app_dir() / "models", user_config_dir() / "models", Path.cwd() / "models"]
    for c in candidates:
        if c.is_dir() and any(c.rglob("*.onnx")):
            return c
    return candidates[0]


def model_path(rel: str) -> Path | None:
    p = model_dir() / rel
    return p if p.exists() else None


def settings_path() -> Path:
    return user_config_dir() / "settings.json"


def presets_dir() -> Path:
    d = user_config_dir() / "presets"
    d.mkdir(parents=True, exist_ok=True)
    return d

"""Logging: console + rotating file, with an optional Qt signal sink."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

_FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


class CallbackHandler(logging.Handler):
    """Forward log records to a callback (used by the GUI log viewer)."""

    def __init__(self, callback: Callable[[str, str], None]) -> None:
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(record.levelname, self.format(record))
        except Exception:  # noqa: BLE001 - never let logging break the app
            pass


def setup_logger(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger("imagecleaner")
    if getattr(root, "_sc_configured", False):
        return root
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(_FMT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "imagecleaner.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    root._sc_configured = True  # type: ignore[attr-defined]
    return root


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"imagecleaner.{name}" if name else "imagecleaner")


def add_callback_handler(callback: Callable[[str, str], None], level: int = logging.INFO) -> logging.Handler:
    root = logging.getLogger("imagecleaner")
    handler = CallbackHandler(callback)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    handler.setLevel(level)
    root.addHandler(handler)
    return handler

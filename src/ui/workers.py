"""Background workers. All heavy work (OCR, inpainting, disk IO) stays off the UI thread."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

log = logging.getLogger("imagecleaner.ui")


class FunctionWorker(QThread):
    """Run `fn(progress, cancelled)` in a thread and deliver its return value.

    `fn` must be self-contained: it receives no UI objects and may only call
    thread-safe core APIs. Progress is a plain callable emitting a queued signal.
    """

    succeeded = Signal(object)
    failed = Signal(str)
    progressed = Signal(int, int, str)

    def __init__(self, fn: Callable[[Callable[[int, int, str], None], Callable[[], bool]], Any],
                 parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D401 - Qt entry point
        try:
            result = self._fn(self.progressed.emit, self.isInterruptionRequested)
        except Exception:  # noqa: BLE001 - surface the traceback instead of dying silently
            log.exception("background task failed")
            self.failed.emit(traceback.format_exc(limit=4))
        else:
            self.succeeded.emit(result)

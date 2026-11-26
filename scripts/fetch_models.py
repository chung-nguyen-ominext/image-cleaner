"""Download the LaMa ONNX model once (build-time only; not needed at runtime)."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

LAMA_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
DEST = Path(__file__).resolve().parents[1] / "models" / "lama" / "lama_fp32.onnx"
MIN_BYTES = 150 * 1024 * 1024


def main() -> int:
    if DEST.is_file() and DEST.stat().st_size >= MIN_BYTES:
        print(f"LaMa already present: {DEST} ({DEST.stat().st_size / 1e6:.1f} MB)")
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading LaMa model -> {DEST}")

    def progress(count: int, block: int, total: int) -> None:
        if total > 0 and count % 64 == 0:
            print(f"  {min(count * block, total) / 1e6:.1f} / {total / 1e6:.1f} MB", flush=True)

    tmp = DEST.with_suffix(".download")
    urllib.request.urlretrieve(LAMA_URL, tmp, reporthook=progress)
    size = tmp.stat().st_size
    if size < MIN_BYTES:
        tmp.unlink(missing_ok=True)
        print(f"Download looks wrong ({size} bytes)", file=sys.stderr)
        return 1
    tmp.replace(DEST)
    print(f"OK: {DEST} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

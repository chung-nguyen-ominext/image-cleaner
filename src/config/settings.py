"""Processing options and persisted app settings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class ProcessOptions:
    keywords: list[str] = field(default_factory=list)

    # matching
    conf_threshold: float = 0.60
    match_mode: str = "fuzzy"  # exact | fuzzy
    fuzzy_threshold: float = 0.85
    word_boundary: bool = True
    ignore_spaces: bool = False

    # element detection
    element_policy: str = "auto"  # auto | text_only
    element_hi: float = 0.75
    element_lo: float = 0.45

    # mask
    padding: int = 2
    mask_mode: str = "auto"  # auto | text | bbox
    refine_mask: bool = True  # protect unrelated text/icons after detection

    # inpainting
    inpaint_engine: str = "auto"  # auto | telea | ns | lama
    inpaint_radius: int = 3
    flat_fill: bool = True  # exact fill when the surrounding UI is one flat colour

    # output
    output_format: str = "same"  # same | png | jpg
    jpeg_quality: int = 95
    uncertain_action: str = "skip"  # skip | text | element

    # ocr preprocessing
    ocr_max_side: int = 4096
    ocr_min_side: int = 1200
    tile_overlap: int = 128
    ocr_upscale_limit: float = 4.0

    def normalized_keywords(self) -> list[str]:
        return normalize_keyword_list(self.keywords)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessOptions":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def normalize_keyword_list(raw: list[str] | str) -> list[str]:
    """Split on newlines/commas, trim, drop empties, de-duplicate (case-insensitive)."""
    if isinstance(raw, str):
        items = raw.replace("\n", ",").split(",")
    else:
        items = []
        for item in raw:
            items.extend(str(item).replace("\n", ",").split(","))
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        kw = " ".join(item.split()).strip()
        if not kw:
            continue
        key = kw.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(kw)
    return result


@dataclass
class AppSettings:
    last_input_dir: str = ""
    last_output_dir: str = ""
    last_preset: str = ""
    options: ProcessOptions = field(default_factory=ProcessOptions)

    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(
            last_input_dir=data.get("last_input_dir", ""),
            last_output_dir=data.get("last_output_dir", ""),
            last_preset=data.get("last_preset", ""),
            options=ProcessOptions.from_dict(data.get("options", {})),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_input_dir": self.last_input_dir,
            "last_output_dir": self.last_output_dir,
            "last_preset": self.last_preset,
            "options": self.options.to_dict(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def save_keyword_preset(path: Path, keywords: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"keywords": normalize_keyword_list(keywords)}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_keyword_preset(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("keywords", []) if isinstance(data, dict) else data
    return normalize_keyword_list(raw)

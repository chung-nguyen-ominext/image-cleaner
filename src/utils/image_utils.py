"""Image IO and mask helpers (PIL for metadata/unicode paths, OpenCV for math)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass
class LoadedImage:
    path: Path
    bgr: np.ndarray
    alpha: np.ndarray | None = None
    icc: bytes | None = None
    dpi: tuple[float, float] | None = None
    exif: bytes | None = None
    fmt: str = "PNG"
    mode: str = "RGB"
    palette: list[int] | None = None

    @property
    def height(self) -> int:
        return int(self.bgr.shape[0])

    @property
    def width(self) -> int:
        return int(self.bgr.shape[1])

    def ocr_image(self) -> np.ndarray:
        """BGR image with alpha composited over white (OCR-friendly)."""
        if self.alpha is None:
            return self.bgr
        a = (self.alpha.astype(np.float32) / 255.0)[..., None]
        out = self.bgr.astype(np.float32) * a + 255.0 * (1.0 - a)
        return out.clip(0, 255).astype(np.uint8)


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path)
    with Image.open(path) as im:
        fmt = (im.format or path.suffix.lstrip(".") or "PNG").upper()
        icc = im.info.get("icc_profile")
        dpi = im.info.get("dpi")
        exif = im.info.get("exif")
        im = ImageOps.exif_transpose(im)
        mode = im.mode
        palette = im.getpalette() if mode == "P" else None

        alpha: np.ndarray | None = None
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8).copy()
            rgb = np.asarray(rgba.convert("RGB"), dtype=np.uint8)
        else:
            rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return LoadedImage(
        path=path,
        bgr=bgr,
        alpha=alpha,
        icc=icc,
        dpi=dpi,
        exif=exif,
        fmt=fmt,
        mode=mode,
        palette=palette,
    )


def unique_output_path(out_dir: Path, stem: str, suffix: str) -> Path:
    """Never overwrite: first free name wins."""
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = out_dir / f"{stem}{suffix}"
    n = 1
    while candidate.exists():
        candidate = out_dir / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


def _palette_if_lossless(img: Image.Image, palette: list[int]) -> Image.Image:
    """Keep an indexed PNG indexed when every pixel is still in the palette."""
    colors = np.asarray(palette, dtype=np.uint8).reshape(-1, 3)[:256]
    lookup: dict[tuple[int, int, int], int] = {}
    for index, color in enumerate(colors):
        lookup.setdefault(tuple(int(v) for v in color), index)

    pixels = np.asarray(img).reshape(-1, 3)
    unique, inverse = np.unique(pixels, axis=0, return_inverse=True)
    if len(unique) > len(lookup):
        return img

    indices = np.empty(len(unique), dtype=np.uint8)
    for i, color in enumerate(unique):
        index = lookup.get(tuple(int(v) for v in color))
        if index is None:
            return img
        indices[i] = index

    out = Image.fromarray(indices[inverse].reshape(img.height, img.width), mode="P")
    out.putpalette(palette)
    return out


def save_image(loaded: LoadedImage, bgr: np.ndarray, out_path: Path, fmt: str | None = None,
               jpeg_quality: int = 95) -> Path:
    fmt = (fmt or loaded.fmt).upper()
    if fmt in ("JPG", "JPEG"):
        fmt = "JPEG"
    if fmt not in ("PNG", "JPEG", "WEBP", "BMP", "TIFF"):
        fmt = "PNG"

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if loaded.alpha is not None:
        rgba = np.dstack([rgb, loaded.alpha])
        img = Image.fromarray(rgba, mode="RGBA")
    else:
        img = Image.fromarray(rgb, mode="RGB")

    kwargs: dict = {}
    if loaded.dpi:
        kwargs["dpi"] = loaded.dpi
    if loaded.icc:
        kwargs["icc_profile"] = loaded.icc
    if fmt == "JPEG":
        kwargs.update(quality=int(jpeg_quality), subsampling=0, optimize=False, progressive=False)
        if loaded.exif:
            kwargs["exif"] = loaded.exif
    elif fmt == "PNG":
        if loaded.alpha is None and loaded.mode == "P" and loaded.palette:
            img = _palette_if_lossless(img, loaded.palette)
        kwargs.update(compress_level=9, optimize=True)
    elif fmt == "WEBP":
        kwargs.update(lossless=True, quality=100, method=6)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format=fmt, **kwargs)
    return out_path


def feather_mask(mask: np.ndarray, sigma: float = 1.5) -> np.ndarray:
    """Binary uint8 mask -> float32 alpha with soft edges."""
    if mask.dtype != np.uint8:
        mask = (mask > 0).astype(np.uint8) * 255
    blurred = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), sigma)
    return np.clip(blurred, 0.0, 1.0)


def dilate_mask(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    return cv2.dilate(mask, k)


def union_masks(masks: list[np.ndarray], shape: tuple[int, int]) -> np.ndarray:
    out = np.zeros(shape, dtype=np.uint8)
    for m in masks:
        if m is not None and m.shape == shape:
            out = cv2.bitwise_or(out, m)
    return out


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    # np.where on a 4K mask costs ~25 ms; findNonZero+boundingRect is ~0.6 ms.
    if mask.dtype != np.uint8:
        mask = (mask > 0).astype(np.uint8)
    points = cv2.findNonZero(mask)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    return (int(x), int(y), int(w), int(h))


def composite(original: np.ndarray, filled: np.ndarray, mask: np.ndarray, sigma: float = 1.2) -> np.ndarray:
    """Hard-replace masked pixels; everything outside the mask stays bit-identical.

    Feathering the outer edge would blend remnants of the removed object back in
    (e.g. a pink halo from a red badge), so the replacement is deliberately hard.
    Seam quality is handled by the mask padding and the inpainting engine.
    """
    out = original.copy()
    sel = mask > 0
    out[sel] = filled[sel]
    return out


def overlay_regions(bgr: np.ndarray, regions, alpha: float = 0.35) -> np.ndarray:
    """Debug/preview overlay: translucent masks + colored boxes."""
    out = bgr.copy()
    colors = {"element": (0, 200, 0), "text": (0, 165, 255), "uncertain": (0, 0, 255)}
    tint = out.copy()
    for r in regions:
        if r.mask is not None:
            tint[r.mask > 0] = colors.get(r.kind.value, (255, 0, 255))
    out = cv2.addWeighted(tint, alpha, out, 1 - alpha, 0)
    for r in regions:
        x, y, w, h = r.bbox
        color = colors.get(r.kind.value, (255, 0, 255))
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 1)
    return out

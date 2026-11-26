"""Generate synthetic UI screenshots with known ground truth (offline, no assets needed).

Each fixture targets one behaviour: standalone text, filled button, outlined
badge, gradient background, dark mode, multiple occurrences, panel guard,
4K tiling and a no-match case.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT_REG = FONT_DIR / "DejaVuSans.ttf"
FONT_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"


def font(size: int, bold: bool = False):
    """Fail loudly when DejaVu is missing: ground truth depends on this font."""
    path = FONT_BOLD if bold else FONT_REG
    if not path.exists():
        raise FileNotFoundError(f"DejaVu font required for reproducible fixtures: {path}")
    return ImageFont.truetype(str(path), size)


def _canvas(w: int, h: int, color=(255, 255, 255)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def _gradient(w: int, h: int, c1=(250, 250, 250), c2=(200, 210, 225)) -> Image.Image:
    arr = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        t = y / max(1, h - 1)
        arr[y, :, :] = [int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)]
    return Image.fromarray(arr)


def _text(draw: ImageDraw.ImageDraw, xy, text, size, fill=(20, 20, 20), bold=False):
    draw.text(xy, text, font=font(size, bold), fill=fill)


def btn_filled() -> Image.Image:
    img = _canvas(720, 360, (242, 243, 245))
    d = ImageDraw.Draw(img)
    _text(d, (40, 30), "Account settings", 26, bold=True)
    _text(d, (40, 80), "Manage your subscription and billing.", 18, (90, 95, 105))
    d.rounded_rectangle((40, 140, 260, 196), radius=10, fill=(37, 99, 235))
    _text(d, (78, 158), "Upgrade now", 20, (255, 255, 255), bold=True)
    d.rounded_rectangle((290, 140, 420, 196), radius=10, outline=(180, 184, 190), width=2)
    _text(d, (322, 158), "Cancel", 20, (60, 64, 72))
    _text(d, (40, 250), "VPN", 20, (30, 30, 30))
    return img


def text_gradient() -> Image.Image:
    img = _gradient(720, 360)
    d = ImageDraw.Draw(img)
    _text(d, (60, 60), "Project Phoenix", 34, (25, 28, 35), bold=True)
    _text(d, (60, 140), "Confidential", 40, (180, 30, 30), bold=True)
    _text(d, (60, 220), "Internal use only - do not distribute", 20, (60, 64, 72))
    return img


def multi_occurrence() -> Image.Image:
    img = _canvas(760, 420)
    d = ImageDraw.Draw(img)
    for i, y in enumerate((40, 150, 260, 350)):
        _text(d, (40, y), f"Row {i + 1}: Internal Use Only", 22, (20, 20, 20))
        _text(d, (40, y + 32), "Regular content that must stay untouched.", 16, (110, 110, 110))
    d.rectangle((520, 40, 720, 90), fill=(240, 240, 240))
    _text(d, (540, 55), "VPN", 22, (20, 20, 20), bold=True)
    return img


def dark_mode() -> Image.Image:
    img = _canvas(720, 360, (24, 26, 30))
    d = ImageDraw.Draw(img)
    _text(d, (40, 40), "Network", 26, (240, 240, 240), bold=True)
    _text(d, (40, 100), "Connected", 20, (150, 220, 160))
    d.rounded_rectangle((40, 150, 190, 198), radius=24, fill=(58, 62, 70))
    _text(d, (72, 162), "VPN", 20, (235, 235, 235), bold=True)
    _text(d, (40, 250), "Confidential", 22, (220, 220, 220))
    return img


def panel_guard() -> Image.Image:
    """Keyword lives inside a card that also contains unrelated text."""
    img = _canvas(760, 400, (236, 238, 242))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 40, 700, 300), radius=14, fill=(255, 255, 255), outline=(210, 214, 220), width=2)
    _text(d, (90, 70), "VPN", 24, (20, 20, 20), bold=True)
    _text(d, (90, 115), "Status: connected to Frankfurt", 18, (70, 74, 82))
    _text(d, (90, 150), "Kill switch enabled", 18, (70, 74, 82))
    _text(d, (90, 185), "Protocol: WireGuard", 18, (70, 74, 82))
    d.rounded_rectangle((90, 230, 250, 275), radius=8, fill=(37, 99, 235))
    _text(d, (120, 244), "Disconnect", 17, (255, 255, 255))
    return img


def big_4k() -> Image.Image:
    img = _canvas(3840, 2160, (250, 250, 252))
    d = ImageDraw.Draw(img)
    _text(d, (120, 100), "Dashboard", 54, (20, 20, 20), bold=True)
    for i in range(6):
        y = 300 + i * 260
        d.rounded_rectangle((120, y, 1800, y + 180), radius=16, outline=(215, 218, 224), width=3)
        _text(d, (170, y + 40), f"Report {i + 1}", 34, (30, 30, 30), bold=True)
        _text(d, (170, y + 100), "Confidential", 30, (170, 30, 30))
    d.rounded_rectangle((3000, 160, 3600, 260), radius=40, fill=(20, 120, 220))
    _text(d, (3080, 185), "VPN", 40, (255, 255, 255), bold=True)
    return img


def bleed_white() -> Image.Image:
    """White background, red border + black text: inpainting colour-bleed case."""
    img = _canvas(400, 300)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 85, 300, 215), outline=(235, 0, 0), width=5)
    _text(d, (80, 125), "VPN", 36, (10, 10, 10), bold=True)
    d.line((80, 205, 280, 205), fill=(235, 0, 0), width=4)
    return img


def bleed_gradient() -> Image.Image:
    """Gradient background with the same red border/text contamination."""
    arr = np.zeros((300, 400, 3), np.uint8)
    for y in range(300):
        value = int(255 - 100 * y / 299)
        arr[y, :, :] = (value, value, value)
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 85, 300, 215), outline=(235, 0, 0), width=5)
    _text(d, (80, 125), "VPN", 36, (10, 10, 10), bold=True)
    d.line((80, 205, 280, 205), fill=(235, 0, 0), width=4)
    return img


def gray_card_black_page() -> tuple[Image.Image, dict]:
    """Black page, gray card, black 'VPN' text inside the card."""
    img = _canvas(720, 400, (0, 0, 0))
    d = ImageDraw.Draw(img)
    card = (60, 140, 340, 70)
    color = (92, 94, 98)  # RGB gray
    d.rectangle((card[0], card[1], card[0] + card[2], card[1] + card[3]),
                fill=color)
    button = (65, 145, 330, 60)
    d.rectangle((button[0], button[1], button[0] + button[2], button[1] + button[3]),
                fill=(210, 210, 210))
    f = font(26, bold=True)
    d.text((80, 158), "VPN", font=f, fill=(15, 15, 15))
    bb = d.textbbox((80, 158), "VPN", font=f)
    text_box = [bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]]
    gt = {
        "page_bgr": [0, 0, 0],
        "card_bgr": [color[2], color[1], color[0]],
        "card_bbox": list(card),
        "button_bbox": list(button),
        "text_bbox": text_box,
        "unit_mask_bbox": [76, 154, 90, 38],
    }
    return img, gt


def blue_box_white_page() -> tuple[Image.Image, dict]:
    """White page, blue box, black 'VPN' text inside the box."""
    img = _canvas(720, 400, (255, 255, 255))
    d = ImageDraw.Draw(img)
    box = (60, 140, 340, 70)
    color = (37, 99, 235)  # RGB blue
    d.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]),
                fill=color)
    f = font(26, bold=True)
    d.text((80, 158), "VPN", font=f, fill=(15, 15, 15))
    bb = d.textbbox((80, 158), "VPN", font=f)
    text_box = [bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]]
    gt = {
        "page_bgr": [255, 255, 255],
        "box_bgr": [color[2], color[1], color[0]],
        "box_bbox": list(box),
        "text_bbox": text_box,
        "unit_mask_bbox": [76, 154, 90, 38],
    }
    return img, gt


def vpn_button_icon_annotation() -> tuple[Image.Image, dict]:
    """Black icon + blue VPN button + red annotation crossing the icon."""
    clean = _canvas(720, 400, (255, 255, 255))
    cd = ImageDraw.Draw(clean)
    cx, cy, radius = 70, 200, 30
    for r in (radius, radius - 12):
        cd.arc((cx - r, cy - r, cx + r, cy + r), 20, 340, fill=(10, 10, 10), width=6)
    cd.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(10, 10, 10))

    img = clean.copy()
    d = ImageDraw.Draw(img)
    button = (90, 182, 120, 36)
    d.rounded_rectangle((button[0], button[1], button[0] + button[2], button[1] + button[3]),
                        radius=8, fill=(37, 99, 235), outline=(10, 10, 10), width=3)
    f = font(20, bold=True)
    d.text((112, 190), "VPN", font=f, fill=(255, 255, 255))
    raw_bb = d.textbbox((112, 190), "VPN", font=f)
    text_bb = [raw_bb[0], raw_bb[1], raw_bb[2] - raw_bb[0], raw_bb[3] - raw_bb[1]]
    d.rectangle((55, 190, 150, 196), fill=(235, 0, 0))  # red annotation over icon
    d.rectangle((100, 176, 170, 182), fill=(235, 0, 0))

    gt = {
        "page_bgr": [255, 255, 255],
        "icon_bgr": [10, 10, 10],
        "button_bbox": list(button),
        "text_bbox": list(text_bb),
        "icon_pristine_bbox": [cx - 25, cy + 18, 50, 14],
        "icon_core_bbox": [cx - radius, cy - radius, 2 * radius, 2 * radius],
        "red_union_bbox": [55, 176, 115, 20],
    }
    return img, gt


def vpn_button_icon_clean() -> Image.Image:
    """Ground truth for `vpn_button_icon_annotation`: icon on white only."""
    img = _canvas(720, 400, (255, 255, 255))
    d = ImageDraw.Draw(img)
    cx, cy, radius = 70, 200, 30
    for r in (radius, radius - 12):
        d.arc((cx - r, cy - r, cx + r, cy + r), 20, 340, fill=(10, 10, 10), width=6)
    d.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(10, 10, 10))
    return img


def multi_container() -> tuple[Image.Image, dict]:
    """Two different containers in one image (Phase 8 regression).

    Left: gray card on a black page, with a light button inside.
    Right: blue box on a white page, with black text inside.
    The two regions must be restored with their own local background.
    """
    img = _canvas(1000, 480, (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((500, 0, 999, 479), fill=(255, 255, 255))

    card = (50, 120, 480, 300)       # x1, y1, x2, y2
    button = (70, 150, 460, 230)
    card_rgb = (92, 94, 98)
    d.rectangle(card, fill=card_rgb)
    d.rectangle(button, fill=(210, 210, 210))
    f = font(28, bold=True)
    d.text((95, 170), "VPN", font=f, fill=(15, 15, 15))
    left_text = list(d.textbbox((95, 170), "VPN", font=f))

    box = (560, 120, 950, 230)
    box_rgb = (37, 99, 235)
    d.rectangle(box, fill=box_rgb)
    d.text((590, 150), "VPN", font=f, fill=(15, 15, 15))
    right_text = list(d.textbbox((590, 150), "VPN", font=f))

    def xywb(b):
        return [b[0], b[1], b[2] - b[0], b[3] - b[1]]

    gt = {
        "left_page_bgr": [0, 0, 0],
        "left_card_bgr": [card_rgb[2], card_rgb[1], card_rgb[0]],
        "left_card_bbox": xywb(card),
        "left_button_bbox": xywb(button),
        "left_text_bbox": xywb(left_text),
        "right_page_bgr": [255, 255, 255],
        "right_box_bgr": [box_rgb[2], box_rgb[1], box_rgb[0]],
        "right_box_bbox": xywb(box),
        "right_text_bbox": xywb(right_text),
    }
    return img, gt


def preview_parity() -> tuple[Image.Image, dict]:
    """One button containing the keyword and unrelated text (preview vs save)."""
    img = _canvas(520, 220)
    d = ImageDraw.Draw(img)
    button = (40, 60, 330, 150)       # x1, y1, x2, y2
    d.rectangle(button, fill=(210, 210, 210))
    f = font(24, bold=True)
    d.text((60, 95), "VPN", font=f, fill=(20, 20, 20))
    d.text((240, 95), "KEEP", font=f, fill=(20, 20, 20))
    vpn_box = list(d.textbbox((60, 95), "VPN", font=f))
    keep_box = list(d.textbbox((240, 95), "KEEP", font=f))

    def xywb(b):
        return [b[0], b[1], b[2] - b[0], b[3] - b[1]]

    gt = {
        "button_bbox": xywb(button),
        "vpn_bbox": xywb(vpn_box),
        "keep_bbox": xywb(keep_box),
        "button_bgr": [210, 210, 210],
    }
    return img, gt


def _xywb(box) -> list[int]:
    return [box[0], box[1], box[2] - box[0], box[3] - box[1]]


def protected_icon_variants() -> tuple[Image.Image, dict]:
    """Five icon/button layouts that must all protect the visible icon.

    The current refinement only protects dark compact components to the LEFT
    of the matched text, so 'right', 'above', 'light' and 'colored' are red.
    """
    img = _canvas(1200, 620)
    d = ImageDraw.Draw(img)
    f = font(24, bold=True)
    cases: dict[str, dict] = {}

    def add_case(name: str, ox: int, oy: int, button, text_xy,
                 icon_center, icon_radius, button_rgb, text_rgb, icon_rgb,
                 region=None):
        d.rectangle((ox + button[0], oy + button[1], ox + button[2], oy + button[3]),
                    fill=button_rgb)
        d.text((ox + text_xy[0], oy + text_xy[1]), "VPN", font=f, fill=text_rgb)
        tb = list(d.textbbox((ox + text_xy[0], oy + text_xy[1]), "VPN", font=f))
        cx, cy = ox + icon_center[0], oy + icon_center[1]
        d.ellipse((cx - icon_radius, cy - icon_radius,
                   cx + icon_radius, cy + icon_radius), fill=icon_rgb)
        icon_box = (cx - icon_radius, cy - icon_radius,
                    cx + icon_radius, cy + icon_radius)
        if region is None:
            region = (
                min(ox + button[0], icon_box[0]) - 2,
                min(oy + button[1], icon_box[1]) - 2,
                max(ox + button[2], icon_box[2]) + 2,
                max(oy + button[3], icon_box[3]) + 2,
            )
        else:
            region = (ox + region[0], oy + region[1], ox + region[2], oy + region[3])
        cases[name] = {
            "button_bbox": _xywb((ox + button[0], oy + button[1],
                                  ox + button[2], oy + button[3])),
            "text_bbox": _xywb(tb),
            "icon_bbox": _xywb(icon_box),
            "region_bbox": _xywb(region),
            "button_bgr": [button_rgb[2], button_rgb[1], button_rgb[0]],
            "icon_bgr": [icon_rgb[2], icon_rgb[1], icon_rgb[0]],
            "page_bgr": [255, 255, 255],
        }

    # Black icon left / right / above, on a light button.
    add_case("left", 0, 0, (60, 120, 300, 200), (95, 145),
             (35, 160), 26, (210, 210, 210), (20, 20, 20), (10, 10, 10))
    add_case("right", 400, 0, (60, 120, 300, 200), (95, 145),
             (325, 160), 26, (210, 210, 210), (20, 20, 20), (10, 10, 10))
    add_case("above", 800, 0, (60, 130, 300, 210), (95, 155),
             (180, 105), 26, (210, 210, 210), (20, 20, 20), (10, 10, 10))

    # Light icon on a dark button; colored icon on a light button.
    add_case("light_on_dark", 0, 310, (60, 120, 300, 200), (95, 145),
             (270, 160), 20, (40, 40, 40), (240, 240, 240), (235, 235, 235))
    add_case("colored_on_light", 400, 310, (60, 120, 300, 200), (95, 145),
             (270, 160), 20, (230, 230, 230), (20, 20, 20), (220, 60, 40))

    return img, {"cases": cases}


def unrelated_red_badge() -> tuple[Image.Image, dict]:
    """A red badge near (but not connected to) the target button."""
    img = _canvas(500, 260)
    d = ImageDraw.Draw(img)
    button = (60, 80, 300, 170)
    d.rectangle(button, fill=(210, 210, 210))
    f = font(24, bold=True)
    d.text((95, 110), "VPN", font=f, fill=(20, 20, 20))
    text_box = list(d.textbbox((95, 110), "VPN", font=f))
    badge = (315, 110, 339, 134)
    d.rectangle(badge, fill=(235, 0, 0))
    return img, {
        "button_bbox": _xywb(button),
        "text_bbox": _xywb(text_box),
        "badge_bbox": _xywb(badge),
        "button_bgr": [210, 210, 210],
        "badge_bgr": [0, 0, 235],
        "page_bgr": [255, 255, 255],
    }


def no_match() -> Image.Image:
    img = _canvas(640, 260)
    d = ImageDraw.Draw(img)
    _text(d, (40, 40), "Nothing sensitive here", 30, (20, 20, 20))
    _text(d, (40, 110), "Just a normal paragraph of text.", 20, (90, 90, 90))
    return img


def substring_zza() -> tuple[Image.Image, dict]:
    """One OCR word containing a suffix keyword, plus a distant element.

    Returns (image, ground truth) so tests compare against real per-glyph boxes
    instead of hardcoded guesses.
    """
    img = _canvas(720, 240)
    d = ImageDraw.Draw(img)
    text = "abcxyzzza"
    f = font(60)
    x, y = 40, 30
    d.text((x, y), text, font=f, fill=(15, 15, 15))

    # Ground truth = actual ink bounds (textbbox includes the advance/right
    # side bearing, which is ~5px wider than the visible glyph and would make
    # a localization test fail for the wrong reason).
    char_boxes: list[list[int]] = []
    advance = 0.0
    for ch in text:
        ink = Image.new("L", img.size, 0)
        ImageDraw.Draw(ink).text((x + advance, y), ch, font=f, fill=255)
        char_boxes.append(list(ink.getbbox()))
        advance += f.getlength(ch)

    keep_text = "KEEPTHIS"
    keep_font = font(26)
    d.text((40, 160), keep_text, font=keep_font, fill=(15, 15, 15))
    keep_bbox = list(d.textbbox((40, 160), keep_text, font=keep_font))

    def union(boxes):
        return [
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        ]

    gt = {
        "text": text,
        "font": str(FONT_REG),
        "font_size": 60,
        "origin": [x, y],
        "char_boxes": char_boxes,
        "zza_bbox": union(char_boxes[6:9]),       # last 3 glyphs
        "prefix_bbox": union(char_boxes[0:6]),    # abcxyz – must stay untouched
        "keep_text": keep_text,
        "keep_bbox": keep_bbox,
    }
    return img, gt


BUILDERS = {
    "btn_filled": btn_filled,
    "text_gradient": text_gradient,
    "multi_occurrence": multi_occurrence,
    "dark_mode": dark_mode,
    "panel_guard": panel_guard,
    "big_4k": big_4k,
    "no_match": no_match,
    "substring_zza": substring_zza,
    "bleed_white": bleed_white,
    "bleed_gradient": bleed_gradient,
    "gray_card_black_page": gray_card_black_page,
    "blue_box_white_page": blue_box_white_page,
    "vpn_button_icon_annotation": vpn_button_icon_annotation,
    "vpn_button_icon_clean": vpn_button_icon_clean,
    "multi_container": multi_container,
    "preview_parity": preview_parity,
    "protected_icon_variants": protected_icon_variants,
    "unrelated_red_badge": unrelated_red_badge,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, builder in BUILDERS.items():
        path = out / f"{name}.png"
        result = builder()
        if isinstance(result, tuple):
            image, ground_truth = result
            image.save(path)
            gt_path = out / f"{name}.json"
            gt_path.write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")
            print(f"wrote {path} + {gt_path}")
        else:
            result.save(path)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()

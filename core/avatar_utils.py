import hashlib
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


def get_initials(full_name: str | None, username: str | None) -> str:
    """
    Create 1-2 letter initials.

    Examples:
    - "Hanan Bajramovic" -> "HB"
    - "Madonna" -> "M"
    """

    name = (full_name or "").strip()
    if not name and username:
        name = username.strip()

    if not name:
        return "U"

    parts = [p for p in name.replace(".", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if len(parts) == 1:
        word = parts[0]
        if len(word) >= 2:
            return (word[0] + word[1]).upper()
        return word[0].upper()

    return "U"


def _pick_colors(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """
    Pick a (background, foreground) color pair deterministically.

    We intentionally include both dark and light backgrounds so the text
    remains readable (dark text on light backgrounds, white text on dark).
    """

    palettes: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
        ((0, 82, 204), (255, 255, 255)),
        ((94, 53, 177), (255, 255, 255)),
        ((23, 43, 77), (255, 255, 255)),
        ((0, 105, 92), (255, 255, 255)),
        ((222, 235, 255), (0, 82, 204)),
        ((244, 245, 247), (23, 43, 77)),
        ((255, 235, 210), (138, 62, 0)),
        ((223, 247, 232), (47, 132, 86)),
        ((224, 204, 255), (76, 29, 149)),
        ((76, 29, 149), (255, 255, 255)),
    ]

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    idx = digest[0] % len(palettes)
    return palettes[idx]


def generate_initials_avatar_png(
    initials: str,
    *,
    size: int = 256,
    seed: str | None = None,
) -> bytes:
    """
    Generate a square PNG avatar with a Jira-like big circle background and
    large initials.
    """
    bg, fg = _pick_colors(seed or initials)

    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)

    def _load_font(font_size: int):

        candidate_paths = [
            "/System/Library/Fonts/Geneva.ttf",
            "/System/Library/Fonts/Keyboard.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/ArialHB.ttc",
            "/System/Library/Fonts/LucidaGrande.ttc",
            "/System/Library/Fonts/Monaco.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/DejaVuSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
        for p in candidate_paths:
            try:
                return ImageFont.truetype(p, font_size)
            except Exception:
                continue
        return None

    font_size = int(size * 0.45)
    font = _load_font(font_size)
    font_is_truetype = isinstance(font, ImageFont.FreeTypeFont)
    if font is None:
        font = ImageFont.load_default()
        font_is_truetype = False

    text = initials[:2].upper()

    padding = int(size * 0.11)
    max_w = size - 2 * padding
    max_h = size - 2 * padding

    if font_is_truetype:
        final_bbox = None
        while True:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            if text_w <= max_w and text_h <= max_h:
                final_bbox = bbox
                break
            font_size = max(12, font_size - 2)
            new_font = _load_font(font_size)
            if new_font is None:
                break
            font = new_font

        bbox = final_bbox or draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (size - text_w) // 2 - bbox[0]
        y = (size - text_h) // 2 - bbox[1]
        draw.text((x, y), text, fill=fg, font=font)
    else:
        tmp = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)
        bbox = tmp_draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        tmp_draw.text((0, 0), text, fill=(*fg, 255), font=font)
        content = tmp.crop(bbox)
        content_w, content_h = content.size
        if content_w <= 0 or content_h <= 0:
            return b""

        scale = min(max_w / content_w, max_h / content_h) * 0.82
        scaled_w = max(1, int(content_w * scale))
        scaled_h = max(1, int(content_h * scale))

        content = content.resize((scaled_w, scaled_h), resample=Image.NEAREST)

        x = (size - scaled_w) // 2
        y = (size - scaled_h) // 2

        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(content, dest=(x, y))
        img = img_rgba.convert("RGB")

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

"""AI Money thumbnail: vibrant AI base image + number chip + bold text (PIL)."""
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .utils import run_cmd

FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ACCENT_RED = (232, 29, 38)
ACCENT_YELLOW = (255, 214, 10)


def _gen_base(cfg, run, meta, logger):
    subject = meta.get("thumbnail_subject") or "futuristic AI interface glowing"
    style = (
        f"{subject}, bold vibrant tech editorial illustration, dramatic rim "
        f"lighting, high contrast, saturated colors, glowing neon accents, "
        f"futuristic, ultra detailed, clean composition, no text, no letters"
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        out = Path(tf.name)
    try:
        run_cmd(["z-ai", "image", "-p", style, "-o", str(out), "-s", "1344x768"],
                timeout=300, retries=2, logger=logger, backoff=15)
        return out
    except Exception:  # noqa: BLE001
        out.unlink(missing_ok=True)
        # free fallback: Pollinations (flux) - unique vibrant base image
        try:
            from .visuals import ai_image
            dest = run["dir"] / "thumbnail_base.jpg"
            if ai_image(style, dest, 1280, 720, logger):
                return dest
        except Exception as e2:  # noqa: BLE001
            logger.warning(f"pollinations thumbnail failed: {str(e2)[:100]}")
        vis = sorted((run["dir"] / "visuals").glob("scene_*.jpg"))
        return vis[0] if vis else None


def _fit_font(d, text_lines, max_w, start_size, stroke=8):
    """Shrink font until every line fits max_w."""
    size = start_size
    while size > 44:
        f = ImageFont.truetype(FONT_B, size)
        widest = max(d.textlength(ln, font=f) + 2 * stroke for ln in text_lines)
        if widest <= max_w:
            return f
        size -= 6
    return ImageFont.truetype(FONT_B, 44)


def make_thumbnail(cfg, run, meta, logger):
    W, H = 1280, 720
    base = _gen_base(cfg, run, meta, logger)
    if base is None:
        logger.warning("no thumbnail base available, skipping")
        return None
    img = Image.open(base).convert("RGB").resize((W, H), Image.LANCZOS)

    # pop: saturation + contrast boost, subtle vignette
    img = ImageEnhance.Color(img).enhance(1.18)
    img = ImageEnhance.Contrast(img).enhance(1.08)

    d = ImageDraw.Draw(img, "RGBA")
    # bottom gradient for text legibility
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(int(H * 0.42), H):
        a = int(225 * (y - H * 0.42) / (H * 0.58))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    img.paste(Image.alpha_composite(img.convert("RGBA"), grad).convert("RGB"),
              (0, 0))
    d = ImageDraw.Draw(img, "RGBA")

    # channel badge top-left (red chip)
    badge = cfg["channel"]["badge_text"]
    f_badge = ImageFont.truetype(FONT_B, 40)
    bw = d.textlength(badge, font=f_badge)
    d.rectangle((28, 30, 28 + bw + 38, 94), fill=ACCENT_RED + (240,))
    d.text((47, 36), badge, font=f_badge, fill="white")

    # number chip top-right (yellow square: story/tool count)
    num = str(meta.get("thumbnail_number") or "").strip()
    kind = str(meta.get("thumbnail_kind") or "STORIES").upper()[:7]
    if num:
        f_num = ImageFont.truetype(FONT_B, 96)
        f_knd = ImageFont.truetype(FONT_B, 26)
        kw = max(d.textlength(num, font=f_num), 92) + 44
        kh = 118
        x1, y1 = W - 40 - int(kw), 30
        d.rounded_rectangle((x1, y1, W - 40, y1 + kh), radius=18,
                            fill=ACCENT_YELLOW + (245,))
        nw = d.textlength(num, font=f_num)
        d.text((x1 + (kw - nw) / 2, y1 + 2), num, font=f_num, fill=(12, 10, 4))
        lw = d.textlength(kind, font=f_knd)
        d.text((x1 + (kw - lw) / 2, y1 + kh - 30), kind, font=f_knd,
               fill=(60, 48, 8))

    # punch text: up to 3 lines bottom-left, dynamic size, last line in yellow box
    words = (meta.get("thumbnail_text") or "AI MONEY DIGEST").upper().split()
    lines = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= 13:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
        if len(lines) == 2:
            break
    if cur and len(lines) < 3:
        lines.append(cur)
    lines = lines[:3] or ["AI MONEY DIGEST"]

    f_main = _fit_font(d, lines, W - 90 - (160 if num else 0), 104)
    line_h = f_main.size + 22
    y = H - 64 - line_h * len(lines)
    x = 40
    stroke = 9
    for i, ln in enumerate(lines):
        last = (i == len(lines) - 1)
        if last:
            lw = d.textlength(ln, font=f_main)
            pad_x, pad_y = 14, 4
            d.rectangle((x - pad_x, y - pad_y - stroke,
                         x + lw + pad_x + stroke, y + f_main.size + pad_y),
                        fill=ACCENT_YELLOW + (255,))
            d.text((x, y), ln, font=f_main, fill=(14, 10, 4))
        else:
            d.text((x, y), ln, font=f_main, fill="white",
                   stroke_width=stroke, stroke_fill=(12, 12, 16))
        y += line_h

    dest = run["dir"] / "thumbnail.jpg"
    img.save(dest, "JPEG", quality=92)
    logger.info(f"thumbnail saved: {dest}")
    return dest

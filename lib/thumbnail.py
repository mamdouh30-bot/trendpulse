"""AI Money thumbnail v2: vibrant AI base + fixed host persona + bold type.

Layout: AI-generated subject background (left-weighted), host portrait
right side with soft mask + shadow, big Montserrat headline with a yellow
highlight box, red channel badge and yellow number chip.
"""
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import persona
from .utils import run_cmd

FONT_B = persona.FONT_B if persona.FONT_B.exists() else \
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_XB = persona.FONT_XB if persona.FONT_XB.exists() else FONT_B
ACCENT_RED = (232, 29, 38)
ACCENT_YELLOW = (255, 214, 10)


def _font(path, size):
    if Path(path).exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


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
        # free fallback: Pollinations (1024 px cap) - upscaled + sharpened
        try:
            from .visuals import ai_image
            dest = run["dir"] / "thumbnail_base.jpg"
            if ai_image(style, dest, 1024, 576, logger):
                return dest
        except Exception as e2:  # noqa: BLE001
            logger.warning(f"pollinations thumbnail failed: {str(e2)[:100]}")
        vis = sorted((run["dir"] / "visuals").glob("scene_*.jpg"))
        # best fallback: a frame grab from the real B-roll footage
        try:
            fr = sorted((run["dir"] / "footage" / "cache")
                        .glob("clip_*.mp4"))
            if fr:
                grab = run["dir"] / "thumbnail_base.jpg"
                run_cmd(["ffmpeg", "-y", "-v", "quiet", "-ss", "1.5",
                         "-i", str(fr[0]), "-frames:v", "1", str(grab)],
                        timeout=60, retries=1, logger=logger)
                if grab.exists() and grab.stat().st_size > 20_000:
                    return grab
        except Exception:  # noqa: BLE001
            pass
        return vis[0] if vis else None


def _persona_layer(W, H, cfg):
    """Host portrait cutout for the right side (or None if assets missing)."""
    shot = persona.shot_path(cfg, "thumb")
    if not shot:
        return None
    pw = int(W * 0.44)
    ph = H
    img = Image.open(shot).convert("RGB")
    # focus on the face (upper-center of the square portrait)
    crop = persona.cover_crop(img, pw, ph, focus_y=0.30)
    # soft elliptical mask so the cutout blends into the background
    mask = Image.new("L", (pw, ph), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((-pw * 0.18, -H * 0.06, pw * 1.18, H * 1.06), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(28))
    return crop.convert("RGBA"), mask


def _fit_font(d, text_lines, max_w, start_size, stroke=8):
    """Shrink font until every line fits max_w."""
    size = start_size
    while size > 44:
        f = _font(FONT_XB, size)
        widest = max(d.textlength(ln, font=f) + 2 * stroke for ln in text_lines)
        if widest <= max_w:
            return f
        size -= 6
    return _font(FONT_XB, 44)


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

    # left-side dark gradient so text always pops
    d = ImageDraw.Draw(img, "RGBA")
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for x in range(int(W * 0.30)):
        a = int(235 * (1 - x / (W * 0.30)))
        gd.line([(x, 0), (x, H)], fill=(6, 8, 16, a))
    img = Image.alpha_composite(img.convert("RGBA"), grad)

    # host persona on the right
    pl = _persona_layer(W, H, cfg)
    if pl is not None:
        crop, mask = pl
        px = W - crop.size[0] + int(W * 0.02)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sh = Image.new("RGBA", crop.size, (0, 0, 0, 160))
        shadow.paste(sh, (px + 10, 14), mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        img = Image.alpha_composite(img, shadow)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.paste(crop, (px, 0), mask)
        img = Image.alpha_composite(img, layer)

    d = ImageDraw.Draw(img, "RGBA")

    # channel badge top-left (red chip)
    badge = cfg["channel"]["badge_text"]
    f_badge = _font(FONT_B, 40)
    bw = d.textlength(badge, font=f_badge)
    d.rectangle((28, 30, 28 + bw + 38, 94), fill=ACCENT_RED + (240,))
    d.text((47, 36), badge, font=f_badge, fill="white")

    # number chip top-right area (shifted left so it never covers the host)
    num = str(meta.get("thumbnail_number") or "").strip()
    kind = str(meta.get("thumbnail_kind") or "STORIES").upper()[:7]
    if num:
        f_num = _font(FONT_XB, 92)
        f_knd = _font(FONT_B, 24)
        kw = max(d.textlength(num, font=f_num), 88) + 40
        kh = 112
        x1, y1 = int(W * 0.60) - int(kw), 30
        d.rounded_rectangle((x1, y1, x1 + int(kw), y1 + kh), radius=18,
                            fill=ACCENT_YELLOW + (245,))
        nw = d.textlength(num, font=f_num)
        d.text((x1 + (kw - nw) / 2, y1 + 2), num, font=f_num, fill=(12, 10, 4))
        lw = d.textlength(kind, font=f_knd)
        d.text((x1 + (kw - lw) / 2, y1 + kh - 28), kind, font=f_knd,
               fill=(60, 48, 8))

    # punch text: up to 3 lines bottom-left, dynamic size, last line yellow box
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

    f_main = _fit_font(d, lines, int(W * 0.62), 104)
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
    img.convert("RGB").save(dest, "JPEG", quality=92)
    logger.info(f"thumbnail saved: {dest}")
    return dest

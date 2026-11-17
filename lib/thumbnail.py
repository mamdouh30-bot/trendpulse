"""YouTube thumbnail (portable edition): free AI base image + bold overlay text.

Base image via Pollinations (free, no key); falls back to the first scene
photo when generation fails.
"""
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .visuals import ai_image

FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _gen_base(cfg, run, meta, logger):
    subject = meta.get("thumbnail_subject") or meta.get("title", "breaking story")
    style = (
        f"{subject}, dramatic editorial photograph, cinematic composition, "
        f"dark moody atmosphere, high contrast, vignette, news documentary "
        f"style, photorealistic, high detail, copy space at bottom"
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        out = Path(tf.name)
    if ai_image(style, out, 1344, 768, logger):
        return out
    logger.warning("thumbnail base generation failed: falling back to a "
                   "scene photo")
    out.unlink(missing_ok=True)
    vis = sorted((run["dir"] / "visuals").glob("scene_*.jpg"))
    return vis[0] if vis else None


def make_thumbnail(cfg, run, meta, logger):
    W, H = 1280, 720
    base = _gen_base(cfg, run, meta, logger)
    if base is None:
        logger.warning("no thumbnail base available, skipping")
        return None
    img = Image.open(base).convert("RGB").resize((W, H), Image.LANCZOS)

    d = ImageDraw.Draw(img, "RGBA")
    # bottom gradient for text legibility
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(int(H * 0.45), H):
        a = int(210 * (y - H * 0.45) / (H * 0.55))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    img.paste(Image.alpha_composite(img.convert("RGBA"), grad).convert("RGB"),
              (0, 0))
    d = ImageDraw.Draw(img, "RGBA")

    # channel badge top-left
    badge = cfg["channel"]["badge_text"]
    f_badge = ImageFont.truetype(FONT_B, 34)
    bw = d.textlength(badge, font=f_badge)
    d.rectangle((28, 26, 28 + bw + 34, 84), fill=(214, 34, 44, 235))
    d.text((45, 32), badge, font=f_badge, fill="white")

    # punch text (2 lines max, yellow keyword highlight on last line)
    words = (meta.get("thumbnail_text") or meta.get("title", "")).upper().split()
    lines = [" ".join(words[:len(words) // 2]).strip(),
             " ".join(words[len(words) // 2:]).strip()]
    lines = [l for l in lines if l] or ["THE STORY THEY BURIED"]
    f_main = ImageFont.truetype(FONT_B, 92)
    y = H - 210 if len(lines) > 1 else H - 140
    for i, ln in enumerate(lines):
        lw = d.textlength(ln, font=f_main)
        x = max(24, (W - lw) / 2)
        highlight = (i == len(lines) - 1) and len(lines) > 1
        d.text((x, y), ln, font=f_main, fill="white",
               stroke_width=8, stroke_fill=(10, 10, 14))
        if highlight:
            d.rectangle((x - 8, y - 6, x + lw + 8, y + 96),
                        outline=(255, 214, 10, 255), width=5)
        y += 104

    dest = run["dir"] / "thumbnail.jpg"
    img.save(dest, "JPEG", quality=90)
    logger.info(f"thumbnail saved: {dest}")
    return dest

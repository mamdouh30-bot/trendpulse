"""Fixed host persona: Adam Sterling — shot selection + broadcast overlays.

The persona ships as committed studio shots (assets/persona). This module
composes them into 1080p broadcast frames: lower-thirds, name chips,
channel badge, episode label — the look of a produced TV studio.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FONT_XB = PROJECT_ROOT / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"
FONT_B = PROJECT_ROOT / "assets" / "fonts" / "Montserrat-Bold.ttf"
FONT_SB = PROJECT_ROOT / "assets" / "fonts" / "Montserrat-SemiBold.ttf"

NAVY = (10, 14, 26)
ACCENT = (41, 118, 232)      # broadcast blue
GOLD = (255, 196, 0)
WHITE = (244, 246, 250)
GREY = (172, 180, 194)


def _font(path, size):
    if Path(path).exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


def shot_path(cfg, kind, idx=0):
    """Resolve a persona shot file by kind (intro/bridge/cutaway/outro/thumb)."""
    p = cfg.get("persona", {}).get("shots_dir", "assets/persona")
    key = {"intro": "intro_shot", "cutaway": "cutaway_shot",
           "outro": "outro_shot", "thumb": "thumb_shot"}.get(kind)
    if kind == "bridge":
        shots = cfg.get("persona", {}).get("bridge_shots") or []
        name = shots[idx % len(shots)] if shots else "host_talk_a.png"
        path = PROJECT_ROOT / p / name
    else:
        name = cfg.get("persona", {}).get(key, "")
        path = PROJECT_ROOT / p / name
    return path if path.exists() else None


def cover_crop(img, W, H, focus_y=0.42):
    """Cover-crop any image to exactly W x H (focal point slightly high)."""
    sw, sh = img.size
    target = W / H
    if sw / sh > target:
        nw, nh = int(sh * target), sh
    else:
        nw, nh = sw, int(sw / target)
    nw, nh = max(nw, 8), max(nh, 8)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - W) // 2
    top = max(0, min(nh - H, int(nh * focus_y - H * focus_y)))
    return img.crop((left, top, left + W, top + H))


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _chip(draw, xy, text, font, fg=WHITE, bg=ACCENT, pad=(16, 8), radius=8):
    x, y = xy
    tw = draw.textlength(text, font=font)
    box = (x, y, x + tw + pad[0] * 2, y + font.size + pad[1] * 2)
    draw.rounded_rectangle(box, radius=radius, fill=bg)
    draw.text((x + pad[0], y + pad[1]), text, font=font, fill=fg)
    return box


def compose_host_frame(cfg, shot_file, W, H, headline=None, chip_text=None,
                       episode_label=None, corner_badge=True, cta=None,
                       darken=0.0):
    """Compose a persona shot into a 1080p broadcast frame (PIL)."""
    img = Image.open(shot_file).convert("RGB")
    frame = cover_crop(img, W, H)
    if darken > 0:
        frame = Image.eval(frame, lambda v: int(v * (1 - darken)))

    d = ImageDraw.Draw(frame, "RGBA")

    # bottom gradient for lower-third legibility
    grad_h = int(H * 0.42)
    for i in range(grad_h):
        y = H - grad_h + i
        a = int(205 * (i / grad_h) ** 1.4)
        d.line([(0, y), (W, y)], fill=(6, 8, 16, a))

    # top-left channel badge
    if corner_badge:
        badge = cfg.get("channel", {}).get("badge_text", "AI MONEY")
        _chip(d, (40, 36), badge, _font(FONT_B, 34), pad=(18, 8), radius=10)

    # episode label top-right
    if episode_label:
        f_ep = _font(FONT_SB, 30)
        tw = d.textlength(episode_label, font=f_ep)
        _chip(d, (W - tw - 72, 36), episode_label, f_ep,
              fg=(12, 16, 28), bg=GOLD, pad=(16, 8), radius=10)

    # lower third: accent bar + chip + headline
    if headline or chip_text:
        x0 = 64
        f_chip = _font(FONT_SB, 30)
        f_head = None
        lines = []
        if headline:
            size = 66
            while size > 44:
                cand = _font(FONT_XB, size)
                lines = _wrap(d, headline.upper(), cand, W - x0 - 120)
                if len(lines) <= 2:
                    f_head = cand
                    break
                size -= 6
            if f_head is None:
                f_head = _font(FONT_XB, 44)
                lines = _wrap(d, headline.upper(), f_head, W - x0 - 120)[:2]
        chip_h = (f_chip.size + 16) if chip_text else 0
        head_h = (f_head.size + 12) * len(lines) if lines else 0
        block_h = chip_h + head_h + 18
        y = H - 88 - block_h

        d.rectangle((x0, y, x0 + 12, y + block_h), fill=ACCENT)
        cy = y + 2
        if chip_text:
            _chip(d, (x0 + 34, cy), chip_text, f_chip,
                  fg=WHITE, bg=(ACCENT[0], ACCENT[1], ACCENT[2], 235))
            cy += chip_h
        for ln in lines:
            d.text((x0 + 34, cy), ln, font=f_head, fill=WHITE,
                   stroke_width=4, stroke_fill=(8, 10, 20))
            cy += f_head.size + 12

    # subscribe CTA ribbon (outro)
    if cta:
        f_cta = _font(FONT_XB, 58)
        tw = d.textlength(cta, font=f_cta)
        bx = (W - tw) // 2 - 34
        by = int(H * 0.20)
        d.rounded_rectangle((bx, by, bx + tw + 68, by + f_cta.size + 44),
                            radius=16, fill=(232, 29, 38, 245))
        d.text((bx + 34, by + 22), cta, font=f_cta, fill=WHITE)

    return frame


def brand_card(cfg, W, H, kicker, title, sub=None, accent=ACCENT):
    """Full-brand typographic card (logo sting / section card)."""
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    # subtle radial glow
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W * 0.15, H * 0.05, W * 0.85, H * 0.95), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    tint = Image.new("RGB", (W, H), (18, 42, 84))
    img = Image.composite(tint, img, glow)
    d = ImageDraw.Draw(img)

    f_k = _font(FONT_SB, 34)
    kw = d.textlength(kicker, font=f_k)
    _chip(d, ((W - kw) / 2 - 20, int(H * 0.335)), kicker, f_k,
          fg=WHITE, bg=accent, pad=(20, 10), radius=10)

    size = 128
    while size > 60:
        f_t = _font(FONT_XB, size)
        lines = _wrap(d, title.upper(), f_t, W - 160)
        if len(lines) <= 2 and max(d.textlength(x, font=f_t)
                                   for x in lines) <= W - 160:
            break
        size -= 8
    y = int(H * 0.44)
    for ln in lines:
        tw = d.textlength(ln, font=f_t)
        d.text(((W - tw) / 2, y), ln, font=f_t, fill=WHITE)
        y += f_t.size + 14
    if sub:
        f_s = _font(FONT_SB, 36)
        tw = d.textlength(sub, font=f_s)
        d.text(((W - tw) / 2, y + 22), sub, font=f_s, fill=GREY)
    return img

"""Scene visuals (portable edition): real stock photos first, free AI fallback,
branded tool cards with official logos.

Photo providers (chain, first configured wins):
  Pexels (PEXELS_API_KEY, free key) -> Pixabay (PIXABAY_API_KEY, free key)
  -> Openverse (no key, CC-licensed).

AI fallback: Pollinations image API (free tier is capped at 1024 px, so the
image is upscaled + sharpened locally). Tool cards render official product
logos (Clearbit) onto broadcast-style brand cards.
"""
import json
import os
import random
import re
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

from .utils import load_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SEARCH_TIMEOUT = 120
_DOWNLOAD_TIMEOUT = 60
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TrendPulse/2.0"}


def _env() -> dict:
    env = load_env(PROJECT_ROOT / ".env")
    env.update({k: v for k, v in os.environ.items() if v})
    return env


# -------------------------------------------------------------- providers ---
def _pexels(query, n, gl, env):
    r = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": n, "orientation": "landscape"},
        headers={"Authorization": env["PEXELS_API_KEY"]},
        timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    pool = []
    for p in r.json().get("photos", []):
        src = (p.get("src") or {}).get("original")
        if src:
            pool.append({"url": src, "w": p.get("width", 0)})
    return pool


def _pixabay(query, n, gl, env):
    r = requests.get(
        "https://pixabay.com/api/",
        params={"key": env["PIXABAY_API_KEY"], "q": query,
                "per_page": min(max(n, 3), 50), "image_type": "photo",
                "orientation": "horizontal", "safesearch": "true"},
        timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    pool = []
    for p in r.json().get("hits", []):
        url = p.get("largeImageURL") or p.get("webformatURL")
        if url:
            pool.append({"url": url, "w": p.get("imageWidth", 0)})
    return pool


def _openverse(query, n, gl, env):
    r = requests.get(
        "https://api.openverse.org/v1/images/",
        params={"q": query, "page_size": max(n, 10)},
        headers=UA, timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    pool = []
    for p in r.json().get("results", []):
        if p.get("url"):
            pool.append({"url": p["url"], "w": p.get("width") or 0})
    return pool


def _providers(env):
    chain = []
    if env.get("PEXELS_API_KEY"):
        chain.append(("pexels", _pexels))
    if env.get("PIXABAY_API_KEY"):
        chain.append(("pixabay", _pixabay))
    chain.append(("openverse", _openverse))
    return chain


def _search(query, n, gl, logger):
    env = _env()
    out = []
    for name, fn in _providers(env):
        try:
            got = fn(query, n, gl, env)
            out.extend({"url": p["url"], "query": query,
                        "w": p.get("w", 0)} for p in got)
            if len(out) >= 3:
                break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"photo provider '{name}' failed for "
                           f"'{query[:50]}': {str(e)[:120]}")
    return out


# --------------------------------------------------------------- AI image ---
def _enhance_upscale(raw: bytes, dest: Path, target_w=2048, target_h=1152):
    """Upscale + sharpen a small (<=1024 px) AI image to Ken-Burns quality."""
    import io
    from PIL import ImageFilter

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.size[0] < target_w:
        img = img.resize((target_w, target_h), Image.LANCZOS)
    img = img.filter(ImageFilter.UnsharpMask(radius=2.0, percent=100,
                                             threshold=2))
    img.save(dest, "JPEG", quality=90)


def ai_image(prompt, dest: Path, width=1024, height=576, logger=None) -> bool:
    """Generate one photorealistic image via Pollinations (free, no key).

    The free tier caps output at ~1024 px wide (larger sizes answer 402),
    so we request 1024x576 and upscale+sharpen locally. Retries 4 times —
    the free endpoint occasionally answers 503/402 under load."""
    import time
    base_url = ("https://image.pollinations.ai/prompt/"
                + urllib.parse.quote(prompt[:600])
                + f"?width={width}&height={height}&nologo=true&model=flux")
    last_err = None
    for attempt in range(1, 5):
        url = base_url + f"&seed={random.randint(0, 10**9)}"
        try:
            r = requests.get(url, timeout=240, headers=UA)
            r.raise_for_status()
            if len(r.content) < 20_000:
                raise ValueError(f"suspicious tiny image ({len(r.content)}B)")
            _enhance_upscale(r.content, dest)
            return True
        except Exception as e:  # noqa: BLE001
            last_err = e
            dest.unlink(missing_ok=True)
            if logger:
                logger.warning(f"ai_image attempt {attempt}/4 failed: "
                               f"{str(e)[:140]}")
            time.sleep(8 * attempt)
    if logger:
        logger.warning(f"ai_image exhausted retries: {str(last_err)[:140]}")
    return False


# ------------------------------------------------------------- normalization -
def _normalize(src: Path, dest: Path, W: int, H: int):
    """Cover-crop any image to exactly W x H (16:9)."""
    img = Image.open(src).convert("RGB")
    sw, sh = img.size
    target = W / H
    if sw / sh > target:
        nh = H
        nw = int(sw * H / sh)
    else:
        nw = W
        nh = int(sh * W / sw)
    nw = max(nw, 16)
    nh = max(nh, 16)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - W) // 2
    top = (nh - H) // 2
    img = img.crop((left, top, left + W, top + H))
    img.save(dest, "JPEG", quality=88)


def _download(url, dest, W, H):
    r = requests.get(url, timeout=_DOWNLOAD_TIMEOUT, stream=True, headers=UA)
    r.raise_for_status()
    tmp = dest.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    _normalize(tmp, dest, W, H)
    tmp.unlink(missing_ok=True)


def _ai_fallback(query, dest, W, H, logger):
    prompt = (f"{query}, editorial tech photograph, realistic, modern, crisp "
              f"professional lighting, high contrast, punchy colors, "
              f"high detail")
    tmp = dest.with_suffix(".ai.tmp")
    if ai_image(prompt, tmp, 1024, 576, logger):
        try:
            _normalize(tmp, dest, W, H)
            return True
        finally:
            tmp.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    return False


def _words(q):
    return set(re.findall(r"[a-z]{3,}", q.lower()))


# ------------------------------------------------------------- tool cards ---
def _match_tools(cfg, text):
    """Return [(key, {url,label})] whose name appears as a word in text."""
    t = str(text or "").lower()
    out = []
    for key, meta in (cfg.get("links", {}).get("tools") or {}).items():
        if re.search(rf"\b{re.escape(str(key).lower())}\b", t):
            out.append((key, meta))
    return out


def _fetch_logo(domain, dest: Path):
    url = f"https://logo.clearbit.com/{domain}?size=512"
    try:
        r = requests.get(url, timeout=25, headers=UA)
        r.raise_for_status()
        if len(r.content) < 800:
            return False
        dest.write_bytes(r.content)
        img = Image.open(dest)
        img.verify()
        return True
    except Exception:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        return False


def make_tool_card(cfg, run, tool_key, tool_meta, scene_id, logger):
    """Broadcast-style product card: official logo + name + CTA ribbon."""
    from PIL import ImageDraw, ImageFilter, ImageFont

    W = int(cfg["visuals"]["width"])
    H = int(cfg["visuals"]["height"])
    rd = run["dir"] / "render"
    rd.mkdir(exist_ok=True)
    dest = rd / f"tool_{scene_id:03d}.png"
    if dest.exists():
        return dest

    img = Image.new("RGB", (W, H), (10, 14, 26))
    d = ImageDraw.Draw(img)
    for y in range(H):
        f = y / H
        d.line([(0, y), (W, y)],
               fill=(int(10 + 14 * f), int(16 + 22 * f), int(30 + 38 * f)))
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W * 0.22, H * 0.12, W * 0.78, H * 0.88), fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(150))
    tint = Image.new("RGB", (W, H), (20, 48, 96))
    img = Image.composite(tint, img, glow)
    d = ImageDraw.Draw(img)

    label = str(tool_meta.get("label") or tool_key)
    url = str(tool_meta.get("url") or "")
    domain = urllib.parse.urlparse(url).netloc or f"{tool_key}.com"

    logo_path = rd / f"logo_{tool_key}.png"
    logo = Image.open(logo_path).convert("RGBA") if \
        _fetch_logo(domain, logo_path) else None

    f_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/"
                                "DejaVuSans-Bold.ttf", 96)
    from .persona import FONT_XB, FONT_SB
    if FONT_XB.exists():
        f_name = ImageFont.truetype(str(FONT_XB), 96)
    f_chip = ImageFont.truetype(str(FONT_SB), 34) if FONT_SB.exists() \
        else f_name.font_variant(size=34)

    def fit(text, font, max_w):
        size = font.size
        f = font
        while size > 40:
            f = font.font_variant(size=size)
            if d.textlength(text, font=f) <= max_w:
                return f
            size -= 6
        return f

    name_f = fit(label.upper(), f_name, W * 0.52)
    nw = d.textlength(label.upper(), font=name_f)

    if logo is not None:
        box = 340
        logo.thumbnail((box, box), Image.LANCZOS)
        lx = (W - (box + 60 + nw)) // 2
        ly = H * 0.40 - logo.size[1] / 2
        img.paste(logo, (int(lx), int(ly)), logo)
        tx = lx + box + 60
    else:
        tx = (W - nw) / 2
    ty = H * 0.40 - name_f.size / 2
    d.text((tx, ty), label.upper(), font=name_f, fill=(244, 246, 250))

    chip = "OFFICIAL LINK IN THE DESCRIPTION"
    cw = d.textlength(chip, font=f_chip)
    cx = (W - cw) // 2 - 24
    cy = H * 0.58
    d.rounded_rectangle((cx, cy, cx + cw + 48, cy + f_chip.size + 30),
                        radius=12, fill=(255, 196, 0))
    d.text((cx + 24, cy + 15), chip, font=f_chip, fill=(14, 12, 4))

    badge = cfg.get("channel", {}).get("badge_text", "AI MONEY")
    f_b = f_chip
    bw = d.textlength(badge, font=f_b)
    d.rounded_rectangle((40, 36, 40 + bw + 36, 36 + f_b.size + 16),
                        radius=10, fill=(41, 118, 232))
    d.text((58, 44), badge, font=f_b, fill=(255, 255, 255))

    img.save(dest, "PNG")
    if logo_path.exists():
        logo_path.unlink()
    logger.info(f"tool card ready: {tool_key} -> {dest.name}")
    return dest


def assign_tool_cards(cfg, run, script, logger):
    """Give the money-angle scene of each story one branded tool card."""
    scenes = script.get("scenes") or []
    by_ch = {}
    for s in scenes:
        by_ch.setdefault(s.get("chapter"), []).append(s)
    made = 0
    for ch, ch_scenes in sorted(by_ch.items()):
        if not ch or ch >= 99:
            continue
        # scan from the END of the story (money angle lives there)
        for s in reversed(ch_scenes):
            hits = _match_tools(cfg, s.get("narration", ""))
            if not hits:
                continue
            key, meta = hits[0]
            card = make_tool_card(cfg, run, key, meta, s["id"], logger)
            s["tool_card_file"] = str(card)
            made += 1
            break
    logger.info(f"tool cards assigned: {made}")
    return made


def generate_all(cfg, run, script, logger):
    W, H = cfg["visuals"]["width"], cfg["visuals"]["height"]
    gl = "us"
    vis_dir = run["dir"] / "visuals"
    vis_dir.mkdir(exist_ok=True)
    scenes = script["scenes"]

    # ---- 1) build photo pools per chapter ----
    chapters = {}
    for s in scenes:
        chapters.setdefault(s["chapter"], []).append(s)

    queries = []
    for ch, ch_scenes in sorted(chapters.items()):
        seen = set()
        ch_q = []
        for s in ch_scenes:
            key = " ".join(_words(s["visual_query"]))
            if key and key not in seen:
                seen.add(key)
                ch_q.append(s["visual_query"])
            if len(ch_q) >= cfg["visuals"]["per_chapter_queries"]:
                break
        for q in ch_q:
            queries.append((ch, q))

    logger.info(f"searching {len(queries)} photo queries ...")
    pools = {ch: [] for ch in chapters}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(_search, q, cfg["visuals"]["search_count"], gl, logger)
                for ch, q in queries]
        for fut, (ch, q) in zip(futs, queries):
            try:
                pools[ch].extend(fut.result())
            except Exception:  # noqa: BLE001
                pass
    total_pool = sum(len(v) for v in pools.values())
    logger.info(f"photo pool: {total_pool} real images across {len(pools)} chapters")

    # ---- 2) assign + download per scene ----
    used = {ch: set() for ch in pools}

    def best_for(scene):
        ch = scene["chapter"]
        sw = _words(scene["visual_query"])
        pool = pools.get(ch) or [p for ps in pools.values() for p in ps]
        used_idx = used.get(ch, set())
        best, best_score = None, -1
        for i, p in enumerate(pool):
            if i in used_idx or not p.get("url"):
                continue
            score = len(sw & _words(p["query"])) + p.get("w", 0) / 100000.0
            if score > best_score:
                best, best_score = (i, p), score
        if best:
            used.setdefault(ch, set()).add(best[0])
            return best[1]["url"]
        return None

    def do_scene(scene):
        dest = vis_dir / f"scene_{scene['id']:03d}.jpg"
        if dest.exists():
            return True
        url = best_for(scene)
        if url:
            try:
                _download(url, dest, W, H)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"download failed scene {scene['id']}: {str(e)[:110]}")
        if cfg["visuals"]["ai_fallback"]:
            if _ai_fallback(scene["visual_query"], dest, W, H, logger):
                return True
        return False

    failed = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(do_scene, s): s for s in scenes}
        for fut in as_completed(futs):
            if not fut.result():
                failed.append(futs[fut]["id"])

    # last-resort: reuse any produced image
    produced = sorted(vis_dir.glob("scene_*.jpg"))
    if produced:
        for s in scenes:
            dest = vis_dir / f"scene_{s['id']:03d}.jpg"
            if not dest.exists():
                dest.write_bytes(produced[(s["id"] - 1) % len(produced)].read_bytes())

    for s in scenes:
        f = vis_dir / f"scene_{s['id']:03d}.jpg"
        s["image_file"] = str(f) if f.exists() else None

    if cfg["visuals"].get("tool_cards", True):
        try:
            assign_tool_cards(cfg, run, script, logger)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tool cards skipped: {str(e)[:140]}")

    logger.info(f"visuals ready: {len(produced)} images, failed={len(failed)}")
    return True

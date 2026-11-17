"""Scene visuals (portable edition): real stock photos first, free AI fallback.

Photo providers (chain, first configured wins):
  Pexels (PEXELS_API_KEY, free key) -> Pixabay (PIXABAY_API_KEY, free key)
  -> Openverse (no key, CC-licensed).

AI fallback: Pollinations image API (free, no key) renders a photorealistic
editorial photo when no real photo matches a scene.
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
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TrendPulse/1.0"}


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
def ai_image(prompt, dest: Path, width=1344, height=768, logger=None) -> bool:
    """Generate one photorealistic image via Pollinations (free, no key).

    Retries 3 times — the free endpoint occasionally answers 503."""
    import time
    base_url = ("https://image.pollinations.ai/prompt/"
                + urllib.parse.quote(prompt[:600])
                + f"?width={width}&height={height}&nologo=true&model=flux")
    last_err = None
    for attempt in range(1, 4):
        url = base_url + f"&seed={random.randint(0, 10**9)}"
        try:
            r = requests.get(url, timeout=240, headers=UA)
            r.raise_for_status()
            if len(r.content) < 15_000:
                raise ValueError(f"suspicious tiny image ({len(r.content)}B)")
            dest.write_bytes(r.content)
            img = Image.open(dest)
            img.verify()
            return True
        except Exception as e:  # noqa: BLE001
            last_err = e
            dest.unlink(missing_ok=True)
            if logger:
                logger.warning(f"ai_image attempt {attempt}/3 failed: "
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
    prompt = (f"{query}, editorial documentary photograph, realistic, 35mm film, "
              f"natural lighting, cinematic composition, muted colors, film grain, "
              f"high detail")
    tmp = dest.with_suffix(".ai.tmp")
    if ai_image(prompt, tmp, 1344, 768, logger):
        try:
            _normalize(tmp, dest, W, H)
            return True
        finally:
            tmp.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    return False


def _words(q):
    return set(re.findall(r"[a-z]{3,}", q.lower()))


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

    logger.info(f"visuals ready: {len(produced)} images, failed={len(failed)}")
    return True

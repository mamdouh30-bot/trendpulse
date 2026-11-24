"""Real stock FOOTAGE acquisition (moving B-roll) — the core realism upgrade.

Instead of stacking still photos, every scene gets a real moving video clip
downloaded from free providers and normalized to broadcast spec. Provider
chain (mixed for diversity — at least two providers are always tried):

  pexels    Pexels Videos API   (PEXELS_API_KEY — free key, best quality)
  pixabay   Pixabay Videos API  (PIXABAY_API_KEY — free key)
  mixkit    Mixkit library      (no key, direct MP4, free commercial license)
  coverr    Coverr library      (no key, direct MP4, free license)
  wikimedia Wikimedia Commons   (no key, CC licenses, real-world clips)
  nasa      NASA video library  (no key, public domain, labs/space/robots)

Every clip is normalized with ffmpeg: trimmed to a usable middle segment,
scaled, 30 fps, H.264, silent — then mapped to scenes as motion files that
the renderer cuts, slows and grades. Still photos (lib.visuals) remain the
fallback for any scene without footage.
"""
import hashlib
import json
import os
import re
import shutil
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .utils import load_env, probe_duration

PROJECT_ROOT = Path(__file__).resolve().parent.parent

UA = {"User-Agent":
      "TrendPulseBot/3.0 (https://github.com/mamdouh30-bot/trendpulse; "
      "trendpulse-bot@users.noreply.github.com) requests"}
SEARCH_TIMEOUT = 25
_DL_TIMEOUT = 90
_MAX_CLIP_MB = 80
_MIN_WIDTH = 960
_MIN_DUR = 3.0
_TAKE = 18.0            # max seconds cut from any source clip

BROWSER_UA = {"User-Agent":
              "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
_COVERR_BAD = ("premium", "staging", "paywall", "ai-generation")

# broad fallback searches when a specific query has no results
_GENERIC = [
    ("server|data center|rack", "server room data center"),
    ("stock|market|trading|finance|invest", "stock market trading"),
    ("robot|factory|manufactur", "robot arm factory automation"),
    ("city|street|skyline|traffic", "city traffic timelapse"),
    ("phone|mobile|app|screen|scroll", "smartphone hand screen"),
    ("typ|keyboard|laptop|coding|developer", "typing keyboard computer"),
    ("office|team|meeting|startup|worker", "modern office people working"),
    ("money|cash|dollar|bank|coin|crypto", "money finance banknotes"),
    ("ai|artificial|neural|chip|gpu|compute", "computer chip technology"),
    ("drone|military|army|weapon|war", "drone flying technology"),
    ("lab|research|scientist|medical|health", "science laboratory research"),
    ("earth|climate|weather|energy|solar", "earth aerial landscape"),
]
_LAST_RESORT = ["technology timelapse", "business people working",
                "city aerial timelapse"]

# Universal B-roll: queries with a guaranteed hit on every provider. When a
# chapter's own queries come back thin, these fill the gap so EVERY scene
# ships with real moving footage — no static image ever reaches the render.
UNIVERSAL_QUERIES = [
    "city aerial timelapse night", "data center server aisle",
    "stock market screens charts", "robot arm assembly line",
    "modern office people working", "technology abstract lights",
]

# PRESENTER v6 — real moving people for every host slot. The channel owner
# requires a natural MOVING presenter (anchor/host/reporter stock footage)
# on screen, never a still photo of a person.
PRESENTER_QUERIES = [
    "news anchor studio desk", "presenter talking to camera",
    "woman presenting camera studio", "man speaking to camera interview",
    "podcast host microphone talking", "reporter reporting camera street",
    "businesswoman presenting office", "tv studio host show",
]


def acquire_presenters(cfg, run, logger, want=8):
    """Fetch real MOVING presenter clips used for the fixed channel intro,
    story bridges, cutaways and outro (replaces all static persona frames).

    Clips land in outbox/<run>/motion/presenter_XX.mp4 and their URLs are
    recorded in the clip-history ledger so every episode gets NEW presenters
    and no clip ever repeats across episodes."""
    env = _env()
    fcfg = cfg.get("footage") or {}
    order = fcfg.get("providers",
                     ["pexels", "pixabay", "mixkit", "coverr",
                      "wikimedia", "nasa"])
    want = int(fcfg.get("presenter_clips", want))
    history = load_clip_history(cfg)
    motion_dir = run["dir"] / "motion"
    motion_dir.mkdir(parents=True, exist_ok=True)
    cache = run["dir"] / "footage" / "cache"
    cache.mkdir(parents=True, exist_ok=True)

    cands, seen = [], set()
    for q in PRESENTER_QUERIES:
        try:
            for c in search_pool(q, 6, env, order, logger):
                u = c.get("url")
                if u and u not in seen and u not in history:
                    seen.add(u)
                    cands.append(c)
        except Exception:  # noqa: BLE001
            pass
        if len(cands) >= want * 2:
            break
    if not cands:
        logger.warning("presenter pool: no fresh candidates found")
        return []

    got, url_map = [], {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_clip, c, cache, logger): c for c in cands}
        for f in as_completed(futs):
            try:
                p = f.result()
            except Exception:  # noqa: BLE001
                p = None
            if p:
                got.append(p)
                url_map[str(p)] = futs[f].get("url", "")
            if len(got) >= want:
                break

    presenters = []
    for i, p in enumerate(sorted(got), 1):
        dest = motion_dir / f"presenter_{i:02d}.mp4"
        try:
            shutil.copyfile(p, dest)
            presenters.append(dest)
        except Exception:  # noqa: BLE001
            pass
    if url_map:
        save_clip_history(cfg, set(url_map.values()))
    logger.info(f"presenter pool: {len(presenters)} moving host clips "
                f"(want {want}, candidates {len(cands)})")
    return presenters

# ---------------------------------------------------------------- history --
def load_clip_history(cfg):
    """URLs of clips used by PREVIOUS episodes (cross-run anti-repetition)."""
    path = PROJECT_ROOT / (cfg.get("footage", {}) or {}).get(
        "history_file", "state/clip_history.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except Exception:  # noqa: BLE001
        return set()


def save_clip_history(cfg, used_urls):
    path = PROJECT_ROOT / (cfg.get("footage", {}) or {}).get(
        "history_file", "state/clip_history.json")
    try:
        old = load_clip_history(cfg) if path.exists() else set()
        merged = sorted(set(used_urls) | old)
        max_keep = int((cfg.get("footage", {}) or {}).get(
            "history_max", 4000))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged[-max_keep:]), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _env() -> dict:
    env = load_env(PROJECT_ROOT / ".env")
    env.update({k: v for k, v in os.environ.items() if v})
    return env


def _words(q):
    return " ".join(re.findall(r"[a-zA-Z]{3,}", str(q).lower())[:6])


def _generic_for(query):
    q = str(query).lower()
    for pat, generic in _GENERIC:
        if re.search(pat, q):
            return generic
    return None


# ------------------------------------------------------------------ pexels ---
def _pexels(query, n, env):
    r = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": n, "orientation": "landscape"},
        headers={"Authorization": env["PEXELS_API_KEY"], **UA},
        timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        dur = float(v.get("duration") or 0)
        best = None
        for f in v.get("video_files", []):
            if f.get("file_type") != "video/mp4":
                continue
            w = int(f.get("width") or 0)
            if w < _MIN_WIDTH:
                continue
            if best is None or abs(w - 1920) < abs(int(best.get("width") or 0) - 1920):
                best = f
        if best and dur >= _MIN_DUR:
            out.append({"url": best["link"], "w": int(best.get("width") or 0),
                        "dur": dur, "provider": "pexels", "query": query})
    return out


# ----------------------------------------------------------------- pixabay ---
def _pixabay(query, n, env):
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": env["PIXABAY_API_KEY"], "q": query, "per_page": n,
                "video_type": "film", "safesearch": "true"},
        headers=UA, timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        dur = float(h.get("duration") or 0)
        vids = h.get("videos") or {}
        meta = vids.get("hd") or vids.get("sd") or vids.get("large")
        if meta and meta.get("url") and dur >= _MIN_DUR:
            out.append({"url": meta["url"],
                        "w": int(meta.get("width") or 0), "dur": dur,
                        "provider": "pixabay", "query": query})
    return out


# --------------------------------------------------------------- wikimedia ---
def _commons_transcoded(orig_url, tags=("480p.vp9.webm", "360p.webm")):
    """Wikimedia serves smaller transcoded derivatives at a md5-based path."""
    fname = urllib.parse.unquote(orig_url.rsplit("/", 1)[-1])
    h = hashlib.md5(fname.encode("utf-8")).hexdigest()
    base = ("https://upload.wikimedia.org/wikipedia/commons/transcoded/"
            f"{h[0]}/{h[:2]}/{urllib.parse.quote(fname)}")
    return [f"{base}/{urllib.parse.quote(fname)}.{t}" for t in tags]


def _wikimedia(query, n, env=None):
    r = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params={"action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"filetype:video {_words(query)}",
                "gsrnamespace": 6, "gsrlimit": 14,
                "prop": "imageinfo", "iiprop": "url|size|mime"},
        headers=UA, timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    pages = ((r.json().get("query") or {}).get("pages") or {})
    out = []

    def collect(api_pages):
        got = []
        for p in api_pages.values():
            for ii in (p.get("imageinfo") or []):
                mime = ii.get("mime", "")
                if mime not in ("video/webm", "video/mp4", "video/ogg"):
                    continue
                w = int(ii.get("width") or 0)
                size = int(ii.get("size") or 0)
                if w and w < _MIN_WIDTH:
                    continue
                if size > _MAX_CLIP_MB * 1_000_000:
                    continue
                dur = float(ii.get("duration") or 0)
                urls = _commons_transcoded(ii["url"]) + [ii["url"]]
                got.append({"url": urls[0], "alt_urls": urls,
                            "w": w, "dur": dur if dur >= _MIN_DUR else 0.0,
                            "provider": "wikimedia",
                            "page": p.get("title", ""), "query": query})
                break
        return got

    out = collect(pages)
    # second pass: the 2-3 core words only (Commons ranks by description
    # text, so long queries drift off-topic)
    core = " ".join([w for w in re.findall(r"[a-zA-Z]{3,}", query.lower())
                     if w not in ("the", "with", "closeup", "close", "shot",
                                  "moving", "rising", "working", "walking",
                                     "lights", "screen", "screens")][:3])
    if core and core != _words(query):
        try:
            r2 = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={"action": "query", "format": "json",
                        "generator": "search",
                        "gsrsearch": f"filetype:video {core}",
                        "gsrnamespace": 6, "gsrlimit": 10,
                        "prop": "imageinfo", "iiprop": "url|size|mime"},
                headers=UA, timeout=SEARCH_TIMEOUT)
            r2.raise_for_status()
            out.extend(collect((r2.json().get("query") or {}).get("pages")
                               or {}))
        except Exception:  # noqa: BLE001
            pass
    # de-dupe by title, b-roll length first
    seen, uniq = set(), []
    for c in out:
        if c["page"] not in seen:
            seen.add(c["page"])
            uniq.append(c)
    uniq.sort(key=lambda c: abs(c["dur"] - 20.0) if c["dur"] else 10_000)
    return uniq[:n]


# -------------------------------------------------------------------- nasa ---
def _nasa(query, n, env=None):
    r = requests.get(
        "https://images-api.nasa.gov/search",
        params={"q": _words(query), "media_type": "video", "page_size": 6},
        headers=UA, timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    items = ((r.json().get("collection") or {}).get("items") or [])
    out = []
    for item in items:
        data = (item.get("data") or [{}])[0]
        nid = data.get("nasa_id")
        if not nid:
            continue
        try:
            ar = requests.get(f"https://images-api.nasa.gov/asset/{nid}",
                              headers=UA, timeout=12).json()
        except Exception:  # noqa: BLE001
            continue
        hrefs = [i.get("href", "") for i in
                 ((ar.get("collection") or {}).get("items") or [])]
        mp4 = [h for h in hrefs if h.endswith(".mp4")]
        if not mp4:
            continue
        def _plain(h):
            h = h.replace("http://images-assets.nasa.gov",
                          "https://images-assets.nasa.gov", 1)
            return urllib.parse.unquote(h)
        prefer = [h for h in mp4 if "~medium" in h] or \
            [h for h in mp4 if "~large" in h] or mp4
        rest = [h for h in mp4 if h not in prefer]
        out.append({"url": _plain(prefer[0]),
                    "alt_urls": [_plain(h) for h in prefer] +
                                [_plain(h) for h in rest],
                    "w": 1280, "dur": 0.0, "provider": "nasa",
                    "page": data.get("title", ""), "query": query})
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ mixkit ---
def _mixkit(query, n, env=None):
    """Mixkit: free HD stock video, no key, direct MP4s (free license).

    Page URL: https://mixkit.co/free-stock-video/<tag>/ ; asset URLs look
    like https://assets.mixkit.co/videos/<id>/<id>-{360|720|1080}.mp4.
    Mixkit tag pages exist only for SHORT popular tags, so we try a ladder:
    first 3 words -> 2 words -> 1 word -> generic remap."""
    words = re.findall(r"[a-z0-9]+", _words(query))
    slugs = []
    for k in (3, 2, 1):
        if len(words) >= k:
            slug = "-".join(words[:k])
            if slug not in slugs:
                slugs.append(slug)
    g = _generic_for(str(query))
    if g:
        slug = "-".join(re.findall(r"[a-z0-9]+", g)[:2])
        if slug not in slugs:
            slugs.append(slug)
    if not slugs:
        slugs = ["technology"]
    out = []
    for slug in slugs[:4]:
        try:
            r = requests.get(
                f"https://mixkit.co/free-stock-video/{slug}/",
                headers=BROWSER_UA, timeout=SEARCH_TIMEOUT)
            if r.status_code >= 400:
                continue
        except Exception:  # noqa: BLE001
            continue
        ids = re.findall(
            r"assets\.mixkit\.co/videos/(\d{3,6})/\d{3,6}-360\.mp4",
            r.text)
        for vid in ids:
            if vid in {c["page"] for c in out}:
                continue
            base = f"https://assets.mixkit.co/videos/{vid}/{vid}"
            out.append({"url": f"{base}-720.mp4",
                        "alt_urls": [f"{base}-1080.mp4", f"{base}-360.mp4",
                                     f"{base}-720.mp4"],
                        "w": 1280, "dur": 0.0, "provider": "mixkit",
                        "page": vid, "query": query})
            if len(out) >= n:
                return out
        if out:
            return out
    return out


# ----------------------------------------------------------------- coverr ---
def _coverr(query, n, env=None):
    """Coverr: free HD stock video, no key, direct CDN MP4s (free license)."""
    q = urllib.parse.quote(_words(query))
    out = []
    try:
        r = requests.get(f"https://coverr.co/s?q={q}", headers=BROWSER_UA,
                         timeout=SEARCH_TIMEOUT)
        if r.status_code >= 400:
            return out
    except Exception:  # noqa: BLE001
        return out
    urls = re.findall(r'https://cdn\.coverr\.co/videos/[^"\'\s]+\.mp4',
                      r.text)
    seen = set()
    ranked = []
    for u in urls:
        if any(b in u for b in _COVERR_BAD):
            continue
        if u in seen:
            continue
        seen.add(u)
        pri = 0 if u.endswith("720p.mp4") else (1 if
              u.endswith("1080p.mp4") else 2)
        ranked.append((pri, u))
    ranked.sort()
    for _, u in ranked[:n]:
        fid = u.rsplit("/", 2)[-2]
        out.append({"url": u, "alt_urls": [u], "w": 1280, "dur": 0.0,
                    "provider": "coverr", "page": fid[:60],
                    "query": query})
    return out


# ------------------------------------------------------------------ search ---
def _providers(env, order):
    chain = []
    for name in order:
        if name == "pexels" and env.get("PEXELS_API_KEY"):
            chain.append(("pexels", _pexels))
        elif name == "pixabay" and env.get("PIXABAY_API_KEY"):
            chain.append(("pixabay", _pixabay))
        elif name == "mixkit":
            chain.append(("mixkit", _mixkit))
        elif name == "coverr":
            chain.append(("coverr", _coverr))
        elif name == "wikimedia":
            chain.append(("wikimedia", _wikimedia))
        elif name == "nasa":
            chain.append(("nasa", _nasa))
    return chain


def search_pool(query, n, cfg_env, order, logger=None):
    """Collect candidate clips for one query across the provider chain.

    Always tries at least two providers so every query mixes sources
    (one provider alone produced repetitive B-roll in practice)."""
    q = _words(query)
    pool, tried = [], {q}
    providers = _providers(cfg_env, order)
    hit_providers = 0
    for name, fn in providers:
        try:
            got = fn(q, n, cfg_env)
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.warning(f"footage '{name}' failed for '{q}': "
                               f"{str(e)[:110]}")
            got = []
        if got:
            hit_providers += 1
        pool.extend(got)
        # keep going until we have plenty of candidates from >=2 providers
        if len(pool) >= max(6, n) and hit_providers >= 2:
            break
        if len(pool) >= n and hit_providers >= 2:
            break
    # broaden: generic remap, then ONE last-resort topic (keep it fast)
    if len(pool) < 5:
        g = _generic_for(q)
        extras = ([g] if g else []) + _LAST_RESORT[:1]
        for extra in extras:
            if not extra or extra in tried:
                continue
            tried.add(extra)
            for name, fn in _providers(cfg_env, order):
                try:
                    got = fn(extra, n, cfg_env)
                except Exception:  # noqa: BLE001
                    got = []
                for c in got:
                    c["query"] = f"{query} [{extra}]"
                pool.extend(got)
                if len(pool) >= 6:
                    break
            if len(pool) >= 6:
                break
    return pool


# ------------------------------------------------- download + normalization --
def _download(url, dest, max_mb=_MAX_CLIP_MB, max_seconds=90):
    t0 = time.time()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        with requests.get(url, timeout=(20, 40), stream=True,
                          headers=UA) as r:
            r.raise_for_status()
            cl = int(r.headers.get("content-length") or 0)
            if cl > max_mb * 1_000_000:
                raise ValueError(f"too big ({cl // 1_000_000} MB)")
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 18):
                    done += len(chunk)
                    if done > max_mb * 1_000_000:
                        raise ValueError("too big while streaming")
                    if time.time() - t0 > max_seconds:
                        raise TimeoutError("download too slow — aborting")
                    f.write(chunk)
        tmp.replace(dest)
        return True
    except Exception:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return False


def _head_size(url):
    """Return content length, or None when unknown/dead.

    A missing content-length header (chunked responses, some CDNs) means
    UNKNOWN — the caller should still attempt the download. Only a real
    0-byte body is treated as dead."""
    try:
        h = requests.head(url, timeout=10, headers=UA, allow_redirects=True)
        if h.status_code >= 400:
            return None
        raw = h.headers.get("content-length")
        if raw is None:
            return -1                     # unknown size — try the download
        return int(raw)
    except Exception:  # noqa: BLE001
        return None


def _probe_w(path):
    from .utils import run_cmd
    try:
        out = run_cmd(["ffprobe", "-v", "quiet", "-print_format", "json",
                       "-show_streams", "-show_format", str(path)],
                      timeout=60, retries=1)
        meta = json.loads(out or "{}")
        vs = next((s for s in meta.get("streams", [])
                   if s.get("codec_type") == "video"), {})
        return int(vs.get("width") or 0), float(
            meta.get("format", {}).get("duration") or 0)
    except Exception:  # noqa: BLE001
        return 0, 0.0


def _normalize(src: Path, dest: Path, logger) -> bool:
    """Cut a usable middle segment, scale, 30fps H.264, silent."""
    w, dur = _probe_w(src)
    if w < 640 or dur < _MIN_DUR:
        return False
    take = min(_TAKE, dur)
    skip = min(0.30 * dur, max(0.0, dur - take)) if dur > take + 1.5 else 0.0
    vf = "scale='min(1920,iw)':-2,setsar=1,fps=30"
    cmd = ["ffmpeg", "-y", "-ss", f"{skip:.2f}", "-i", str(src),
           "-t", f"{take:.2f}", "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-pix_fmt", "yuv420p", str(dest)]
    from .utils import run_cmd
    try:
        run_cmd(cmd, timeout=600, retries=1, logger=logger)
    except Exception:  # noqa: BLE001
        return False
    if not dest.exists() or dest.stat().st_size < 100_000:
        return False
    dw, ddur = _probe_w(dest)
    if dw < 640 or ddur < 2.5:
        dest.unlink(missing_ok=True)
        return False
    dest.with_suffix(".json").write_text(json.dumps(
        {"dur": ddur, "w": dw, "src_dur": dur}), encoding="utf-8")
    return True


def fetch_clip(cand, cache: Path, logger):
    """Download + normalize one candidate. Returns Path or None."""
    key = hashlib.sha1(
        f"{cand.get('page') or ''}|{cand['url']}".encode()).hexdigest()[:16]
    dest = cache / f"clip_{key}.mp4"
    if dest.exists() and dest.with_suffix(".json").exists():
        return dest
    raw = cache / f"raw_{key}.mp4"
    urls = [cand["url"]] + (cand.get("alt_urls") or [])
    try:
        for u in urls:
            size = _head_size(u)
            if size is not None and size >= 0 and size > 60 * 1_000_000:
                continue          # avoid multi-minute downloads
            if size == 0:
                continue          # dead/empty URL (real 0-byte body)
            if _download(u, raw):
                if _normalize(raw, dest, logger):
                    return dest
                raw.unlink(missing_ok=True)
    finally:
        raw.unlink(missing_ok=True)
    dest.unlink(missing_ok=True)
    return None


# ------------------------------------------------------------------ assign ---
def assign_footage(cfg, run, script, logger):
    """Give every scene real moving footage: motion/motion_<id>.mp4."""
    mcfg = cfg.get("motion") or {}
    fcfg = cfg.get("footage") or {}
    if os.environ.get("TRENDPULSE_DISABLE_FOOTAGE") == "1" \
            or not mcfg.get("enabled", True):
        logger.info("footage disabled by config — Ken Burns stills only")
        return False
    env = _env()
    order = fcfg.get("providers",
                     ["pexels", "pixabay", "mixkit", "coverr",
                      "wikimedia", "nasa"])
    per_ch = int(fcfg.get("per_chapter_clips", 12))
    per_q = int(fcfg.get("per_query_results", 8))
    history = load_clip_history(cfg)
    if history:
        logger.info(f"clip history: {len(history)} previously-used URLs "
                    f"will be avoided")
    scenes = script.get("scenes") or []
    if not scenes:
        return False

    root = run["dir"] / "footage"
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    motion_dir = run["dir"] / "motion"
    motion_dir.mkdir(parents=True, exist_ok=True)

    chapters = {}
    for s in scenes:
        chapters.setdefault(s.get("chapter", 0), []).append(s)

    providers_used = set()
    pool_by_ch = {}
    for ch, ch_scenes in sorted(chapters.items()):
        queries, seen = [], set()
        for s in ch_scenes:
            q = _words(s.get("visual_query", ""))
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
            if len(queries) >= 6:
                break
        pool = []
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(search_pool, q, per_q, env, order, logger)
                    for q in queries]
            for f in futs:
                try:
                    pool.extend(f.result())
                except Exception:  # noqa: BLE001
                    pass
        # de-dupe by url
        uniq, seen_u = [], set()
        for c in pool:
            u = c["url"]
            if u not in seen_u:
                seen_u.add(u)
                uniq.append(c)
        fresh = [c for c in uniq if c["url"] not in history]
        if len(uniq) and not fresh:
            # whole pool already used by earlier episodes — better a repeat
            # than a static image; relax completely for this chapter
            logger.warning(f"chapter {ch}: all {len(uniq)} candidates used "
                           f"before — relaxing history filter")
            fresh = uniq
        elif len(fresh) < max(4, per_ch // 2) and len(uniq) > len(fresh):
            # keep some used ones as backfill, fresh first
            backfill = [c for c in uniq if c["url"] in history]
            fresh = fresh + backfill[: max(2, per_ch // 4)]
        pool_by_ch[ch] = fresh
        logger.info(f"footage pool chapter {ch}: {len(fresh)} candidates "
                    f"({len(uniq)} found, "
                    f"{len(uniq) - len([c for c in uniq if c['url'] not in history])} "
                    f"filtered by history, {len(queries)} queries)")

    def dl_batch(cands, want, url_map):
        got, fails = [], {"dead": 0}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(fetch_clip, c, cache, logger): c
                    for c in cands[: want * 3]}
            for f in as_completed(futs):
                try:
                    p = f.result()
                except Exception:  # noqa: BLE001
                    p = None
                if p:
                    got.append(p)
                    url_map[str(p)] = futs[f].get("url", "")
                    providers_used.add(futs[f].get("provider", ""))
                else:
                    fails["dead"] += 1
                if len(got) >= want:
                    break
        if fails["dead"] and not got:
            logger.warning(f"dl_batch: {fails['dead']} candidates all failed")
        return got

    clips_by_ch = {}
    url_map = {}                     # downloaded path -> source URL
    for ch, pool in pool_by_ch.items():
        clips_by_ch[ch] = dl_batch(pool, per_ch, url_map) if pool else []

    # merge: any chapter whose downloaded pool is smaller than its scene
    # count borrows from the other chapters so no scene repeats one clip
    all_clips = []
    seen_clips = set()
    for ch in sorted(clips_by_ch):
        for p in clips_by_ch[ch]:
            if p not in seen_clips:
                seen_clips.add(p)
                all_clips.append(p)
    for ch in sorted(chapters):
        clips = clips_by_ch.get(ch) or []
        need = len(chapters[ch])
        if 0 < len(clips) < need:
            extra = [p for p in all_clips if p not in clips]
            clips_by_ch[ch] = clips + extra[: need - len(clips)]
        elif not clips:
            clips_by_ch[ch] = list(all_clips)

    # ---- universal B-roll guarantee: EVERY scene gets moving footage ----
    total_scenes = sum(len(v) for v in chapters.values())
    if len(all_clips) < total_scenes:
        want_u = min(24, total_scenes - len(all_clips) + 6)
        logger.warning(f"pool thin ({len(all_clips)}/{total_scenes}) — "
                       f"fetching {want_u} universal B-roll clips")
        u_pool = []
        seen_uq = set()
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(search_pool, q, per_q, env, order, logger)
                    for q in UNIVERSAL_QUERIES]
            for f in futs:
                try:
                    for c in f.result():
                        if c["url"] not in seen_uq:
                            seen_uq.add(c["url"])
                            u_pool.append(c)
                except Exception:  # noqa: BLE001
                    pass
        u_fresh = [c for c in u_pool if c["url"] not in history] or u_pool
        got_u = dl_batch(u_fresh, want_u, url_map)
        for p in got_u:
            if p not in seen_clips:
                seen_clips.add(p)
                all_clips.append(p)
        for ch in sorted(chapters):
            clips = clips_by_ch.get(ch) or []
            need = len(chapters[ch])
            extra = [p for p in all_clips if p not in clips]
            if len(clips) < need:
                clips_by_ch[ch] = clips + extra[: need - len(clips)]
            elif not clips:
                clips_by_ch[ch] = list(all_clips)
        logger.info(f"universal pool added — total {len(all_clips)} clips")

    # rotation with distinct-neighbor guarantee: consecutive scenes must
    # never share the same clip when the pool has more than one
    global_i = 0
    assigned = 0
    for ch, ch_scenes in sorted(chapters.items()):
        clips = clips_by_ch.get(ch) or []
        if not clips:
            continue
        if len(clips) > 1:
            # deterministic per-chapter rotation offset
            shift = (int(ch) * 7 + 3) % len(clips)
            clips = clips[shift:] + clips[:shift]
        for s in ch_scenes:
            cur = s.get("motion_file")
            if cur and not Path(cur).exists():
                s["motion_file"] = None      # dead reference after a resume
            cur_b = s.get("motion_file_b")
            if cur_b and not Path(cur_b).exists():
                s["motion_file_b"] = None
            if not s.get("motion_file"):
                p = clips[global_i % len(clips)]
                if len(clips) > 1:
                    for _ in range(len(clips)):
                        prev = str(s.get("_prev_motion") or "")
                        if str(p) != prev:
                            break
                        global_i += 1
                        p = clips[global_i % len(clips)]
                s["_prev_motion"] = str(p)
                s["motion_file"] = str(p)
                s["motion_skip_frac"] = round(
                    ((s["id"] * 0.6180339887) % 1.0) * 0.40, 3)
                global_i += 1
                assigned += 1
            # second shot for long scenes (the renderer splits long scenes)
            if len(clips) >= 2 and not s.get("motion_file_b"):
                p2 = clips[(global_i + 1) % len(clips)]
                if p2 != s.get("motion_file"):
                    s["motion_file_b"] = str(p2)
        logger.info(f"chapter {ch}: footage for "
                    f"{sum(1 for s in ch_scenes if s.get('motion_file'))}"
                    f"/{len(ch_scenes)} scenes from {len(set(str(s.get('motion_file')) for s in ch_scenes))} unique clips")

    (root / "credits.json").write_text(json.dumps(
        {"providers": sorted(providers_used),
         "credit": "Stock footage: Mixkit / Coverr / Pexels / Pixabay / "
                   "Wikimedia Commons contributors (CC) / "
                   "NASA (public domain) — free licenses"},
        ensure_ascii=False), encoding="utf-8")
    logger.info(f"footage assigned to {assigned}/{len(scenes)} scenes "
                f"(providers: {sorted(providers_used) or 'none'})")

    # record what this episode used, so future episodes avoid these clips
    used_urls = set()
    for s in scenes:
        for k in ("motion_file", "motion_file_b"):
            p = s.get(k)
            if p and Path(p).exists():
                u = url_map.get(str(p))
                if u:
                    used_urls.add(u)
    if used_urls:
        save_clip_history(cfg, used_urls)
        logger.info(f"clip history updated: +{len(used_urls)} URLs")
    return assigned > 0

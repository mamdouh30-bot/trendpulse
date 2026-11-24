"""GitHub Releases backlog: episodes survive a dead YouTube token.

When youtube.upload fails (expired/revoked OAuth refresh token), the fully
rendered episode is normally lost with the ephemeral runner. This module
stores video + metadata + thumbnail as assets of the `pending-episodes`
release, and re-publishes them automatically (drain) as soon as the token
works again — so no episode is ever lost and the channel catches up.
"""
import json
import os
import tempfile
from pathlib import Path

import requests

from . import youtube

TAG = "pending-episodes"
_API = "https://api.github.com/repos/{repo}"


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or "mamdouh30-bot/trendpulse"


def _token():
    for k in ("GITHUB_TOKEN", "TRENDPULSE_GH_TOKEN", "GH_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v
    return None


def _headers(auth=True):
    h = {"Accept": "application/vnd.github+json"}
    tok = _token() if auth else None
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _get_release():
    # always authed: unauthenticated API reads can hit 403 rate limits,
    # and the Actions GITHUB_TOKEN / local token are always available
    r = requests.get(f"{_API.format(repo=_repo())}/releases/tags/{TAG}",
                     headers=_headers(), timeout=30)
    return r.json() if r.status_code == 200 else None


def _ensure_release(logger):
    rel = _get_release()
    if rel:
        return rel
    r = requests.post(f"{_API.format(repo=_repo())}/releases",
                      headers=_headers(), timeout=30,
                      json={"tag_name": TAG,
                            "name": "Pending episodes (awaiting YouTube "
                                    "token renewal)",
                            "body": "Fully rendered episodes whose YouTube "
                                    "upload failed because the OAuth token "
                                    "was expired/revoked. They are published "
                                    "automatically once the token is "
                                    "renewed and a new cycle starts.",
                            "draft": False, "prerelease": True})
    if r.status_code in (200, 201):
        return r.json()
    return _get_release()  # concurrent-run race


def save_pending(video_path, thumb_path, meta, run_name, logger) -> bool:
    """Store one finished episode (video + meta + thumb) as release assets."""
    rel = _ensure_release(logger)
    if not rel:
        logger.warning("backlog: could not create/find pending-episodes "
                       "release — episode NOT saved")
        return False
    ok = True
    payload_meta = dict(meta or {})
    payload_meta["_run"] = run_name
    files = (
        (f"{run_name}_video.mp4", "video/mp4", Path(video_path)),
        (f"{run_name}_meta.json", "application/json", None),
        (f"{run_name}_thumb.jpg", "image/jpeg", Path(thumb_path)),
    )
    for fname, ctype, src in files:
        try:
            url = rel["upload_url"].replace(
                "{?name,label}", f"?name={fname}")
            if src is not None:
                if not Path(src).exists():
                    continue
                data = Path(src).read_bytes()
            else:
                data = json.dumps(payload_meta, ensure_ascii=False).encode()
            r = requests.post(url, headers={**_headers(), "Content-Type": ctype},
                              data=data, timeout=1800)
            ok = ok and r.status_code == 201
        except Exception as e:  # noqa: BLE001
            ok = False
            logger.warning(f"backlog asset {fname} failed: {str(e)[:140]}")
    logger.info(f"backlog save {'OK' if ok else 'PARTIAL'}: {run_name} "
                f"-> release '{TAG}'")
    return ok


def drain_pending(cfg, logger) -> int:
    """Re-upload every stored episode; delete the assets that succeed."""
    rel = _get_release()
    if not rel:
        return 0
    assets = rel.get("assets") or []
    groups = {}
    for a in assets:
        n = a["name"]
        if n.endswith("_video.mp4"):
            groups.setdefault(n[:-len("_video.mp4")], {})["video"] = a
        elif n.endswith("_meta.json"):
            groups.setdefault(n[:-len("_meta.json")], {})["meta"] = a
        elif n.endswith("_thumb.jpg"):
            groups.setdefault(n[:-len("_thumb.jpg")], {})["thumb"] = a
    if not groups:
        return 0
    logger.info(f"backlog drain: {len(groups)} pending episode(s) found")
    published = 0
    with tempfile.TemporaryDirectory() as td:
        for run_name, g in sorted(groups.items()):
            if "video" not in g or "meta" not in g:
                continue
            try:
                vpath = Path(td) / f"{run_name}_video.mp4"
                mpath = Path(td) / f"{run_name}_meta.json"
                tpath = Path(td) / f"{run_name}_thumb.jpg"
                _download(g["video"]["browser_download_url"], vpath)
                _download(g["meta"]["browser_download_url"], mpath)
                meta = json.loads(mpath.read_text(encoding="utf-8"))
                if "thumb" in g:
                    _download(g["thumb"]["browser_download_url"], tpath)
                result = youtube.upload(vpath,
                                        tpath if tpath.exists() else None,
                                        meta, cfg, logger)
                if result.get("uploaded"):
                    published += 1
                    _delete_assets(g.values(), logger)
                    logger.info(f"backlog: published {run_name} "
                                f"({result.get('video_id')})")
                else:
                    logger.warning(f"backlog: {run_name} still blocked: "
                                   f"{str(result.get('reason'))[:120]}")
                    break  # token still dead — don't churn the rest
            except Exception as e:  # noqa: BLE001
                logger.warning(f"backlog drain {run_name} failed: "
                               f"{str(e)[:140]}")
                break
    return published


def _download(url, dest: Path):
    # auth header is dropped automatically by requests on the cross-host
    # redirect to the signed S3 download URL
    with requests.get(url, headers=_headers(), stream=True,
                      timeout=1800) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def _delete_assets(assets, logger):
    for a in assets:
        try:
            r = requests.delete(
                f"{_API.format(repo=_repo())}/releases/assets/{a['id']}",
                headers=_headers(), timeout=60)
            if r.status_code not in (204, 200, 404):
                logger.warning(f"backlog asset delete {a['name']}: "
                               f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"backlog asset delete {a['name']}: "
                           f"{str(e)[:100]}")

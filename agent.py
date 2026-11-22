#!/usr/bin/env python3
"""
TrendPulse — Autonomous YouTube Documentary Agency
==================================================
Full pipeline: trends -> script -> voice -> visuals -> motion -> render
-> thumbnail -> metadata -> publish. Resumable via state.json.

Usage:
  python3 agent.py --full          # complete production run (default)
  python3 agent.py --mini          # fast end-to-end smoke test
  python3 agent.py --resume outbox/run_XXXX
  python3 agent.py --full --skip-motion
"""
import argparse
import datetime as dt
import json
import re
import shutil
import sys
import traceback
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import llm, metadata, motion, renderer, scriptwriter, thumbnail  # noqa: E402
from lib import trends, visuals, voiceover, youtube  # noqa: E402
from lib.utils import load_state, save_state, setup_logger  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent


def load_cfg():
    with open(PROJECT_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def new_run_dir():
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "outbox" / f"run_{ts}"


def finish_run(run, script, meta, final_path, total, upload_result, logger):
    out = {
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "topic": run.get("topic", {}).get("title", ""),
        "title": meta["title"] if meta else (script or {}).get("title", ""),
        "video_file": str(final_path) if final_path else None,
        "duration_minutes": round(total / 60, 1) if total else None,
        "thumbnail": str(run["dir"] / "thumbnail.jpg"),
        "upload": upload_result or {"uploaded": False, "reason": "not attempted"},
        "size_mb": round(final_path.stat().st_size / 1e6, 1) if final_path else None,
    }
    save_state(run["dir"] / "summary.json", out)

    hist_path = PROJECT_ROOT / "state" / "history.json"
    hist = load_state(hist_path, default=[])
    hist.append({
        "date": out["finished_at"],
        "topic": out["topic"],
        "title": out["title"],
        "file": out["video_file"],
        "video_id": (upload_result or {}).get("video_id"),
    })
    save_state(hist_path, hist[-500:])

    rep = run["dir"] / "REPORT.txt"
    rep.write_text(
        "TRENDPULSE PRODUCTION REPORT\n"
        "=" * 42 + "\n"
        f"Topic      : {out['topic']}\n"
        f"Title      : {out['title']}\n"
        f"Duration   : {out['duration_minutes']} min\n"
        f"Video      : {out['video_file']}\n"
        f"Thumbnail  : {out['thumbnail']}\n"
        f"Size       : {out['size_mb']} MB\n"
        f"Upload     : {json.dumps(out['upload'], ensure_ascii=False)}\n",
        encoding="utf-8")
    logger.info("run summary written")
    return out


def publish_only(args, cfg):
    """Upload an already-completed run (e.g. one that waited for credentials)."""
    run_dir = Path(args.publish)
    if not run_dir.is_absolute():
        run_dir = (PROJECT_ROOT / run_dir).resolve()
    final = run_dir / "render" / "final.mp4"
    if not final.exists():
        print(f"no final.mp4 in {run_dir}")
        return 1
    logger = setup_logger(run_dir)
    meta = load_state(run_dir / "metadata.json") or {}
    thumb = run_dir / "thumbnail.jpg"
    if not meta:
        state = load_state(run_dir / "state.json", default={})
        meta = metadata.fallback_meta(state.get("script") or {},
                                      state.get("topic") or {},
                                      {"summary": "", "sources": []})
    logger.info(f"=== publish-only mode for {run_dir.name} ===")
    result = youtube.upload(final, thumb if thumb.exists() else None,
                            meta, cfg, logger)
    # patch summary.json + history
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        summary = load_state(summary_path)
        summary["upload"] = result
        save_state(summary_path, summary)
    hist_path = PROJECT_ROOT / "state" / "history.json"
    hist = load_state(hist_path, default=[])
    for h in reversed(hist):
        if h.get("file") == str(final):
            h["video_id"] = result.get("video_id")
            break
    save_state(hist_path, hist[-500:])
    logger.info(f"publish-only done: {json.dumps(result)[:300]}")
    return 0 if result.get("uploaded") else 1


def run_pipeline(args):
    cfg = load_cfg()
    if args.resume:
        run_dir = Path(args.resume)
        if not run_dir.is_absolute():
            run_dir = (PROJECT_ROOT / run_dir).resolve()
        state = load_state(run_dir / "state.json", default={})
    else:
        run_dir = new_run_dir()
        state = {}
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(run_dir)
    run = {"dir": run_dir, "project_root": PROJECT_ROOT, "cfg": cfg}
    logger.info(f"=== TrendPulse run started ({'MINI' if args.mini else 'FULL'}) "
                f"-> {run_dir} ===")

    stages = ["trends", "script", "voice", "visuals", "motion",
              "render", "thumbnail", "metadata", "publish"]
    if args.mini:
        stages = ["script", "voice", "visuals", "render", "thumbnail",
                  "metadata", "publish"]

    script, meta, final_path, total, upload_result = state.get("script"), None, None, None, None

    try:
        # ---------------- trends + research ----------------
        if "trends" in stages and not state.get("trends_done"):
            topic = trends.select_topic(cfg, run, logger)
            facts = trends.research(cfg, run, topic, logger)
            run["topic"] = topic
            state["topic"] = topic
            state["trends_done"] = True
            save_state(run_dir / "state.json", state)
        else:
            topic = state.get("topic") or {"title": "mini test topic"}
            facts = load_state(run_dir / "research" / "facts.json",
                               default={"summary": "test", "sources": []})

        # ---------------- script ----------------
        if not state.get("script_done"):
            if args.mini:
                from lib.scriptwriter import MINI_SCRIPT
                script = json.loads(json.dumps(MINI_SCRIPT))
            else:
                script = scriptwriter.write_script(cfg, run, topic, facts, logger)
            (run_dir / "script").mkdir(exist_ok=True)
            save_state(run_dir / "script" / "script.json", script)
            state["script_done"] = True
            save_state(run_dir / "state.json", state)
        else:
            script = load_state(run_dir / "script" / "script.json")
        if not script.get("scenes"):
            raise RuntimeError("script has no scenes")
        logger.info(f"script: {len(script['scenes'])} scenes")

        # resume guard: if most footage files vanished (fresh runner),
        # re-run the motion stage instead of crashing in the renderer
        if state.get("motion_done"):
            have = sum(1 for s in script["scenes"]
                       if s.get("motion_file") and Path(s["motion_file"]).exists())
            if have < max(1, len(script["scenes"]) // 2):
                logger.warning(f"footage missing on resume ({have}/"
                               f"{len(script['scenes'])}) — re-running motion")
                state["motion_done"] = False

        # resume guard: voice wavs are gitignored (too heavy for the repo);
        # when a crashed run is resumed on a fresh runner they are gone —
        # re-run the (free) TTS stage instead of KeyError-ing in the renderer
        if state.get("voice_done"):
            vdir = run_dir / "voice"
            have = sum(1 for s in script["scenes"]
                       if (vdir / f"scene_{s['id']:03d}.wav").exists())
            if have < len(script["scenes"]):
                logger.warning(f"voice files missing on resume ({have}/"
                               f"{len(script['scenes'])}) — re-running voiceover")
                state["voice_done"] = False

        # ---------------- voiceover ----------------
        if not state.get("voice_done"):
            voiceover.generate_all(cfg, run, script, logger)
            state["voice_done"] = True
            save_state(run_dir / "state.json", state)

        # ---------------- visuals ----------------
        if not state.get("visuals_done"):
            visuals.generate_all(cfg, run, script, logger)
            state["visuals_done"] = True
            save_state(run_dir / "state.json", state)

        # ---------------- motion (real B-roll footage) ----------------
        if not args.skip_motion and not state.get("motion_done"):
            motion.generate_clips(cfg, run, script, logger)
            state["motion_done"] = True
            save_state(run_dir / "state.json", state)

        # ---------------- render ----------------
        if not state.get("render_done"):
            final_path, total = renderer.render_video(cfg, run, script, logger)
            state["render_done"] = True
            save_state(run_dir / "state.json", state)
        else:
            final_path = run_dir / "render" / "final.mp4"
            total = None

        # ---------------- thumbnail + metadata ----------------
        if not state.get("meta_done"):
            meta = metadata.generate(cfg, run, script, topic, facts, logger)
            state["meta_done"] = True
            save_state(run_dir / "state.json", state)
        else:
            meta = load_state(run_dir / "metadata.json")
        if not meta:
            meta = metadata.fallback_meta(script, topic, facts)

        if not (run_dir / "thumbnail.jpg").exists():
            thumbnail.make_thumbnail(cfg, run, meta, logger)

        # ---------------- publish ----------------
        upload_result = None
        if not args.mini and cfg["upload"]["enabled"]:
            # duplicate-publish guard: never upload a title that went live
            # already (local history within 36h OR live on the channel).
            # Covers the resume race that once published one episode twice.
            _dupe = False
            _t_norm = re.sub(r"[^a-z0-9]+", " ",
                             str((meta or {}).get("title") or "").lower()).strip()
            if _t_norm:
                _now = dt.datetime.now(dt.timezone.utc)
                for h in load_state(PROJECT_ROOT / "state" / "history.json",
                                    default=[]):
                    try:
                        d = dt.datetime.fromisoformat(
                            str(h.get("date")))
                        if d.tzinfo is None:
                            d = d.replace(tzinfo=dt.timezone.utc)
                        if (_now - d).total_seconds() > 36 * 3600:
                            continue
                    except Exception:  # noqa: BLE001
                        continue
                    h_norm = re.sub(r"[^a-z0-9]+", " ",
                                    str(h.get("title") or "").lower()).strip()
                    if h_norm and h_norm == _t_norm:
                        _dupe = True
                        logger.warning("DUPLICATE title in local history "
                                       f"({h.get('date')}) — upload blocked")
                        break
                if not _dupe:
                    _dupe = youtube.find_recent_duplicate(
                        (meta or {}).get("title", ""), logger)
                    if _dupe:
                        logger.warning("DUPLICATE title live on channel — "
                                       "upload blocked")
            if _dupe:
                upload_result = {"uploaded": False,
                                 "reason": "duplicate-title guard — publish "
                                           "skipped (video kept in outbox)"}
            else:
                upload_result = youtube.upload(
                    final_path, run_dir / "thumbnail.jpg", meta, cfg, logger)
                if not upload_result.get("uploaded"):
                    logger.warning("video kept in outbox for manual upload")
        state["published"] = upload_result
        save_state(run_dir / "state.json", state)

        run["topic"] = state.get("topic", topic if 'topic' in dir() else {})
        summary = finish_run(run, script, meta, final_path, total, upload_result, logger)
        logger.info(f"=== RUN COMPLETE: {json.dumps(summary, ensure_ascii=False)[:400]} ===")
        return 0

    except Exception as e:  # noqa: BLE001
        logger.error(f"RUN FAILED at stage: {traceback.format_exc(limit=3)}")
        save_state(run_dir / "state.json", state)
        return 1


def main():
    ap = argparse.ArgumentParser(description="TrendPulse autonomous producer")
    ap.add_argument("--full", action="store_true", help="full production run")
    ap.add_argument("--mini", action="store_true", help="fast smoke test")
    ap.add_argument("--resume", help="resume a previous run directory")
    ap.add_argument("--publish", metavar="RUN_DIR",
                    help="upload an existing completed run only")
    ap.add_argument("--skip-motion", action="store_true",
                    help="disable AI motion clips this run")
    args = ap.parse_args()
    if args.publish:
        sys.exit(publish_only(args, load_cfg()))
    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()

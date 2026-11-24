#!/usr/bin/env python3
"""One autonomous production cycle for free hosting.

Finds the newest unfinished run in outbox/ and resumes it; if every run is
finished (or there is none), starts a fresh full production run — unless the
last publish is fresher than the 12-hour cadence (extra cron slots are only
catch-up opportunities, they must never double the output). Designed to be
called by GitHub Actions, Docker scheduler or plain cron — safe to re-run any
time thanks to state.json checkpoints.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# normal cadence = every 12 hours; slots that fire earlier than this after a
# successful publish skip the fresh run (they still resume unfinished runs)
MIN_PUBLISH_INTERVAL_H = 10.0


def run_is_finished(run_dir: Path) -> bool:
    """Finished = summary.json exists (pipeline reached the end)
    or state explicitly marks a successful upload."""
    if (run_dir / "summary.json").exists():
        return True
    st = run_dir / "state.json"
    if not st.exists():
        return False
    try:
        state = json.loads(st.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    pub = state.get("published") or {}
    return bool(pub.get("uploaded"))


def pick_resume_target():
    outbox = ROOT / "outbox"
    if not outbox.exists():
        return None
    runs = sorted((d for d in outbox.iterdir()
                   if d.is_dir() and d.name.startswith("run_")),
                  key=lambda d: d.name, reverse=True)
    for d in runs:
        if not run_is_finished(d):
            return d
    return None


def _parse_ts(raw) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(str(raw))
    except Exception:  # noqa: BLE001
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def last_finished_at() -> dt.datetime | None:
    """Newest *successful publish* timestamp across outbox summaries +
    state/history.json.

    CRITICAL: only cycles whose upload actually succeeded count. Dead-token
    cycles used to count here too, which re-armed the cadence guard after
    every failed slot and silenced production for 10+ hours per slot —
    exactly the 'agent stopped publishing' outage of Nov 23-24."""
    newest = None
    outbox = ROOT / "outbox"
    if outbox.exists():
        for sj in outbox.glob("run_*/summary.json"):
            try:
                data = json.loads(sj.read_text(encoding="utf-8"))
                up = data.get("upload") or {}
                ok = bool(up.get("uploaded")) and bool(up.get("video_id"))
                d = _parse_ts(data.get("finished_at")) if ok else None
            except Exception:  # noqa: BLE001
                d = None
            if d and (not newest or d > newest):
                newest = d
    hist_path = ROOT / "state" / "history.json"
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            hist = []
        for h in reversed(hist or []):
            # a history entry counts as a publish only when a video id exists
            d = _parse_ts(h.get("date")) if h.get("video_id") else None
            if d and (not newest or d > newest):
                newest = d
    return newest


def main() -> int:
    target = pick_resume_target()
    if target:
        mode = ["--resume", str(target.relative_to(ROOT))]
        print(f"[run_cycle] resuming unfinished run: {target.name}", flush=True)
    else:
        lf = last_finished_at()
        if lf:
            hours = (dt.datetime.now(dt.timezone.utc) - lf).total_seconds() / 3600
            if hours < MIN_PUBLISH_INTERVAL_H:
                print(f"[run_cycle] cadence guard: last publish {hours:.1f}h ago "
                      f"< {MIN_PUBLISH_INTERVAL_H}h — nothing to do this slot",
                      flush=True)
                return 0
        mode = ["--full"]
        print("[run_cycle] no unfinished run — starting a fresh FULL run",
              flush=True)
    try:
        rc = subprocess.run([sys.executable, "agent.py", *mode],
                            cwd=ROOT).returncode
    except KeyboardInterrupt:
        print("[run_cycle] interrupted", flush=True)
        return 1
    print(f"[run_cycle] agent exit code: {rc}", flush=True)
    # propagate: the workflow retries failed cycles (self-heal) and the
    # commit step still runs because it is guarded with if: always()
    return rc


if __name__ == "__main__":
    sys.exit(main())

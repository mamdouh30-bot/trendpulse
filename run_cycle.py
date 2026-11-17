#!/usr/bin/env python3
"""One autonomous production cycle for free hosting.

Finds the newest unfinished run in outbox/ and resumes it; if every run is
finished (or there is none), starts a fresh full production run. Designed to
be called by GitHub Actions, Docker scheduler or plain cron — safe to re-run
any time thanks to state.json checkpoints.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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


def main() -> int:
    target = pick_resume_target()
    if target:
        mode = ["--resume", str(target.relative_to(ROOT))]
        print(f"[run_cycle] resuming unfinished run: {target.name}", flush=True)
    else:
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
    # never crash the caller: the commit step still needs to push checkpoints
    return 0


if __name__ == "__main__":
    sys.exit(main())

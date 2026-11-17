"""Shared utilities: logging, shell runner, state, JSON extraction."""
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def setup_logger(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger("trendpulse")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


class RateLimited(RuntimeError):
    """Raised when the upstream API answers 429 Too Many Requests."""


def run_cmd(cmd, timeout=600, retries=1, logger=None, quiet=False, backoff=3):
    """Run a subprocess command with retries + progressive backoff (handles 429s)."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if r.returncode != 0:
                stderr = r.stderr[-300:]
                if "429" in stderr or "Too many requests" in stderr:
                    raise RateLimited(f"429: {' '.join(map(str, cmd))[:120]}")
                raise RuntimeError(
                    f"cmd failed ({r.returncode}): {' '.join(map(str, cmd))[:180]}\n"
                    f"stderr: {stderr}"
                )
            return r.stdout
        except RateLimited as e:
            last_err = e
            if logger:
                logger.warning(f"rate limited (attempt {attempt}/{retries}), "
                               f"waiting {backoff * attempt}s ...")
            if attempt < retries:
                time.sleep(backoff * attempt)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if logger:
                logger.warning(f"cmd attempt {attempt}/{retries} failed: {str(e)[:200]}")
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last_err


def load_state(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return default if default is not None else {}


def save_state(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_json(text: str):
    """Robustly pull the first valid JSON object/array out of an LLM answer."""
    if not text:
        raise ValueError("empty text")
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth, instr, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
            else:
                if ch == '"':
                    instr = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except Exception:  # noqa: BLE001
                            break
    raise ValueError(f"no valid JSON found in: {text[:200]}")


def probe_duration(path: Path) -> float:
    out = run_cmd(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], timeout=60
    )
    return float(out.strip().splitlines()[0])


def ff_time(seconds: float) -> str:
    return f"{seconds:.3f}"


def safe_name(text: str, maxlen=60) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_]+", "-", text).lower()
    return text[:maxlen].rstrip("-") or "untitled"


def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

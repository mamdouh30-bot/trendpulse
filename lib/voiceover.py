"""Narration voiceover (portable edition): ElevenLabs -> edge-tts fallback.

Provider decision (per video, kept consistent across all scenes):
  cfg.voice.provider = auto | elevenlabs | edge
  auto  -> probe ElevenLabs with a tiny TTS call; use it only if the key
           has text_to_speech permission AND enough remaining quota.
  Any per-scene ElevenLabs failure falls back to edge-tts (Microsoft neural
  voices, free, no key) automatically, so a quota exhaustion or outage can
  never stall a production run.
"""
import asyncio
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .utils import load_env, probe_duration, run_cmd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EDGE_VOICE = "en-US-AndrewMultilingualNeural"   # upbeat US male host


def _env() -> dict:
    import os
    env = load_env(PROJECT_ROOT / ".env")
    env.update({k: v for k, v in os.environ.items() if v})
    return env


# ----------------------------------------------------------------- ElevenLabs
def _el_cfg(cfg):
    el = dict(cfg.get("voice", {}).get("elevenlabs") or {})
    el.setdefault("voice_id", "pNInz6obpgDQGcFmaJgB")   # Adam, deep US male
    el.setdefault("model", "eleven_multilingual_v2")
    el.setdefault("stability", 0.45)
    el.setdefault("similarity_boost", 0.75)
    el.setdefault("style", 0.35)
    return el


def _el_key():
    return (_env().get("ELEVENLABS_API_KEY") or "").strip()


def _el_tts(key, el, text, out_mp3, logger=None):
    """Single TTS call. Retries once without `speed` on 422; backs off on 429."""
    settings = {"stability": el["stability"],
                "similarity_boost": el["similarity_boost"],
                "style": el["style"]}
    if el.get("speed"):
        settings["speed"] = float(el["speed"])

    def post(body, timeout=240):
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{el['voice_id']}",
            data=json.dumps(body).encode(),
            headers={"xi-api-key": key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:180]}")

    payload = {"text": text, "model_id": el["model"],
               "voice_settings": settings}
    for attempt in range(3):
        try:
            audio = post(payload)
            out_mp3.write_bytes(audio)
            return len(audio)
        except RuntimeError as e:
            msg = str(e)
            if "422" in msg and "speed" in payload.get("voice_settings", {}):
                payload["voice_settings"].pop("speed")   # model without speed
                continue
            if "429" in msg and attempt < 2:             # rate limited
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise RuntimeError("el TTS retries exhausted")


def _el_quota_ok(key):
    """Best-effort quota check; unknown quota (401 on user_read) = assume OK."""
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        remaining = d.get("character_limit", 0) - d.get("character_count", 0)
        return remaining
    except Exception:  # noqa: BLE001
        return None


def _probe_el(key, el, logger):
    """Tiny real call to prove the key has text_to_speech permission."""
    try:
        tmp = Path("/tmp/el_probe.mp3")
        n = _el_tts(key, el, "This is a voice test.", tmp, logger)
        return n > 1000
    except Exception as e:  # noqa: BLE001
        logger.warning(f"elevenlabs probe failed: {str(e)[:150]}")
        return False


# ------------------------------------------------------------------ edge TTS
def _mp3_to_wav(mp3: Path, wav: Path, logger):
    run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
             "-ar", "44100", "-ac", "1", "-sample_fmt", "s16", str(wav)],
            timeout=120, retries=2, logger=logger)


def _edge_tts(text, out_wav: Path, speed: float, logger=None):
    """Microsoft Edge neural TTS (free, no key). speed 1.0 -> rate +0%."""
    import edge_tts

    voice = _env().get("TRENDPULSE_EDGE_VOICE") or DEFAULT_EDGE_VOICE
    pct = int(round((float(speed) - 1.0) * 100))
    rate = f"{pct:+d}%"

    mp3 = out_wav.with_suffix(".edge.mp3")

    async def _synth():
        com = edge_tts.Communicate(text, voice, rate=rate)
        await com.save(str(mp3))

    last_err = None
    for attempt in range(1, 4):
        try:
            asyncio.run(_synth())
            if mp3.exists() and mp3.stat().st_size > 5000:
                _mp3_to_wav(mp3, out_wav, logger)
                mp3.unlink(missing_ok=True)
                return True
            raise RuntimeError("empty edge-tts output")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if logger:
                logger.warning(f"edge-tts attempt {attempt}/3 failed: "
                               f"{str(e)[:150]}")
            time.sleep(8 * attempt)
    mp3.unlink(missing_ok=True)
    raise RuntimeError(f"edge-tts failed: {str(last_err)[:200]}")


def _one_edge(scene, voice_dir, speed, logger):
    out = voice_dir / f"scene_{scene['id']:03d}.wav"
    if out.exists() and out.stat().st_size > 20000:
        return True
    text = scene["narration"].replace('"', "'")
    try:
        _edge_tts(text, out, speed, logger)
        dur = probe_duration(out)
        if dur < 0.8:
            raise RuntimeError(f"suspicious duration {dur:.2f}s")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"edge voice scene {scene['id']} failed: {str(e)[:150]}")
        out.unlink(missing_ok=True)
        return False


# ------------------------------------------------------------------ ElevenLabs per scene
def _one_el(scene, voice_dir, key, el, logger):
    wav = voice_dir / f"scene_{scene['id']:03d}.wav"
    if wav.exists() and wav.stat().st_size > 20000:
        return True
    mp3 = voice_dir / f"scene_{scene['id']:03d}.mp3"
    text = scene["narration"].replace('"', "'")
    try:
        _el_tts(key, el, text, mp3, logger)
        _mp3_to_wav(mp3, wav, logger)
        mp3.unlink(missing_ok=True)
        dur = probe_duration(wav)
        if dur < 0.8:
            raise RuntimeError(f"suspicious duration {dur:.2f}s")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"el voice scene {scene['id']} failed: {str(e)[:150]}")
        mp3.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)
        return False


# ------------------------------------------------------------------ main API
def generate_all(cfg, run, script, logger):
    voice_dir = run["dir"] / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    scenes = script["scenes"]
    speed = cfg["voice"].get("speed", 0.95)

    provider = cfg.get("voice", {}).get("provider", "auto")
    key = _el_key()
    el = _el_cfg(cfg)
    use_el = False
    if provider in ("auto", "elevenlabs"):
        if not key:
            logger.info("voice provider: edge-tts (no ELEVENLABS_API_KEY)")
        else:
            # all-or-nothing: never mix two voices inside one video
            needed = int(sum(len(s.get("narration", "")) for s in scenes)
                         * 1.15) + 200
            remaining = _el_quota_ok(key)
            if remaining is None:
                use_el = _probe_el(key, el, logger)
                if use_el:
                    logger.info("voice provider: ELEVENLABS (probe passed, "
                                "quota unknown)")
            elif remaining < needed:
                logger.info(f"voice provider: edge-tts (elevenlabs has "
                            f"{remaining} chars, full video needs ~{needed})")
            elif _probe_el(key, el, logger):
                use_el = True
                logger.info(f"voice provider: ELEVENLABS "
                            f"(remaining {remaining} >= needed {needed})")
            else:
                logger.info("voice provider: edge-tts (elevenlabs probe failed)")
    else:
        logger.info("voice provider: edge-tts (config)")

    ok, failed = 0, []
    workers = 2 if use_el else 4
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for s in scenes:
            if use_el:
                futs[ex.submit(_one_el, s, voice_dir, key, el, logger)] = s
            else:
                futs[ex.submit(_one_edge, s, voice_dir, speed, logger)] = s
        for fut in as_completed(futs):
            s = futs[fut]
            if fut.result():
                ok += 1
            else:
                failed.append(s["id"])

    # retry round for failures -> falls back to the free edge voice
    for sid in list(failed):
        s = next(x for x in scenes if x["id"] == sid)
        if _one_edge(s, voice_dir, speed, logger):
            failed.remove(sid)
            ok += 1
            logger.info(f"scene {sid}: edge-tts fallback voice used")

    for s in scenes:
        f = voice_dir / f"scene_{s['id']:03d}.wav"
        if f.exists():
            s["voice_file"] = str(f)
            s["voice_dur"] = probe_duration(f)
        else:
            s["voice_file"] = None
            s["voice_dur"] = 3.0
            if s["id"] not in failed:
                failed.append(s["id"])

    if failed:
        raise RuntimeError(f"voiceover failed for scenes: {failed}")
    total = sum(s["voice_dur"] for s in scenes)
    logger.info(f"voiceover complete: {len(scenes)} segments, "
                f"{total/60:.1f} min audio")
    return True

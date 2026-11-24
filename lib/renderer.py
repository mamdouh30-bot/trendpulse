"""Broadcast renderer: host persona frames, Ken Burns scenes, tool cards,
xfade assembly, real bundled music + SFX, sidechain ducking, pro loudness.

Set TRENDPULSE_FAST_RENDER=1 (default on CI) to use faster x264 presets —
recommended on 2-core free hosting runners."""
import datetime as dt
import json
import os
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import audio as fx
from . import persona as host
from .utils import probe_duration, run_cmd

FAST = os.environ.get("TRENDPULSE_FAST_RENDER") == "1"
CLIP_PRESET = "veryfast" if FAST else "fast"
CLIP_CRF = "21" if FAST else "18"
FINAL_PRESET = "faster" if FAST else "medium"
FINAL_CRF = "22" if FAST else "19"

FONT_XB = host.FONT_XB
FONT_B = host.FONT_B
FONT_SB = host.FONT_SB
FONT_R = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
GRADE = "eq=contrast=1.05:saturation=1.06,vignette=PI/4.6,noise=alls=6:allf=t+u"
# Stills (host frames / brand cards) skip temporal noise: on 2-core CI the
# noise pass over a zoompanned still is the #1 cause of render timeouts.
GRADE_STILL = "eq=contrast=1.05:saturation=1.06,vignette=PI/4.6"


def _font(path, size):
    if Path(path).exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


# ------------------------------------------------------------ intro/outro --
def make_intro_card(run, cfg, title, logger):
    """Brand sting card: AI MONEY DIGEST + date."""
    W = int(cfg["visuals"]["width"])
    H = int(cfg["visuals"]["height"])
    today = dt.date.today().strftime("%B %-d, %Y") if hasattr(dt, "locale") \
        else dt.date.today().strftime("%B %d").replace(" 0", " ")
    img = host.brand_card(
        cfg, W, H,
        kicker=cfg["channel"]["badge_text"],
        title="AI Money Digest",
        sub=f"THE AI STORIES THAT MATTER — {today.upper()}")
    out = run["dir"] / "render"
    out.mkdir(exist_ok=True)
    dest = out / "card_intro.png"
    img.save(dest, quality=92)
    logger.info("intro brand card drawn")
    return dest


def make_outro_card(run, cfg, logger):
    """End brand card: subscribe reminder."""
    W = int(cfg["visuals"]["width"])
    H = int(cfg["visuals"]["height"])
    img = host.brand_card(
        cfg, W, H,
        kicker=cfg["channel"]["badge_text"],
        title="See You In 12 Hours",
        sub="SUBSCRIBE — NEW AI DIGEST EVERY 12 HOURS",
        accent=(232, 29, 38))
    out = run["dir"] / "render"
    out.mkdir(exist_ok=True)
    dest = out / "card_outro.png"
    img.save(dest, quality=92)
    logger.info("outro brand card drawn")
    return dest


def make_subscribe_banner(run, cfg, logger):
    """Animated subscribe banner (PIL) overlaid on the intro/outro host
    frames; lib.renderer slides it up inside the ffmpeg overlay filter."""
    rd = run["dir"] / "render"
    rd.mkdir(exist_ok=True)
    dest = rd / "banner_subscribe.png"
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    H = 168
    f_main = _font(FONT_XB, 54)
    f_ch = _font(FONT_XB, 40)
    f_sub = _font(FONT_B, 22)
    sub_txt = "NEW DIGEST EVERY 12 HOURS"
    # measure on a scratch canvas, then size the banner to fit the text
    scratch = ImageDraw.Draw(Image.new("RGBA", (1200, H)))
    sub_w = scratch.textlength(sub_txt, font=f_sub)
    W = max(760, int(488 + max(scratch.textlength("AI MONEY", font=f_ch),
                               sub_w) + 26))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # translucent dark backing for legibility on any footage
    d.rounded_rectangle((0, 0, W, H), radius=22, fill=(10, 10, 14, 185))
    # red subscribe button
    bx0, by0, bx1, by1 = 22, 26, 462, 142
    d.rounded_rectangle((bx0, by0, bx1, by1), radius=16,
                        fill=(232, 29, 38, 255))
    tw = d.textlength("SUBSCRIBE", font=f_main)
    d.text(((bx0 + bx1 - tw) / 2, (by0 + by1 - f_main.size) / 2 + 2),
           "SUBSCRIBE", font=f_main, fill=(255, 255, 255, 255))
    # channel block
    d.text((486, 44), cfg["channel"]["name"].upper(), font=f_ch,
           fill=(255, 255, 255, 255))
    d.text((488, 104), sub_txt, font=f_sub,
           fill=(210, 210, 218, 255))
    img.save(dest)
    logger.info("subscribe banner drawn")
    return dest


def make_host_frames(cfg, run, script, logger):
    """Pre-compose every persona frame used by the cut (bridge/cutaway/outro)."""
    W = int(cfg["visuals"]["width"])
    H = int(cfg["visuals"]["height"])
    rd = run["dir"] / "render"
    rd.mkdir(exist_ok=True)
    today = dt.date.today().strftime("%b %d").replace(" 0", " ")
    ep_label = f"EP — {today.upper()}"
    made = {}

    def compose(dest_name, shot_file, **kw):
        dest = rd / dest_name
        if not dest.exists():
            frame = host.compose_host_frame(cfg, shot_file, W, H, **kw)
            frame.save(dest, quality=92)
        made[dest_name] = dest
        return dest

    # episode intro: host at the desk + episode title lower third
    intro_shot = host.shot_path(cfg, "intro")
    if intro_shot:
        compose("host_intro.png", intro_shot,
                headline=script["title"], chip_text=None,
                episode_label=ep_label)

    # one bridge frame per story chapter
    bridge_idx = 0
    for ch in sorted({s.get("chapter", 0) for s in script["scenes"]}):
        if ch in (0, 99):
            continue
        shot = host.shot_path(cfg, "bridge", bridge_idx)
        bridge_idx += 1
        if not shot:
            continue
        ch_title = ""
        for c in script.get("chapters", []):
            if c.get("num") == ch:
                ch_title = c.get("title", "")
                break
        compose(f"host_ch{ch}.png", shot,
                headline=ch_title or f"Story {ch}",
                chip_text=f"STORY {ch} — {cfg['persona']['name'].upper()}",
                episode_label=ep_label)

    # mid-story cutaway frame
    cut_shot = host.shot_path(cfg, "cutaway")
    if cut_shot:
        compose("host_cutaway.png", cut_shot, corner_badge=True)

    # outro: host + subscribe CTA
    outro_shot = host.shot_path(cfg, "outro")
    if outro_shot:
        compose("host_outro.png", outro_shot,
                cta="SUBSCRIBE", corner_badge=True,
                episode_label=ep_label)
    logger.info(f"host frames composed: {len(made)}")
    return made


# ------------------------------------------------------------ scene renderer
def _drawtext_filter(entry, dur, fps):
    scene = entry.get("scene") or {}
    if entry.get("_no_chapter_text") or scene.get("_host_frame") \
            or scene.get("chapter") in (0, 99) or not scene.get("chapter_title"):
        return None
    tf = entry.get("_chapter_tf")
    if not tf:
        return None
    end = max(2.0, dur - 1.4)
    return (
        f"drawtext=fontfile={FONT_B}:textfile={tf}:fontcolor=white@0.94:"
        f"fontsize=42:borderw=2:bordercolor=black@0.7:box=1:boxcolor=black@0.45:"
        f"boxborderw=16:x=(w-text_w)/2:y=h-140:enable='between(t,1.2,{end:.1f})'"
    )


def _smoothstep(p):
    """Smoothstep easing expression for ffmpeg (cubic ease in/out)."""
    return f"({p}*{p}*(3-2*{p}))"


KB_CYCLE = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up"]


def _kb_filter(variant, dur, fps):
    """Ken Burns variants: eased zoom in/out + directional pans (eased).

    All variants keep max zoom 1.12 so a 1.7x upscaled source stays
    pixel-safe — and the eased motion reads far more cinematic than a
    constant-rate zoom."""
    tf = max(1.0, dur * fps)
    prog = f"(on/{tf:.2f})"
    ease = _smoothstep(prog)
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"
    drift = 0.085
    if variant == "pan_right":
        return f"z='1.12':x='{cx}+iw/zoom*{drift}*{ease}':y='{cy}'"
    if variant == "pan_left":
        return f"z='1.12':x='{cx}-iw/zoom*{drift}*{ease}':y='{cy}'"
    if variant == "pan_up":
        return f"z='1.12':x='{cx}':y='{cy}-ih/zoom*0.06*{ease}'"
    if variant == "zoom_out":
        return f"z='max(1.02+0.10*{ease},1.0)':x='{cx}':y='{cy}'"
    return f"z='min(1.10-0.10*{ease},1.11)':x='{cx}':y='{cy}'"


def _overlay_filter(entry):
    """Subscribe banner slide-up overlay expression (needs banner input)."""
    if not entry.get("overlay_banner"):
        return None
    return ("overlay=x='(main_w-overlay_w)/2':"
            "y='main_h-overlay_h-64+(1-min(1,max(0,(t-0.5)/0.6)))"
            "*(overlay_h+140)':enable='gte(t,0.45)'")


def render_scene_clip(scene, entry, cfg, run, logger):
    """Render one scene entry to an intermediate mp4 (no audio)."""
    fps = int(cfg["visuals"].get("fps", 30))
    rd = run["dir"] / "render"
    rd.mkdir(exist_ok=True)
    dest = rd / f"clip_{entry['key']}.mp4"
    done = dest.with_suffix(".done")
    # integrity: a runner killed mid-render leaves a half-written mp4 that
    # passes a size check — only trust clips with a completed marker
    if dest.exists() and done.exists() and dest.stat().st_size > 50_000:
        return dest
    dest.unlink(missing_ok=True)
    done.unlink(missing_ok=True)
    dur = entry["dur"]
    banner = rd / "banner_subscribe.png"
    use_overlay = bool(entry.get("overlay_banner") and banner.exists())

    if entry.get("motion_file"):
        mdur = probe_duration(Path(entry["motion_file"]))
        Wv = int(cfg["visuals"]["width"])
        Hv = int(cfg["visuals"]["height"])
        base = (f"scale={Wv}:{Hv}:force_original_aspect_ratio=increase,"
                f"crop={Wv}:{Hv},fps={fps},setsar=1")
        if mdur > dur + 0.05:
            # clip longer than the scene — start at a varied offset, trim
            skip = float(entry.get("motion_skip_frac") or 0.0) * \
                max(0.0, mdur - dur)
            vf = f"{base},{GRADE}"
            cmd = ["ffmpeg", "-y", "-ss", f"{skip:.3f}", "-i", entry["file"],
                   "-filter_complex", vf, "-t", f"{dur:.3f}"]
        else:
            # clip shorter than the scene — slow it down, hold the last frame
            ratio = dur / max(0.5, mdur)
            deficit = max(0.0, dur - mdur * ratio)
            pad = f"tpad=stop_mode=clone:stop_duration={deficit + 1.0:.2f},"
            vf = f"{base},setpts={ratio:.5f}*PTS,{pad}{GRADE}"
            cmd = ["ffmpeg", "-y", "-i", entry["file"],
                   "-filter_complex", vf, "-t", f"{dur:.3f}"]
    else:
        # 1.25x upscale: max KB zoom (1.12) + pan drift stay pixel-safe while
        # costing ~half the pixels of the old 1.7x path on CI runners
        W5 = int(cfg["visuals"]["width"] * 1.25)
        H5 = int(cfg["visuals"]["height"] * 1.25)
        kb = _kb_filter(entry.get("kb", "zoom_in"), dur, fps)
        vf = (
            f"scale={W5}:{H5},"
            f"zoompan={kb}:"
            f"d=1:s={cfg['visuals']['width']}x{cfg['visuals']['height']}:"
            f"fps={fps},{GRADE_STILL}"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-t",
               f"{dur:.3f}", "-i", entry["file"], "-filter_complex", vf]

    ovl = _overlay_filter(entry) if use_overlay else None
    if ovl:
        # banner becomes input #1; label the base chain then composite.
        # the banner MUST be capped (-framerate/-t): an infinite looped
        # input stalls framesync against the fixed-length main input and
        # was the root cause of the CI render timeouts
        vf = f"[0:v]{vf}[bg];[bg][1:v]{ovl}"
        cmd.insert(cmd.index("-i", 1) + 2, "-framerate")
        cmd.insert(cmd.index("-i", 1) + 3, str(fps))
        cmd.insert(cmd.index("-i", 1) + 4, "-t")
        cmd.insert(cmd.index("-i", 1) + 5, f"{dur:.3f}")
        cmd.insert(cmd.index("-i", 1) + 6, "-loop")
        cmd.insert(cmd.index("-i", 1) + 7, "1")
        cmd.insert(cmd.index("-i", 1) + 8, "-i")
        cmd.insert(cmd.index("-i", 1) + 9, str(banner))
    dtf = _drawtext_filter(entry, dur, fps)
    if dtf:
        vf += "," + dtf
    vf += ",format=yuv420p"
    cmd[cmd.index("-filter_complex") + 1] = vf
    cmd += ["-c:v", "libx264", "-preset", CLIP_PRESET, "-crf", CLIP_CRF,
            "-pix_fmt", "yuv420p", "-an", str(dest)]
    try:
        run_cmd(cmd, timeout=900, retries=2, logger=logger)
    except Exception:  # noqa: BLE001
        # one clip must never kill the whole episode: fall back to a cheap
        # chain (1:1 scale + gentle zoom, no grade/noise) that still MOVES
        logger.warning(f"clip {entry['key']} failed — rendering LITE fallback")
        lite = _lite_clip_cmd(entry, dest, dur, fps, cfg, banner,
                              use_overlay)
        run_cmd(lite, timeout=900, retries=1, logger=logger)
    done.touch()  # render completed — cache-safe on resume
    return dest


def _lite_clip_cmd(entry, dest, dur, fps, cfg, banner, use_overlay):
    """Emergency low-cost command for a scene the full chain cannot render.

    Stills keep a slow zoom (never a frozen frame); motion clips keep the
    real footage with just a scale/crop. No grade, no noise, no easing."""
    W = int(cfg["visuals"]["width"])
    H = int(cfg["visuals"]["height"])
    frames = max(1.0, dur * fps)
    if entry.get("motion_file"):
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},fps={fps},setsar=1")
        cmd = ["ffmpeg", "-y", "-ss", "0", "-i", entry["file"],
               "-filter_complex", vf, "-t", f"{dur:.3f}"]
    else:
        kb = (f"z='min(1.03+0.05*(on/{frames:.2f}),1.08)':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},zoompan={kb}:d=1:s={W}x{H}:fps={fps}")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-t",
               f"{dur:.3f}", "-i", entry["file"], "-filter_complex", vf]
    if use_overlay and banner and Path(banner).exists():
        vf = (f"[0:v]{vf}[bg];[bg][1:v]"
              "overlay=x='(main_w-overlay_w)/2':y='main_h-overlay_h-64'")
        cmd += ["-framerate", str(fps), "-t", f"{dur:.3f}",
                "-loop", "1", "-i", str(banner)]
    cmd[cmd.index("-filter_complex") + 1] = vf + ",format=yuv420p"
    cmd += ["-c:v", "libx264", "-preset", CLIP_PRESET, "-crf", CLIP_CRF,
            "-pix_fmt", "yuv420p", "-an", str(dest)]
    return cmd


# ------------------------------------------------------------- audio mixing
def _read_wav_mono(path, target_sr):
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
    if sr == target_sr:
        return data
    t_old = np.arange(len(data)) / sr
    t_new = np.arange(int(len(data) * target_sr / sr)) / target_sr
    return np.interp(t_new, t_old, data).astype(np.float32)


def build_mix(cfg, run, timeline, logger):
    """Narration at exact scene starts; ducked music + sfx; write mix.wav."""
    rd = run["dir"] / "render"
    total = timeline["total"]
    sr = 44100
    n = int(total * sr)
    voice_track = np.zeros(n, dtype=np.float32)
    for e in timeline["entries"]:
        if e.get("voice_file"):
            seg = _read_wav_mono(e["voice_file"], sr)
            i0 = int((e["start"] + 0.30) * sr)
            i1 = min(n, i0 + len(seg))
            if i0 < n:
                voice_track[i0:i1] += seg[: i1 - i0]
    peak = np.max(np.abs(voice_track)) + 1e-9
    voice_track *= 0.82 / peak

    music = fx.load_audio(rd / "music.wav", sr=sr)  # (n,2) float
    if len(music) < n:
        music = np.tile(music, (int(np.ceil(n / max(1, len(music)))), 1))[:n]
    else:
        music = music[:n]

    duck_db = float(cfg.get("music", {}).get("duck_db", 9.0))
    if duck_db > 0:
        music, voice_track = fx.apply_duck(music, voice_track,
                                           max_db=duck_db)

    sfx = _read_wav_mono(rd / "sfx.wav", sr)
    sfx = np.resize(sfx, n) if len(sfx) < n else sfx[:n]
    sfx_stereo = np.stack([sfx, np.roll(sfx, int(0.010 * sr))], axis=1)

    mix = voice_track[:n, None] + music + sfx_stereo
    mix = np.tanh(mix * 1.1) * 0.92  # soft limiter
    mono = mix.mean(axis=1)
    dest = rd / "mix.wav"
    pcm = (np.clip(mono, -0.98, 0.98) * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    logger.info(f"audio mix written ({total/60:.1f} min, duck {duck_db} dB)")
    return dest


# ------------------------------------------------------------- final cut
def render_video(cfg, run, script, logger):
    W, H = cfg["visuals"]["width"], cfg["visuals"]["height"]
    fps = int(cfg["visuals"].get("fps", 30))
    rd = run["dir"] / "render"
    xfade = cfg["content"]["xfade"]
    pad = cfg["content"]["scene_pad"]
    cut_every = int(cfg["content"].get("host_cutaway_every", 7))

    intro_img = make_intro_card(run, cfg, script["title"], logger)
    outro_img = make_outro_card(run, cfg, logger)
    has_fixed_intro = any(s.get("is_fixed_intro") for s in script["scenes"])
    if has_fixed_intro:
        make_subscribe_banner(run, cfg, logger)

    voice_dir = run["dir"] / "voice"
    vis_dir = run["dir"] / "visuals"
    motion_dir = run["dir"] / "motion"
    motion_pool = (sorted(p for p in motion_dir.glob("motion_*.mp4"))
                   if motion_dir.exists() else [])

    # PRESENTER v6: real MOVING presenter clips (anchor/host/reporter stock
    # footage) replace every static persona frame — a person on screen must
    # always be a live video, never a photo (hard channel requirement).
    presenter_pool = (sorted(p for p in motion_dir.glob("presenter_*.mp4"))
                      if motion_dir.exists() else [])
    pres_i = 0

    def next_presenter():
        nonlocal pres_i
        if presenter_pool:
            p = presenter_pool[pres_i % len(presenter_pool)]
            pres_i += 1
            return p
        return None

    def assign_moving(entry, s):
        """Fill a host slot with a MOVING visual: presenter clip first,
        then generic b-roll, then the branded graphic card (text art, not
        a photo) as the absolute last resort — a static person is forbidden."""
        p = next_presenter()
        if p:
            entry["file"] = str(p)
            entry["motion_file"] = str(p)
            return True
        if motion_pool:
            pick = motion_pool[(s["id"] * 3 + 1) % len(motion_pool)]
            entry["file"] = str(pick)
            entry["motion_file"] = str(pick)
            entry["motion_skip_frac"] = round(
                ((s["id"] * 0.5757) % 1.0) * 0.40, 3)
            return True
        entry["file"] = str(intro_img)
        return False

    entries = []
    entries.append({"key": "card0", "file": str(intro_img), "dur": 3.0,
                    "voice_file": None, "is_card": True})

    # when no FIXED intro scene exists, open with a 4.6s moving presenter
    # beat instead of the old silent still
    if not has_fixed_intro:
        p = next_presenter()
        if p:
            entries.append({"key": "hostin", "file": str(p),
                            "motion_file": str(p), "dur": 4.6,
                            "voice_file": None, "is_card": True,
                            "is_host": True})
    prev_chapter = 0
    kb_i = 0
    last_scene_hosted = False
    for s in script["scenes"]:
        # self-heal asset references when resuming from checkpoints
        if not s.get("voice_file"):
            vf = voice_dir / f"scene_{s['id']:03d}.wav"
            if vf.exists():
                s["voice_file"] = str(vf)
                s["voice_dur"] = probe_duration(vf)
        if not s.get("image_file"):
            imf = vis_dir / f"scene_{s['id']:03d}.jpg"
            if imf.exists():
                s["image_file"] = str(imf)
        if not s.get("motion_file"):
            mf = motion_dir / f"motion_{s['id']:03d}.mp4"
            if mf.exists():
                s["motion_file"] = str(mf)
        # dead media references after a resume on a fresh runner
        if s.get("motion_file") and not Path(s["motion_file"]).exists():
            s["motion_file"] = None
        if s.get("motion_file_b") and not Path(s["motion_file_b"]).exists():
            s["motion_file_b"] = None
        if s.get("image_file") and not Path(s["image_file"]).exists():
            s["image_file"] = None

        dur = max(3.0, s.get("voice_dur", 0) + pad)
        ch = s.get("chapter", 0)
        entry = {"key": f"s{s['id']:03d}", "dur": dur,
                 "voice_file": s.get("voice_file"), "scene": s}

        # 0) FIXED channel intro: a MOVING presenter + subscribe banner
        if s.get("is_fixed_intro"):
            s["_host_frame"] = True
            entry["overlay_banner"] = True
            entry["is_host"] = True
            assign_moving(entry, s)
        # 1) story bridge: first scene of each story shows the presenter
        elif ch not in (0, 99) and ch != prev_chapter:
            s["_host_frame"] = True
            entry["is_host"] = True
            assign_moving(entry, s)
        # 2) tool card beats b-roll on money-angle scenes
        elif s.get("tool_card_file") and Path(s["tool_card_file"]).exists():
            entry["file"] = s["tool_card_file"]
        # 3) mid-story presenter cutaway
        elif s["id"] % max(2, cut_every) == 0:
            s["_host_frame"] = True
            entry["is_host"] = True
            assign_moving(entry, s)
        # 4) outro scene: presenter + subscribe banner
        elif ch == 99 and s is script["scenes"][-1]:
            s["_host_frame"] = True
            entry["overlay_banner"] = True
            entry["is_host"] = True
            assign_moving(entry, s)
            last_scene_hosted = True
        else:
            use_motion = bool(s.get("motion_file"))
            entry["file"] = s["motion_file"] if use_motion else None
            entry["motion_file"] = s["motion_file"] if use_motion else None
            if not use_motion:
                # NO static images: borrow a real clip from this run's pool
                assign_moving(entry, s)

        # absolute last resort for any slot still empty: the branded
        # graphic card (text art) — a static photo is never allowed
        if not entry.get("file") and not entry.get("motion_file"):
            entry["file"] = str(intro_img)
        if entry.get("is_host"):
            entry["kb"] = "zoom_in"
        else:
            entry["kb"] = KB_CYCLE[kb_i % len(KB_CYCLE)]
            kb_i += 1

        # ---- two-shot cut: long scenes get a real edit rhythm ----
        added_b = False
        if entry.get("motion_file") and dur >= 9.0 and not entry.get("is_host"):
            b_file = s.get("motion_file_b")
            if (not b_file or not Path(b_file).exists()) and len(motion_pool) >= 2:
                pick = motion_pool[(s["id"] * 5 + 2) % len(motion_pool)]
                if str(pick) != str(entry["motion_file"]):
                    b_file = str(pick)
            if b_file and Path(b_file).exists() and \
                    str(b_file) != str(entry["motion_file"]):
                d1 = round(dur * 0.42, 3)
                part_b = {"key": entry["key"] + "b", "file": b_file,
                          "motion_file": b_file,
                          "motion_skip_frac": 0.55 + (s["id"] * 0.37) % 0.3,
                          "dur": round(dur - d1, 3), "voice_file": None,
                          "scene": s, "_no_chapter_text": True}
                entry["dur"] = d1
                entries.append(entry)
                entries.append(part_b)
                added_b = True
            elif len(motion_pool) >= 2:
                # even the b-shot must MOVE: a different pool clip, never a still
                pick = motion_pool[(s["id"] * 7 + 3) % len(motion_pool)]
                if str(pick) != str(entry["motion_file"]):
                    d1 = round(dur * 0.42, 3)
                    part_b = {"key": entry["key"] + "b", "file": str(pick),
                              "motion_file": str(pick),
                              "motion_skip_frac": 0.40 + (s["id"] * 0.29) % 0.4,
                              "dur": round(dur - d1, 3), "voice_file": None,
                              "scene": s, "_no_chapter_text": True}
                    entry["dur"] = d1
                    entries.append(entry)
                    entries.append(part_b)
                    added_b = True
        if not added_b:
            entries.append(entry)
        prev_chapter = ch

    # extra outro presenter beat only when the last scene was not already a
    # presenter shot (avoids back-to-back same feel)
    if not last_scene_hosted:
        p = next_presenter()
        if p:
            entries.append({"key": "hostout", "file": str(p),
                            "motion_file": str(p), "dur": 5.0,
                            "voice_file": None, "is_card": True,
                            "is_host": True, "overlay_banner": True})
    entries.append({"key": "card9", "file": str(outro_img), "dur": 4.0,
                    "voice_file": None, "is_card": True})
    # drop persona entries whose frames could not be composed
    entries = [e for e in entries
               if not (e.get("is_host") and not Path(e["file"]).exists())]

    # chapter textfiles (per entry, so second cuts stay clean)
    for e in entries:
        s = e.get("scene")
        tf = rd / f"chap_{e['key']}.txt"
        if s and s.get("chapter_title") and s.get("chapter") not in (0, 99) \
                and not e.get("_no_chapter_text"):
            label = (s.get("chapter_label")
                     or f"CHAPTER {s['chapter']}")
            tf.write_text(label, encoding="utf-8")
        else:
            tf.write_text("", encoding="utf-8")
        e["_chapter_tf"] = str(tf)
        if s and not s.get("_chapter_tf"):
            s["_chapter_tf"] = str(tf)

    # timeline with xfade overlap compression
    starts, acc = [], 0.0
    for i, e in enumerate(entries):
        starts.append(acc)
        e["start"] = acc
        acc += e["dur"] - (xfade if i < len(entries) - 1 else 0)
    total = acc

    # ---- render every clip ----
    clips = []
    for i, e in enumerate(entries):
        clips.append(render_scene_clip(e.get("scene") or {}, e, cfg, run,
                                       logger))
        logger.info(f"clip {i + 1}/{len(entries)} ok ({e['dur']:.1f}s)")

    # ---- music + sfx aligned to timeline ----
    chapter_starts = []
    prev_ch = 0
    for e in entries:
        s = e.get("scene")
        if s and s.get("chapter", 0) not in (0, 99) and s["chapter"] != prev_ch:
            chapter_starts.append(e["start"])
            prev_ch = s["chapter"]
    if not chapter_starts:
        chapter_starts = [entries[1]["start"] if len(entries) > 1 else 0]
    intensity = [{"t": 0.0, "intensity": 0.55}]
    for cs in chapter_starts:
        intensity.append({"t": cs, "intensity": 0.85})
        intensity.append({"t": cs + 9, "intensity": 0.60})
    intensity.append({"t": max(0.0, entries[-1]["start"]), "intensity": 0.72})
    intensity.append({"t": max(1.0, total - 2), "intensity": 0.45})
    track = run["project_root"] / cfg.get("music", {}).get(
        "track", "assets/music/electrodoodle.mp3")
    fx.build_music(total, intensity, rd / "music.wav",
                   base_gain=cfg["music"]["base_gain"],
                   track=str(track) if track.exists() else None,
                   logger=logger)
    fx.build_sfx(total, [e["start"] for e in entries if not e.get("is_card")],
                 chapter_starts, rd / "sfx.wav",
                 sfx_gain=cfg["music"]["sfx_gain"],
                 chapter_boom=cfg["music"]["chapter_boom"], logger=logger)
    build_mix(cfg, run, {"total": total, "entries": entries}, logger)

    # ---- xfade assembly ----
    silent = rd / "silent.mp4"
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]
    parts = [f"[{i}:v]format=yuv420p,settb=AVTB,fps={fps}[v{i}]"
             for i in range(len(clips))]
    chain = ""
    acc = clips and entries[0]["dur"] or 0
    prev = "[v0]"
    trans_i = 0
    for j in range(1, len(clips)):
        off = acc - xfade
        s_next = entries[j].get("scene")
        s_prev = entries[j - 1].get("scene")
        ch_next = s_next.get("chapter") if s_next else None
        ch_prev = s_prev.get("chapter") if s_prev else None
        if ch_next == 99:
            trans = "fadeblack"
        elif ch_next is not None and ch_prev is not None \
                and ch_next != ch_prev:
            # alternate directional wipes between chapters for variety
            trans = ("smoothleft", "smoothright")[trans_i % 2]
            trans_i += 1
        else:
            trans = "fade"
        outlbl = f"[x{j}]"
        chain += (f"{prev}[v{j}]xfade=transition={trans}:duration={xfade}:"
                  f"offset={off:.3f}{outlbl};")
        prev = outlbl
        acc = acc + entries[j]["dur"] - xfade
    graph = ";".join(parts) + ";" + chain.rstrip(";")
    final_v = prev if len(clips) > 1 else "[v0]"
    graph += f";{final_v}format=yuv420p[vout]" if len(clips) > 1 else ""
    run_cmd(["ffmpeg", "-y", *inputs, "-filter_complex", graph.rstrip(";"),
             "-map", "[vout]", "-c:v", "libx264", "-preset", FINAL_PRESET,
             "-crf", FINAL_CRF, "-pix_fmt", "yuv420p", "-an", str(silent)],
            timeout=5400, retries=1, logger=logger)

    # ---- mux with audio + YouTube loudness (-14 LUFS) ----
    final = rd / "final.mp4"
    fade_start = max(0.0, total - 3.0)
    run_cmd(["ffmpeg", "-y", "-i", str(silent), "-i", str(rd / "mix.wav"),
             "-filter_complex",
             f"[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,"
             f"afade=t=out:st={fade_start:.2f}:d=3[aout]",
             "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
             "-movflags", "+faststart", str(final)],
            timeout=1800, retries=1, logger=logger)

    # ---- timeline.json: real chapter times for the description ----
    chapters_tl = [{"num": 0, "title": "Intro", "start": 0.0}]
    seen = set()
    for e in entries:
        s = e.get("scene")
        if s and s.get("chapter", 0) not in (0, 99, None) \
                and s["chapter"] not in seen:
            seen.add(s["chapter"])
            title = s.get("chapter_title") or f"Story {s['chapter']}"
            chapters_tl.append({"num": s["chapter"], "title": title,
                                "start": e["start"]})
    save_tl = {"total": total, "fps": fps,
               "chapters": chapters_tl}
    (rd / "timeline.json").write_text(
        json.dumps(save_tl, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"FINAL VIDEO: {final} ({total/60:.1f} min)")
    return final, total

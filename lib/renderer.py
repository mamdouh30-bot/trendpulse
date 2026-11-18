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
def _drawtext_filter(scene, dur, fps):
    if scene.get("_host_frame") or scene.get("chapter") in (0, 99) \
            or not scene.get("chapter_title"):
        return None
    tf = scene["_chapter_tf"]
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


def render_scene_clip(scene, entry, cfg, run, logger):
    """Render one scene entry to an intermediate mp4 (no audio)."""
    fps = int(cfg["visuals"].get("fps", 30))
    rd = run["dir"] / "render"
    rd.mkdir(exist_ok=True)
    dest = rd / f"clip_{entry['key']}.mp4"
    if dest.exists() and dest.stat().st_size > 50_000:
        return dest
    dur = entry["dur"]

    if entry.get("motion_file"):
        mdur = probe_duration(Path(entry["motion_file"]))
        ratio = dur / mdur
        stretch = f"setpts={ratio:.5f}*PTS," if ratio > 1.02 else ""
        vf = (
            f"scale={cfg['visuals']['width']}:{cfg['visuals']['height']}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={cfg['visuals']['width']}:{cfg['visuals']['height']},"
            f"fps={fps},{stretch}{GRADE},fps={fps}"
        )
        cmd = ["ffmpeg", "-y", "-i", entry["file"], "-filter_complex", vf,
               "-t", f"{dur:.3f}"]
    else:
        W5 = int(cfg["visuals"]["width"] * 1.7)
        H5 = int(cfg["visuals"]["height"] * 1.7)
        kb = _kb_filter(entry.get("kb", "zoom_in"), dur, fps)
        vf = (
            f"scale={W5}:{H5},"
            f"zoompan={kb}:"
            f"d=1:s={cfg['visuals']['width']}x{cfg['visuals']['height']}:"
            f"fps={fps},{GRADE}"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-t",
               f"{dur:.3f}", "-i", entry["file"], "-filter_complex", vf]

    dtf = _drawtext_filter(scene, dur, fps)
    if dtf:
        vf += "," + dtf
    vf += ",format=yuv420p"
    cmd[cmd.index("-filter_complex") + 1] = vf
    cmd += ["-c:v", "libx264", "-preset", CLIP_PRESET, "-crf", CLIP_CRF,
            "-pix_fmt", "yuv420p", "-an", str(dest)]
    run_cmd(cmd, timeout=900, retries=2, logger=logger)
    return dest


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
    host_frames = make_host_frames(cfg, run, script, logger)

    entries = []
    entries.append({"key": "card0", "file": str(intro_img), "dur": 3.0,
                    "voice_file": None, "is_card": True})

    intro_host = rd / "host_intro.png"
    if intro_host.exists():
        entries.append({"key": "hostin", "file": str(intro_host),
                        "dur": 4.6, "voice_file": None, "is_card": True,
                        "is_host": True})

    voice_dir = run["dir"] / "voice"
    vis_dir = run["dir"] / "visuals"
    motion_dir = run["dir"] / "motion"
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

        dur = max(3.0, s.get("voice_dur", 0) + pad)
        ch = s.get("chapter", 0)
        entry = {"key": f"s{s['id']:03d}", "dur": dur,
                 "voice_file": s["voice_file"], "scene": s}

        # 1) story bridge: first scene of each story shows the host
        if ch not in (0, 99) and ch != prev_chapter and \
                (rd / f"host_ch{ch}.png").exists():
            s["_host_frame"] = True
            entry["file"] = str(rd / f"host_ch{ch}.png")
            entry["is_host"] = True
        # 2) tool card beats b-roll on money-angle scenes
        elif s.get("tool_card_file") and Path(s["tool_card_file"]).exists():
            entry["file"] = s["tool_card_file"]
        # 3) mid-story host cutaway
        elif s["id"] % max(2, cut_every) == 0 and \
                (rd / "host_cutaway.png").exists():
            s["_host_frame"] = True
            entry["file"] = str(rd / "host_cutaway.png")
            entry["is_host"] = True
        # 4) outro host frame
        elif ch == 99 and (rd / "host_outro.png").exists() and \
                s is script["scenes"][-1]:
            s["_host_frame"] = True
            entry["file"] = str(rd / "host_outro.png")
            entry["is_host"] = True
            last_scene_hosted = True
        else:
            use_motion = (s.get("motion_file") and dur <= 8.5)
            entry["file"] = s["motion_file"] if use_motion else s["image_file"]
            entry["motion_file"] = s["motion_file"] if use_motion else None

        # cinematic motion assignment: host frames get a dignified slow
        # zoom-in, b-roll cycles through eased pans/zooms
        if not entry.get("file"):
            entry["file"] = s.get("image_file")
        if entry.get("is_host"):
            entry["kb"] = "zoom_in"
        else:
            entry["kb"] = KB_CYCLE[kb_i % len(KB_CYCLE)]
            kb_i += 1

        entries.append(entry)
        prev_chapter = ch

    # outro brand card; extra host-outro entry only when the last scene
    # did not already show the host (avoids repeating the same frame)
    if not last_scene_hosted and (rd / "host_outro.png").exists():
        entries.append({"key": "hostout", "file": str(rd / "host_outro.png"),
                        "dur": 5.0, "voice_file": None, "is_card": True,
                        "is_host": True})
    entries.append({"key": "card9", "file": str(outro_img), "dur": 4.0,
                    "voice_file": None, "is_card": True})
    # drop persona entries whose frames could not be composed
    entries = [e for e in entries
               if not (e.get("is_host") and not Path(e["file"]).exists())]

    # chapter textfiles
    for e in entries:
        s = e.get("scene")
        if s:
            tf = rd / f"chap_{e['key']}.txt"
            if s.get("chapter_title") and s.get("chapter") not in (0, 99):
                label = (s.get("chapter_label")
                         or f"CHAPTER {s['chapter']}")
                tf.write_text(label, encoding="utf-8")
            else:
                tf.write_text("", encoding="utf-8")
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
            trans = "smoothleft"
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

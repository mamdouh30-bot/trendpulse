"""Cinematic renderer: cards, Ken Burns scenes, AI motion, xfade assembly,
procedural score + SFX, professional loudness normalization.

Set TRENDPULSE_FAST_RENDER=1 (default on CI) to use faster x264 presets —
recommended on 2-core free hosting runners."""
import os
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from . import audio as fx
from .utils import probe_duration, run_cmd

FAST = os.environ.get("TRENDPULSE_FAST_RENDER") == "1"
CLIP_PRESET = "veryfast" if FAST else "fast"
CLIP_CRF = "21" if FAST else "18"
FINAL_PRESET = "faster" if FAST else "medium"
FINAL_CRF = "22" if FAST else "19"

FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
GRADE = "eq=contrast=1.05:saturation=1.06,vignette=PI/4.6,noise=alls=6:allf=t+u"


# ---------------------------------------------------------------- cards (PIL)
def _dark_bg(W, H):
    img = Image.new("RGB", (W, H))
    top = (8, 8, 13)
    bottom = (26, 20, 38)
    for y in range(H):
        f = y / H
        img.paste(tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3)),
                  (0, y, W, y + 1))
    # subtle vignette
    vig = Image.new("L", (W, H), 0)
    dv = ImageDraw.Draw(vig)
    dv.ellipse((-W * 0.3, -H * 0.3, W * 1.3, H * 1.3), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(img, dark, vig.point(lambda p: 120 + p * 135 // 255))
    return img


def _pulse_line(img, y_frac, accent=(214, 34, 44)):
    W, H = img.size
    d = ImageDraw.Draw(img)
    y = int(H * y_frac)
    pts = []
    step = 22
    spike_w = W // 3
    spike_x = (W - spike_w) // 2
    for x in range(0, W, step):
        pts.append((x, y))
        if spike_x <= x <= spike_x + spike_w:
            k = (x - spike_x) / spike_w
            pts[-1] = (x, y)
    # draw flat line then a heartbeat spike in the middle
    d.line([(0, y), (spike_x, y)], fill=accent, width=5)
    mid = spike_x + spike_w // 2
    d.line([(spike_x, y), (mid - 60, y), (mid - 20, y - 46), (mid, y + 30),
            (mid + 20, y), (spike_x + spike_w, y)], fill=accent, width=5)
    d.line([(spike_x + spike_w, y), (W, y)], fill=accent, width=5)
    return img


def _wrap(text, max_chars):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def make_intro_card(run, cfg, title, logger):
    W, H = cfg["visuals"]["width"], cfg["visuals"]["height"]
    img = _dark_bg(W, H)
    img = _pulse_line(img, 0.30)
    d = ImageDraw.Draw(img)
    ch_name = cfg["channel"]["badge_text"]
    f_small = ImageFont.truetype(FONT_B, 44)
    f_title = ImageFont.truetype(FONT_B, 104)
    f_sub = ImageFont.truetype(FONT_R, 38)

    # channel badge
    tw = d.textlength(ch_name, font=f_small)
    d.rectangle(((W - tw) / 2 - 26, H * 0.42 - 20, (W + tw) / 2 + 26, H * 0.42 + 62),
                fill=(214, 34, 44))
    d.text(((W - tw) / 2, H * 0.42), ch_name, font=f_small, fill="white")
    # title (2 lines)
    lines = _wrap(title.upper(), 34)
    y = H * 0.52
    for ln in lines:
        tw = d.textlength(ln, font=f_title)
        d.text(((W - tw) / 2, y), ln, font=f_title, fill=(240, 240, 244))
        y += 118
    # subtitle
    sub = "A " + ch_name + " DOCUMENTARY"
    tw = d.textlength(sub, font=f_sub)
    d.text(((W - tw) / 2, y + 30), sub, font=f_sub, fill=(170, 172, 186))

    out = run["dir"] / "render"
    out.mkdir(exist_ok=True)
    dest = out / "card_intro.png"
    img.save(dest, quality=92)
    logger.info("intro card drawn")
    return dest


def make_outro_card(run, cfg, logger):
    W, H = cfg["visuals"]["width"], cfg["visuals"]["height"]
    img = _dark_bg(W, H)
    img = _pulse_line(img, 0.42)
    d = ImageDraw.Draw(img)
    name = cfg["channel"]["name"]
    tag = cfg["channel"]["tagline"]
    f_big = ImageFont.truetype(FONT_B, 120)
    f_tag = ImageFont.truetype(FONT_R, 44)
    f_cta = ImageFont.truetype(FONT_B, 52)
    tw = d.textlength(name.upper(), font=f_big)
    d.text(((W - tw) / 2, H * 0.50), name.upper(), font=f_big, fill=(240, 240, 244))
    tw = d.textlength(tag, font=f_tag)
    d.text(((W - tw) / 2, H * 0.50 + 150), tag, font=f_tag, fill=(178, 180, 194))
    cta = "SUBSCRIBE  —  NEW INVESTIGATIONS EVERY 12 HOURS"
    tw = d.textlength(cta, font=f_cta)
    d.text(((W - tw) / 2, H * 0.78), cta, font=f_cta, fill=(214, 34, 44))
    out = run["dir"] / "render"
    out.mkdir(exist_ok=True)
    dest = out / "card_outro.png"
    img.save(dest, quality=92)
    logger.info("outro card drawn")
    return dest


# ------------------------------------------------------------ scene renderer
def _drawtext_filter(scene, dur):
    if scene.get("chapter") in (0, 99) or not scene.get("chapter_title"):
        return None
    tf = scene["_chapter_tf"]
    end = max(2.0, dur - 1.4)
    return (
        f"drawtext=fontfile={FONT_B}:textfile={tf}:fontcolor=white@0.94:"
        f"fontsize=46:borderw=2:bordercolor=black@0.7:box=1:boxcolor=black@0.45:"
        f"boxborderw=18:x=(w-text_w)/2:y=h-152:enable='between(t,1.2,{end:.1f})'"
    )


def render_scene_clip(scene, entry, cfg, run, logger):
    """Render one scene entry to an intermediate mp4 (24fps, 1080p, no audio)."""
    rd = run["dir"] / "render"
    rd.mkdir(exist_ok=True)
    dest = rd / f"clip_{entry['key']}.mp4"
    if dest.exists() and dest.stat().st_size > 50_000:
        return dest
    dur = entry["dur"]
    zrate = 0.10 / max(1.0, dur * 24)
    zoom_in = (int(entry["key"][-1]) % 2 == 0)

    if entry.get("motion_file"):
        mdur = probe_duration(Path(entry["motion_file"]))
        ratio = dur / mdur
        stretch = f"setpts={ratio:.5f}*PTS," if ratio > 1.02 else ""
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,fps=24,{stretch}{GRADE},fps=24"
        )
        cmd = ["ffmpeg", "-y", "-i", entry["file"], "-filter_complex", vf,
               "-t", f"{dur:.3f}"]
    else:
        z = (f"min(1+{zrate:.7f}*on,1.10)" if zoom_in
             else f"max(1.10-{zrate:.7f}*on,1.0)")
        vf = (
            "scale=5760:3240,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s=1920x1080:fps=24,{GRADE}"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", "24", "-t",
               f"{dur:.3f}", "-i", entry["file"], "-filter_complex", vf]

    dt = _drawtext_filter(scene, dur)
    if dt:
        vf += "," + dt
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
    """Place narration at exact scene starts; music + sfx aligned; write mix.wav."""
    rd = run["dir"] / "render"
    total = timeline["total"]
    n = int(total * 44100)
    voice_track = np.zeros(n, dtype=np.float32)
    for e in timeline["entries"]:
        if e.get("voice_file"):
            seg = _read_wav_mono(e["voice_file"], 44100)
            i0 = int((e["start"] + 0.30) * 44100)
            i1 = min(n, i0 + len(seg))
            if i0 < n:
                voice_track[i0:i1] += seg[: i1 - i0]
    peak = np.max(np.abs(voice_track)) + 1e-9
    voice_track *= 0.82 / peak

    music = _read_wav_mono(rd / "music.wav", 44100)
    sfx = _read_wav_mono(rd / "sfx.wav", 44100)
    music = np.resize(music, n) if len(music) < n else music[:n]
    sfx = np.resize(sfx, n) if len(sfx) < n else sfx[:n]

    mix = voice_track + music + sfx
    mix = np.tanh(mix * 1.1) * 0.92  # soft limiter
    dest = rd / "mix.wav"
    pcm = (np.clip(mix, -0.98, 0.98) * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(pcm.tobytes())
    logger.info(f"audio mix written ({total/60:.1f} min)")
    return dest


# ------------------------------------------------------------- final cut
def render_video(cfg, run, script, logger):
    W, H = cfg["visuals"]["width"], cfg["visuals"]["height"]
    rd = run["dir"] / "render"
    xfade = cfg["content"]["xfade"]
    pad = cfg["content"]["scene_pad"]

    intro_img = make_intro_card(run, cfg, script["title"], logger)
    outro_img = make_outro_card(run, cfg, logger)

    entries = []
    entries.append({"key": "card0", "file": str(intro_img), "dur": 6.5,
                    "voice_file": None, "is_card": True})
    voice_dir = run["dir"] / "voice"
    vis_dir = run["dir"] / "visuals"
    motion_dir = run["dir"] / "motion"
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
        use_motion = (s.get("motion_file")
                      and dur <= 8.5)
        entries.append({
            "key": f"s{s['id']:03d}",
            "file": s["motion_file"] if use_motion else s["image_file"],
            "motion_file": s["motion_file"] if use_motion else None,
            "dur": dur, "voice_file": s["voice_file"],
            "scene": s,
        })
    entries.append({"key": "card9", "file": str(outro_img), "dur": 8.5,
                    "voice_file": None, "is_card": True})

    # chapter textfiles
    for e in entries:
        s = e.get("scene")
        if s:
            tf = rd / f"chap_{e['key']}.txt"
            if s.get("chapter_title") and s.get("chapter") not in (0, 99):
                label = f"CHAPTER {s['chapter']}" + \
                        (f" — {s['chapter_title'].upper()}" if s.get("chapter_title") else "")
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
        clips.append(render_scene_clip(e.get("scene") or {}, e, cfg, run, logger))
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
    fx.build_music(total, intensity, rd / "music.wav",
                   base_gain=cfg["music"]["base_gain"], logger=logger)
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
    parts = [f"[{i}:v]format=yuv420p,settb=AVTB,fps=24[v{i}]" for i in range(len(clips))]
    chain = ""
    acc = clips and entries[0]["dur"] or 0
    prev = "[v0]"
    for j in range(1, len(clips)):
        off = acc - xfade
        s_next = entries[j].get("scene")
        s_prev = entries[j - 1].get("scene")
        trans = "fade"
        if s_next and s_prev and s_next.get("chapter") != s_prev.get("chapter"):
            trans = "fadeblack"
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

    # ---- mux with audio + loudnorm ----
    final = rd / "final.mp4"
    fade_start = max(0.0, total - 3.0)
    run_cmd(["ffmpeg", "-y", "-i", str(silent), "-i", str(rd / "mix.wav"),
             "-filter_complex",
             f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,"
             f"afade=t=out:st={fade_start:.2f}:d=3[aout]",
             "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
             "-movflags", "+faststart", str(final)],
            timeout=1800, retries=1, logger=logger)
    logger.info(f"FINAL VIDEO: {final} ({total/60:.1f} min)")
    return final, total

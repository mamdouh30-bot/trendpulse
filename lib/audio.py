"""Procedural cinematic audio: dark score bed + whoosh/boom/riser SFX.

Everything is synthesized with numpy (royalty-free by construction) and aligned
to the exact scene timeline of the cut.
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100


def _write_wav(dest: Path, stereo: np.ndarray):
    x = np.clip(stereo, -0.98, 0.98)
    pcm = (x * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.T.reshape(-1, 2).tobytes() if pcm.shape[0] > 1 else pcm.tobytes())


def _env_points(total, timeline):
    """Piecewise-linear intensity envelope from (t, intensity) points."""
    pts = sorted(timeline, key=lambda p: p["t"])
    xs = [p["t"] for p in pts]
    ys = [p["intensity"] for p in pts]
    if not xs or xs[0] > 0:
        xs.insert(0, 0.0)
        ys.insert(0, 0.45)
    if xs[-1] < total:
        xs.append(total)
        ys.append(ys[-1])
    t = np.arange(int(total * SR)) / SR
    return np.interp(t, xs, ys)


def _lowpass(x, alpha):
    out = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += alpha * (x[i] - acc)
        out[i] = acc
    return out


def _lowpass_fast(x, alpha):
    """Vectorized one-pole lowpass approximation via lfilter-style recursion."""
    # chunked scan is slow in pure python; use scipy if present, else fft filter
    try:
        from scipy.signal import lfilter
        return lfilter([alpha], [1, -(1 - alpha)], x)
    except Exception:  # noqa: BLE001
        n = len(x)
        X = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(n, 1 / SR)
        cutoff = alpha * SR / (2 * np.pi)
        H = 1 / np.sqrt(1 + (freqs / max(cutoff, 1.0)) ** 2)
        return np.fft.irfft(X * H, n)


def build_music(total, timeline, dest, base_gain=0.16, logger=None):
    n = int(total * SR)
    t = np.arange(n) / SR
    env = _env_points(total, timeline)

    # sub drone: two detuned sines with slow LFO
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.045 * t)
    sub = 0.55 * np.sin(2 * np.pi * 55.0 * t) + 0.55 * np.sin(2 * np.pi * 55.6 * t)
    sub *= (0.35 + 0.35 * lfo)

    # dark minor pad (A minor-ish: 110 / 130.8 / 164.8 + soft octaves)
    pad = (0.30 * np.sin(2 * np.pi * 110.0 * t)
           + 0.22 * np.sin(2 * np.pi * 130.8 * t)
           + 0.16 * np.sin(2 * np.pi * 164.8 * t)
           + 0.10 * np.sin(2 * np.pi * 220.0 * t))
    pad = _lowpass_fast(pad, 0.08)
    slow = 0.5 + 0.5 * np.sin(2 * np.pi * 0.03 * t + 1.2)
    pad *= (0.30 + 0.30 * slow)

    # heartbeat pulse: 50Hz decaying thump every 2.0 s
    pulse = np.zeros(n)
    beat_len = int(0.55 * SR)
    bt = np.arange(beat_len) / SR
    beat = np.sin(2 * np.pi * (48 - 10 * bt / 0.55) * bt) * np.exp(-bt * 9)
    period = int(2.0 * SR)
    start = 0
    while start < n:
        end = min(start + beat_len, n)
        pulse[start:end] += beat[: end - start]
        start += period
    pulse = _lowpass_fast(pulse, 0.25)

    bed = sub + 0.9 * pad + 0.8 * pulse
    bed *= env * 0.9
    bed /= np.max(np.abs(bed)) + 1e-9

    stereo = np.zeros((n, 2))
    stereo[:, 0] = bed
    stereo[:, 1] = np.roll(bed, int(0.012 * SR))  # subtle width
    _write_wav(dest, stereo * base_gain)
    if logger:
        logger.info(f"music bed: {total/60:.1f} min -> {dest.name}")


def _whoosh(dur=0.55, center=750, width=900, amp=1.0):
    n = int(dur * SR)
    noise = np.random.default_rng().standard_normal(n)
    N = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    H = np.exp(-0.5 * ((freqs - center) / width) ** 2)
    x = np.fft.irfft(N * H, n)
    e = np.sin(np.pi * np.arange(n) / n) ** 2  # smooth swell-fade
    return x * e * amp


def _boom(dur=1.5, amp=1.0):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    f = 62 - 28 * (tt / dur)  # 62 -> 34 Hz drop
    phase = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(phase) * np.exp(-tt * 3.2)
    click_len = int(0.02 * SR)
    click = np.zeros(n)
    click[:click_len] = np.random.default_rng().standard_normal(click_len) * np.exp(
        -np.arange(click_len) / SR * 180)
    click = _lowpass_fast(click, 0.3)
    return (body + 0.4 * click) * amp


def _riser(dur=2.6, amp=1.0):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    f = 180 * (2 ** (tt / dur * 1.7))  # rising pitch
    phase = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(phase) * (tt / dur) ** 2.2
    # band-limited noise swell (fixed band, cheap 1-D FFT filter)
    noise = np.random.default_rng().standard_normal(n)
    N = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    H = np.exp(-0.5 * ((freqs - 1400) / 900) ** 2)
    nsw = np.fft.irfft(N * H, n)
    return (0.6 * tone + 0.8 * nsw) * (tt / dur) ** 2 * amp


def build_sfx(total, scene_starts, chapter_starts, dest, sfx_gain=0.30,
              chapter_boom=True, logger=None):
    n = int(total * SR)
    track = np.zeros(n)

    def place(sig, at, gain=1.0):
        i0 = int(at * SR)
        i1 = min(n, i0 + len(sig))
        if i0 < n:
            track[i0:i1] += sig[: i1 - i0] * gain

    # whoosh at every scene boundary
    for s in scene_starts:
        place(_whoosh(), max(0.0, s - 0.30), 0.9)
    # boom + riser at chapter starts
    for c in chapter_starts:
        if chapter_boom:
            place(_boom(), max(0.0, c - 0.15), 1.4)
        if c > 1.0:
            place(_riser(), max(0.0, c - 2.6), 0.5)

    peak = np.max(np.abs(track)) + 1e-9
    track /= peak
    stereo = np.zeros((n, 2))
    stereo[:, 0] = track
    stereo[:, 1] = np.roll(track, int(0.010 * SR))
    _write_wav(dest, stereo * sfx_gain)
    if logger:
        logger.info(f"sfx track: {len(scene_starts)} whooshes, "
                    f"{len(chapter_starts)} booms -> {dest.name}")

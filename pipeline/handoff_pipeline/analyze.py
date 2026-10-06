"""Pure-ish analysis functions. Input: mono float audio at ANALYSIS_SR. Output: plain dicts/arrays.

Everything here is deterministic DSP (no models), so it is fast enough to run on CPU.
The contract for what we emit lives in contracts/track_analysis.schema.json.
"""
from __future__ import annotations

import numpy as np
import librosa

ANALYSIS_SR = 22050
HOP = 512                 # onset/beat hop: 512 / 22050 = 23.2 ms
WAVE_HOP = 220            # waveform hop: ~100 columns per second
BEATS_PER_BAR = 4

# Tunable thresholds. Calibrate these on real tracks and record changes in ROADMAP.md.
BEATMATCHABLE_MAX_DRIFT_MS = 10.0         # worst per-segment offset of the global grid
PHASE_HOP, PHASE_NFFT = 64, 512           # high-res onset envelope for grid fitting
GRID_LOW_HZ = 150.0                       # kick band; the grid's phase comes from here
GRID_FULL_WEIGHT = 0.25                   # full-band onsets help where there's no kick
GRID_TEMPO_BAND = 0.04                    # +/- around the tracker tempo (its lags are 2.5% apart)
GRID_COARSE_DRIFT_S = 0.004               # coarse period step = this much drift over the track
GRID_FOLD_BINS = 240                      # phase bins per beat in the coarse search (~2 ms)
ATTACK_WINDOW_BEATS = 1 / 8               # +/- window for the broadband (attack) phase
DRIFT_SEGMENT_BEATS = 32                  # 8 bars per drift measurement
DRIFT_MIN_SALIENCE = 1.6                  # kick-pulse max/mean below which a segment is skipped
BAND_EDGES_HZ = (200.0, 2000.0)           # low < 200 <= mid < 2000 <= high

PITCH_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Krumhansl-Kessler key profiles (C major / C minor), rotated for other tonics.
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


CHROMA_FMIN_HZ = 65.41   # C2. Kick fundamentals (40-60 Hz) otherwise leak into chroma and wreck key/downbeat.


def _chroma_from_power(S_power: np.ndarray, freqs: np.ndarray, sr: int) -> np.ndarray:
    S = S_power.copy()
    S[freqs < CHROMA_FMIN_HZ] = 0.0
    return librosa.feature.chroma_stft(S=S, sr=sr, hop_length=HOP)


# ---------------------------------------------------------------- rhythm

def _onset_envelope(y: np.ndarray, sr: int) -> np.ndarray:
    # Default mean aggregation. Median across bins suppresses kicks (they occupy few
    # low bins) and caused 2/3x and 1/2x tempo errors on the demo tracks.
    return librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)


def _sample_env(env: np.ndarray, times: np.ndarray, sr: int, hop: int) -> np.ndarray:
    """Linearly interpolate an envelope (one value per hop) at arbitrary times."""
    frame_times = librosa.frames_to_time(np.arange(len(env)), sr=sr, hop_length=hop)
    return np.interp(times, frame_times, env, left=0.0, right=0.0)


def grid_envelopes(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """High-res (PHASE_HOP) onset envelopes for grid fitting, each scaled to mean 1.
    Returns (kick, score, full): kick = low-band (< GRID_LOW_HZ) onsets only; full =
    full-band onsets (the attack you hear); score = kick + GRID_FULL_WEIGHT * full.
    Hats and claps are loud in the full band and sit on offbeats, so the kick band has to
    dominate the search for WHICH position is the beat (score). But a kick whose low end
    swells peaks in the kick band tens of ms after its attack, so WHERE exactly the beat
    is comes from full, searched only within ATTACK_WINDOW_BEATS (refine_phase).

    The 512/64 window keeps the envelope peak ~4 ms after the true attack (it was 28 ms
    with 2048). n_fft must be passed with S=: onset_strength otherwise centers as if
    n_fft were 2048, which put the kick envelope 35 ms late."""
    S = np.abs(librosa.stft(y, n_fft=PHASE_NFFT, hop_length=PHASE_HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=PHASE_NFFT)
    kick = librosa.onset.onset_strength(S=librosa.power_to_db(S[freqs < GRID_LOW_HZ]), sr=sr,
                                        hop_length=PHASE_HOP, n_fft=PHASE_NFFT)
    full = librosa.onset.onset_strength(y=y, sr=sr, hop_length=PHASE_HOP, n_fft=PHASE_NFFT)
    n = min(len(kick), len(full))
    kick = kick[:n] / (kick[:n].mean() + 1e-9)
    full = full[:n] / (full[:n].mean() + 1e-9)
    return kick, kick + GRID_FULL_WEIGHT * full, full


def _grid_times(a: float, b: float, duration_s: float) -> np.ndarray:
    return a + b * np.arange(int(np.floor((duration_s - a) / b)) + 1)


def fit_grid(env: np.ndarray, sr: int, duration_s: float, center_bpm: float) -> tuple[float, float]:
    """Best constant grid t_i = a + b*i, found by maximizing S(a, b) = sum_i env(a + b*i)
    directly. Never uses tracked beat indices: a tracker that slips half a beat gives the
    wrong index to every beat after the slip, which biases a line fit (Frog Prince).

    Coarse: for each period b in +/- GRID_TEMPO_BAND around center_bpm (stepped so one
    step drifts GRID_COARSE_DRIFT_S over the track), fold env modulo b into phase bins;
    the best bin is the best a for that b. Fine: evaluate S(a, b) exactly around the
    coarse optimum at 0.1 ms phase resolution. Returns (a, b) with 0 <= a < b."""
    frame_t = np.arange(len(env)) * PHASE_HOP / sr
    b0 = 60.0 / center_bpm
    step = GRID_COARSE_DRIFT_S / (duration_s / b0)
    periods = np.arange(b0 / (1 + GRID_TEMPO_BAND), b0 / (1 - GRID_TEMPO_BAND), step)
    smooth = np.array([0.25, 0.5, 0.25])
    best = (-np.inf, 0.0, b0)
    for b in periods:
        bins = (np.mod(frame_t, b) * (GRID_FOLD_BINS / b)).astype(int) % GRID_FOLD_BINS
        prof = np.bincount(bins, weights=env, minlength=GRID_FOLD_BINS)
        prof = np.convolve(np.concatenate([prof[-1:], prof, prof[:1]]), smooth, mode="valid")
        k = int(np.argmax(prof))
        if prof[k] > best[0]:
            best = (float(prof[k]), (k + 0.5) * b / GRID_FOLD_BINS, float(b))
    _, a_c, b_c = best

    fine_b = np.linspace(b_c - 2 * step, b_c + 2 * step, 41)
    fine_a = a_c + np.arange(-0.005, 0.005 + 1e-9, 0.0001)
    n = int(np.floor((duration_s - a_c) / b_c))
    i = np.arange(n)
    scores = np.empty((len(fine_b), len(fine_a)))
    for r, b in enumerate(fine_b):
        t = fine_a[:, None] + b * i[None, :]
        scores[r] = _sample_env(env, t.ravel(), sr, PHASE_HOP).reshape(t.shape).sum(axis=1)
    r, c = np.unravel_index(int(np.argmax(scores)), scores.shape)
    b = float(fine_b[r])
    return float(np.mod(fine_a[c], b)), b


def refine_phase(full: np.ndarray, sr: int, a: float, b: float, duration_s: float) -> float:
    """Slide the grid within +/- ATTACK_WINDOW_BEATS to where the broadband onsets (attacks)
    are strongest. The window is narrow enough that offbeat hats (1/2 beat) and 16ths (1/4)
    can't pull it, wide enough to reach an attack ~50 ms before a swelling kick's low end
    (seen on real tracks; 1/8 beat is 58 ms at 128 BPM)."""
    beats = _grid_times(a, b, duration_s)
    offsets = np.arange(-ATTACK_WINDOW_BEATS * b, ATTACK_WINDOW_BEATS * b + 1e-9, 0.0002)
    t = (beats[None, :] + offsets[:, None]).ravel()
    scores = _sample_env(full, t, sr, PHASE_HOP).reshape(len(offsets), -1).sum(axis=1)
    return float(np.mod(a + offsets[int(np.argmax(scores))], b))


def grid_drift(kick: np.ndarray, full: np.ndarray, sr: int, a: float, b: float,
               duration_s: float) -> list[dict]:
    """How far the global grid is from the music, segment by segment.

    For each DRIFT_SEGMENT_BEATS-beat segment, profile(o) = mean over its grid beats of
    env(t + o), for o across one whole beat. A segment is measured only if it has an
    on-beat kick: the kick profile peaks within a quarter beat of the grid, and its
    salience (max/mean) is >= DRIFT_MIN_SALIENCE. Segments without one (pad breakdowns;
    intros where the only low-end is offbeat bass, as in Zute) can't confirm or refute
    the grid, so they're skipped. The offset is then the argmax of the full-band (attack)
    profile within ATTACK_WINDOW_BEATS: the same envelope and window refine_phase placed the
    grid with, so constant envelope latency cancels. Measuring on the kick-weighted score
    over a quarter beat instead jumped between a kick's attack and its late low-end swell
    (or other elements) section by section, reading 50-100 ms of "drift" on steady tracks.
    Returns [{start_s, offset_ms (None if skipped), salience}]."""
    beats = _grid_times(a, b, duration_s)
    offsets = np.arange(-0.5 * b, 0.5 * b, 0.0005)
    near = np.abs(offsets) <= 0.25 * b
    attack = np.abs(offsets) <= ATTACK_WINDOW_BEATS * b
    edge_bins = int(0.010 / 0.0005)
    out = []
    for s in range(0, len(beats), DRIFT_SEGMENT_BEATS):
        seg = beats[s:s + DRIFT_SEGMENT_BEATS]
        if len(seg) < DRIFT_SEGMENT_BEATS // 2:
            break
        t = (seg[None, :] + offsets[:, None]).ravel()
        k_prof = _sample_env(kick, t, sr, PHASE_HOP).reshape(len(offsets), -1).mean(axis=1)
        f_prof = _sample_env(full, t, sr, PHASE_HOP).reshape(len(offsets), -1).mean(axis=1)
        k_near = k_prof[near]
        k = int(np.argmax(k_near))
        salience = float(k_near[k] / (k_prof.mean() + 1e-9))
        interior = edge_bins <= k < len(k_near) - edge_bins   # a peak, not the shoulder of an offbeat bump
        off = (float(offsets[attack][int(np.argmax(f_prof[attack]))] * 1000)
               if interior and salience >= DRIFT_MIN_SALIENCE else None)
        out.append({"start_s": float(seg[0]), "offset_ms": off, "salience": salience})
    return out


def track_beats(y: np.ndarray, sr: int, duration_s: float, bpm_hint: float | None = None,
                force_grid: str | None = None) -> dict:
    """Fit a constant beat grid to the whole track (fit_grid), move its phase onto the
    attacks (refine_phase), measure how far the music drifts from it (grid_drift), and keep the grid if the worst drift is under
    BEATMATCHABLE_MAX_DRIFT_MS. Otherwise fall back to the raw tracked beats.

    The tracker only supplies the tempo the grid search is centered on (bpm_hint
    overrides it) and the fallback beats. Its tempo comes from integer-frame lags at HOP,
    so it is quantized to ~2.5% steps (123.05 / 129.20 near 125 BPM).
    """
    env = _onset_envelope(y, sr)
    tempo, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=HOP,
                                            start_bpm=bpm_hint or 120.0, units="frames")
    raw = librosa.frames_to_time(np.asarray(frames), sr=sr, hop_length=HOP)
    if len(raw) < 8:
        raise ValueError(f"beat tracker found only {len(raw)} beats; is this track mostly silent?")
    ibi = np.diff(raw)
    ibi_cv = float(np.std(ibi) / np.mean(ibi))

    kick, score, full = grid_envelopes(y, sr)
    a, b = fit_grid(score, sr, duration_s, bpm_hint or float(np.atleast_1d(tempo)[0]))
    a = refine_phase(full, sr, a, b, duration_s)
    segments = grid_drift(kick, full, sr, a, b, duration_s)
    measured = [abs(s["offset_ms"]) for s in segments if s["offset_ms"] is not None]
    max_drift_ms = max(measured) if measured else None
    beatmatchable = max_drift_ms is not None and max_drift_ms <= BEATMATCHABLE_MAX_DRIFT_MS

    use_regular = beatmatchable if force_grid is None else force_grid == "regular"
    if use_regular:
        beats = _grid_times(a, b, duration_s)
        beats = beats[beats < duration_s]
        grid = "regular"
    else:
        beats = raw
        grid = "tracked"

    return {
        "beats": beats.astype(float),
        "bpm": float(60.0 / b),
        "ibi_cv": ibi_cv,
        "max_drift_ms": max_drift_ms,
        "drift_segments": segments,
        "beatmatchable": bool(beatmatchable),
        "grid": grid,
        "onset_env": env,
    }


def segment_means(feat: np.ndarray, boundaries_s: np.ndarray, sr: int, hop: int) -> np.ndarray:
    """Mean of feature columns over each segment [t_j, t_{j+1}); last segment runs to the end.
    Returns (dims, len(boundaries_s)). Written by hand instead of librosa.util.sync because
    sync's implicit padding shifts columns by one when a boundary falls on frame 0."""
    f = np.clip(librosa.time_to_frames(boundaries_s, sr=sr, hop_length=hop), 0, feat.shape[1] - 1)
    ends = np.append(f[1:], feat.shape[1])
    out = np.zeros((feat.shape[0], len(f)))
    for j, (a, b) in enumerate(zip(f, ends)):
        out[:, j] = feat[:, a:max(b, a + 1)].mean(axis=1)
    return out


def _zscore(x: np.ndarray) -> np.ndarray:
    s = np.std(x)
    return (x - np.mean(x)) / s if s > 1e-9 else np.zeros_like(x)


def find_downbeat_phase(y: np.ndarray, sr: int, beats: np.ndarray, onset_env: np.ndarray) -> tuple[int, float]:
    """Pick which of the 4 beat phases is beat 1 of the bar.

    Score each phase k by the mean (over beats i with i % 4 == k) of
      z(low-band onset) + z(harmonic change) + 0.5 * z(full onset).
    Kicks are on every beat in four-on-the-floor music, so the low band alone is
    ambiguous there; chord/bass changes usually land on beat 1 and break the tie.
    Returns (first_downbeat_index in 0..3, confidence in 0..1).
    """
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low_env = librosa.onset.onset_strength(S=librosa.power_to_db(S[freqs < 150.0]), sr=sr, hop_length=HOP)
    low = _sample_env(low_env, beats, sr, HOP)
    full = _sample_env(onset_env, beats, sr, HOP)

    chroma = _chroma_from_power(S, freqs, sr)
    seg = segment_means(chroma, beats, sr, HOP)                  # column j = segment starting at beat j
    seg = seg / (np.linalg.norm(seg, axis=0, keepdims=True) + 1e-9)
    flux = np.zeros(len(beats))
    flux[1:] = np.linalg.norm(np.diff(seg, axis=1), axis=0)[: len(beats) - 1]

    combined = _zscore(low) + _zscore(flux) + 0.5 * _zscore(full)
    scores = np.array([combined[k::BEATS_PER_BAR].mean() for k in range(BEATS_PER_BAR)])
    order = np.argsort(scores)[::-1]
    spread = scores[order[0]] - scores[order[-1]]
    confidence = float(np.clip((scores[order[0]] - scores[order[1]]) / (spread + 1e-9), 0.0, 1.0))
    return int(order[0]), confidence


# ---------------------------------------------------------------- bars and energy

def _band_power(S_power: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    lo, hi = BAND_EDGES_HZ
    return np.stack([
        S_power[freqs < lo].sum(axis=0),
        S_power[(freqs >= lo) & (freqs < hi)].sum(axis=0),
        S_power[freqs >= hi].sum(axis=0),
    ])  # (3, frames)


def build_bars(y: np.ndarray, sr: int, beats: np.ndarray, first_downbeat_index: int,
               duration_s: float) -> list[dict]:
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    bands = _band_power(S, freqs)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    frame_t = librosa.frames_to_time(np.arange(bands.shape[1]), sr=sr, hop_length=HOP)

    starts = list(range(first_downbeat_index, len(beats), BEATS_PER_BAR))
    period = float(np.median(np.diff(beats)))
    bars, band_db = [], []
    for j, bi in enumerate(starts):
        t0 = float(beats[bi])
        t1 = float(beats[bi + BEATS_PER_BAR]) if bi + BEATS_PER_BAR < len(beats) else t0 + BEATS_PER_BAR * period
        if t0 > duration_s - 0.5 * BEATS_PER_BAR * period:
            break  # drop a tiny partial bar at the very end
        m = (frame_t >= t0) & (frame_t < min(t1, duration_s))
        if not np.any(m):
            m = np.zeros(len(frame_t), dtype=bool)
            m[int(np.argmin(np.abs(frame_t - t0)))] = True
        band_db.append(10 * np.log10(bands[:, m].mean(axis=1) + 1e-10))
        rms_db = float(10 * np.log10(np.mean(rms[m] ** 2) + 1e-10))
        bars.append({"index": j, "beat_index": int(bi), "start_s": round(t0, 4), "rms_db": round(rms_db, 2)})

    band_db = np.array(band_db)  # (n_bars, 3)
    lo = np.percentile(band_db, 5, axis=0)
    hi = np.percentile(band_db, 95, axis=0)
    norm = np.clip((band_db - lo) / np.maximum(hi - lo, 1e-6), 0, 1)
    for bar, e in zip(bars, norm):
        bar["energy"] = {"low": round(float(e[0]), 3), "mid": round(float(e[1]), 3), "high": round(float(e[2]), 3)}
    return bars


# ---------------------------------------------------------------- structure

def _checkerboard(half: int) -> np.ndarray:
    """Gaussian-tapered checkerboard kernel (2*half x 2*half). Positive on the two
    'same-section' quadrants, negative on the two 'cross-section' quadrants."""
    r = np.arange(-half, half) + 0.5
    sign = np.sign(r)
    g = np.exp(-0.5 * (r / (half / 2.0)) ** 2)
    return np.outer(sign * g, sign * g)


def find_sections(y: np.ndarray, sr: int, bars: list[dict], beats: np.ndarray) -> list[dict]:
    """Bar-level self-similarity + checkerboard novelty (Foote).

    F[i] = standardized [band energies, chroma] for bar i. SSM[i, j] = cosine(F[i], F[j]).
    Sliding the checkerboard along the diagonal gives novelty[i], which peaks where
    the bars before i look alike, the bars after i look alike, and the two groups differ.
    Boundaries are snapped to 4-bar multiples and kept >= 8 bars apart.
    """
    n = len(bars)
    if n < 16:
        return [_section(0, 0, n, bars)]

    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP)) ** 2
    chroma = _chroma_from_power(S, librosa.fft_frequencies(sr=sr, n_fft=2048), sr)
    bar_chroma = segment_means(chroma, np.array([b["start_s"] for b in bars]), sr, HOP)
    energy = np.array([[b["energy"]["low"], b["energy"]["mid"], b["energy"]["high"]] for b in bars]).T
    F = np.vstack([energy, bar_chroma])
    F = (F - F.mean(axis=1, keepdims=True)) / (F.std(axis=1, keepdims=True) + 1e-9)
    # Weight AFTER standardizing (weighting before is undone by the z-score). 3 energy rows
    # vs 12 chroma rows: x2 equalizes their total contribution, x3 makes energy dominate,
    # since energy changes are what DJ structure is made of.
    F[:3] *= 3.0
    Fn = F / (np.linalg.norm(F, axis=0, keepdims=True) + 1e-9)
    ssm = Fn.T @ Fn

    half = 4
    K = _checkerboard(half)
    padded = np.pad(ssm, half, mode="edge")
    novelty = np.array([np.sum(K * padded[i:i + 2 * half, i:i + 2 * half]) for i in range(n)])
    novelty[:4] = 0  # no boundary in the first bar group

    thresh = novelty.mean() + 0.5 * novelty.std()
    cands = [i for i in range(1, n - 1) if novelty[i] >= thresh and novelty[i] == novelty[max(0, i - 4):i + 5].max()]
    snapped = sorted({int(round(i / 4) * 4) for i in cands})
    bounds = [0]
    for s in snapped:
        if s - bounds[-1] >= 8 and n - s >= 8:
            bounds.append(s)
    bounds.append(n)
    return [_section(k, bounds[k], bounds[k + 1], bars) for k in range(len(bounds) - 1)]


def _section(index: int, start: int, end: int, bars: list[dict]) -> dict:
    return {"index": index, "start_bar": start, "end_bar": end, "start_s": bars[start]["start_s"]}


def label_sections(sections: list[dict], bars: list[dict]) -> list[dict]:
    """Rough labels from energy relative to the track's loudest section. Deliberately
    coarse; semantic labels (build, drop, verse) come from tagging later.

    r = E_section / E_max with E = mean of the three normalized bands.
    level: high if r >= 0.85, mid if r >= 0.6, else low.
    label: first/last non-high section -> intro/outro; a middle section whose low band
    (kick + bass) is under 0.4 -> break; everything else -> main.
    """
    def mean_band(s, band=None):
        rng = range(s["start_bar"], s["end_bar"])
        if band:
            return float(np.mean([bars[i]["energy"][band] for i in rng]))
        return float(np.mean([np.mean(list(bars[i]["energy"].values())) for i in rng]))

    e = [mean_band(s) for s in sections]
    e_max = max(max(e), 1e-9)
    last = len(sections) - 1
    for s, es in zip(sections, e):
        r = es / e_max
        s["energy_level"] = "high" if r >= 0.85 else "mid" if r >= 0.6 else "low"
        if last > 0 and s["index"] == 0 and r < 0.85:
            s["label"] = "intro"
        elif last > 0 and s["index"] == last and r < 0.85:
            s["label"] = "outro"
        elif mean_band(s, "low") < 0.4:
            s["label"] = "break"
        else:
            s["label"] = "main"
    return sections


def build_phrases(bars: list[dict], sections: list[dict], phrase_bars: int, offset_bars: int = 0) -> list[dict]:
    phrases, start, k = [], offset_bars % phrase_bars, 0
    if start > 0:  # leading partial phrase
        phrases.append((0, start))
    while start < len(bars):
        phrases.append((start, min(phrase_bars, len(bars) - start)))
        start += phrase_bars
    out = []
    for k, (s, nb) in enumerate(phrases):
        sec = next(x["index"] for x in sections if x["start_bar"] <= s < x["end_bar"])
        out.append({"index": k, "start_bar": s, "n_bars": nb, "start_s": bars[s]["start_s"], "section_index": sec})
    return out


# ---------------------------------------------------------------- key

def camelot(pitch_class: int, mode: str) -> str:
    """Camelot code. Major: C=8B, and each step up a fifth (+7 semitones) adds 1.
    Minor uses its relative major (3 semitones up): A minor -> C major -> 8A."""
    pc = pitch_class if mode == "major" else (pitch_class + 3) % 12
    num = ((7 * pc) % 12 + 7) % 12 + 1
    return f"{num}{'B' if mode == 'major' else 'A'}"


def estimate_key(y: np.ndarray, sr: int) -> dict:
    """Average chroma of the harmonic component, correlated (Pearson) against the
    24 rotated Krumhansl-Kessler profiles; best correlation wins.
    Confidence = gap between best and runner-up correlation, scaled to 0..1.
    Known limitation: relative major/minor (8A vs 8B) share a pitch set and are often
    confused. They are mix-compatible, so treat the Camelot number as the reliable part."""
    harmonic = librosa.effects.harmonic(y)
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, hop_length=HOP,
                                        fmin=CHROMA_FMIN_HZ, n_octaves=6).mean(axis=1)
    scores = []
    for mode, prof in (("major", KK_MAJOR), ("minor", KK_MINOR)):
        for pc in range(12):
            scores.append((float(np.corrcoef(chroma, np.roll(prof, pc))[0, 1]), pc, mode))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    conf = float(np.clip((best[0] - second[0]) / 0.2, 0.0, 1.0))
    return {"name": f"{PITCH_NAMES[best[1]]} {best[2]}", "camelot": camelot(best[1], best[2]),
            "confidence": round(conf, 3)}


# ---------------------------------------------------------------- waveform

def build_waveform(y: np.ndarray, sr: int) -> dict:
    """Three-band display waveform, ~100 columns/s, each value 0..255.
    amplitude = sqrt(band power); scaled by the band's 99th percentile and gamma 0.7
    so quiet detail stays visible."""
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=WAVE_HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    amp = np.sqrt(_band_power(S, freqs))
    ref = np.percentile(amp, 99, axis=1, keepdims=True) + 1e-9
    scaled = np.clip((amp / ref) ** 0.7, 0, 1) * 255
    q = scaled.round().astype(int)
    return {"schema_version": 1, "points_per_second": sr / WAVE_HOP,
            "low": q[0].tolist(), "mid": q[1].tolist(), "high": q[2].tolist()}

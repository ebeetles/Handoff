"""Generate two synthetic house-style tracks with separate stems and KNOWN ground truth.

    python make_demo_tracks.py --out tracks/

Writes tracks/<name>.wav plus tracks/<name>.stems/{drums,bass,vocals,other}.wav, where the
stems sum exactly to the mix. Lets you run the whole board (stems included) without
real music or Demucs, and gives the pipeline tests a ground truth to check against.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"])}


def hz(name: str, octave: int) -> float:
    return 440.0 * 2 ** ((NOTE[name] + 12 * (octave + 1) - 69) / 12)


def env_exp(n: int, tau_s: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau_s * SR))


def kick(n=int(0.35 * SR)):
    t = np.arange(n) / SR
    f = 45 + 75 * np.exp(-t / 0.04)                       # pitch sweep 120 -> 45 Hz
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(n, 0.12)


def noise_burst(n, tau, rng, hp=0.0):
    x = rng.standard_normal(n)
    if hp:  # crude one-pole high-pass
        a = np.exp(-2 * np.pi * hp / SR)
        y = np.zeros_like(x); prev_x = prev_y = 0.0
        for i, v in enumerate(x):
            prev_y = a * (prev_y + v - prev_x); prev_x = v; y[i] = prev_y
        x = y
    return x * env_exp(n, tau)


def tone(freq, n, harmonics=(1.0,), tau=None, vibrato=0.0):
    t = np.arange(n) / SR
    ph = 2 * np.pi * freq * t + (vibrato * np.sin(2 * np.pi * 5.5 * t) if vibrato else 0)
    x = sum(a * np.sin((k + 1) * ph) for k, a in enumerate(harmonics))
    if tau:
        x *= env_exp(n, tau)
    fade = min(n, int(0.005 * SR))
    x[:fade] *= np.linspace(0, 1, fade); x[-fade:] *= np.linspace(1, 0, fade)
    return x


def add(buf, start_s, x, gain=1.0):
    i = int(round(start_s * SR))
    if i >= len(buf):
        return
    j = min(len(buf), i + len(x))
    buf[i:j] += gain * x[: j - i]


def render(bpm, lead_s, pickup_beats, chords, sections, melody, seed):
    """sections: list of (n_bars, set_of_parts). chords: list of (root, octave, quality) per bar cycle."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    total_bars = sum(n for n, _ in sections)
    dur = lead_s + (pickup_beats + total_bars * 4) * beat + 2.0
    stems = {k: np.zeros(int(dur * SR)) for k in ("drums", "bass", "vocals", "other")}
    k_snd, clap, hat = kick(), noise_burst(int(0.18 * SR), 0.05, rng, hp=900), noise_burst(int(0.05 * SR), 0.012, rng, hp=6000)
    crash = noise_burst(int(1.5 * SR), 0.5, rng, hp=4000)
    # pickup: a lone hat/clap on the beat(s) before bar 1 (tests downbeat detection)
    for p in range(pickup_beats):
        add(stems["drums"], lead_s + p * beat, clap, 0.5)
    t0 = lead_s + pickup_beats * beat
    bar_i = 0
    for n_bars, parts in sections:
        for b in range(n_bars):
            bar_t = t0 + bar_i * 4 * beat
            root, octv, quality = chords[bar_i % len(chords)]
            third = 3 if quality == "m" else 4
            if "drums" in parts:
                for q in range(4):
                    add(stems["drums"], bar_t + q * beat, k_snd, 0.9)
                    add(stems["drums"], bar_t + (q + 0.5) * beat, hat, 0.25)
                    if q in (1, 3):
                        add(stems["drums"], bar_t + q * beat, clap, 0.45)
                if bar_i % 8 == 0:
                    add(stems["drums"], bar_t, crash, 0.3)
            if "bass" in parts:
                f = hz(root, octv)
                add(stems["bass"], bar_t, tone(f, int(beat * 0.9 * SR), (1, .5, .25), tau=0.4), 0.55)  # beat-1 accent
                for q in range(4):
                    add(stems["bass"], bar_t + (q + 0.5) * beat, tone(f, int(beat * 0.4 * SR), (1, .5, .25), tau=0.15), 0.4)
            if "other" in parts:
                for semis in (0, third, 7):
                    f = hz(root, octv + 2) * 2 ** (semis / 12)
                    add(stems["other"], bar_t, tone(f, int(4 * beat * SR), (1, .3), tau=1.6), 0.12)
            if "vocals" in parts:
                for k, (note, octave, start_beat, len_beats) in enumerate(melody[bar_i % 2]):
                    add(stems["vocals"], bar_t + start_beat * beat,
                        tone(hz(note, octave), int(len_beats * beat * SR), (1, .6, .3, .15), vibrato=0.6), 0.16)
            bar_i += 1
    mix = sum(stems.values())
    scale = 0.89 / np.max(np.abs(mix))                        # -1 dBFS peak; same scale on stems
    stems = {k: v * scale for k, v in stems.items()}
    truth = {"bpm": bpm, "first_downbeat_s": t0, "pickup_beats": pickup_beats, "total_bars": total_bars}
    return mix * scale, stems, truth


def stereo(x, pan=0.0):
    return np.stack([x * np.sqrt(0.5 - pan / 2), x * np.sqrt(0.5 + pan / 2)], axis=1)


TRACKS = {
    "Demo - Amber Room": dict(
        bpm=124, lead_s=0.37, pickup_beats=0, seed=1,
        chords=[("A", 2, "m"), ("F", 2, ""), ("C", 3, ""), ("G", 2, "")],      # A minor -> 8A
        sections=[(16, {"drums"}), (16, {"drums", "bass", "other"}), (8, {"other", "vocals"}),
                  (16, {"drums", "bass", "other", "vocals"}), (8, {"drums"})],
        melody=[[("E", 4, 0, 1), ("C", 4, 1, 1), ("A", 3, 2, 2)], [("G", 4, 0, 2), ("E", 4, 2, 2)]],
        key="8A"),
    "Demo - Teal Signal": dict(
        bpm=128, lead_s=0.10, pickup_beats=1, seed=2,
        chords=[("E", 2, "m"), ("C", 3, ""), ("G", 2, ""), ("D", 3, "")],      # E minor -> 9A
        sections=[(16, {"drums", "other"}), (16, {"drums", "bass", "other"}), (8, {"other", "vocals"}),
                  (16, {"drums", "bass", "other", "vocals"}), (8, {"drums", "other"})],
        melody=[[("B", 4, 0, 1.5), ("G", 4, 1.5, 0.5), ("E", 4, 2, 2)], [("D", 5, 0, 2), ("B", 4, 2, 2)]],
        key="9A"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("tracks"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    truth_all = {}
    for name, cfg in TRACKS.items():
        key = cfg.pop("key")
        mix, stems, truth = render(**cfg)
        truth["camelot"] = key
        pans = {"drums": 0.0, "bass": 0.0, "vocals": 0.1, "other": -0.15}
        st = {k: stereo(v, pans[k]) for k, v in stems.items()}
        sf.write(args.out / f"{name}.wav", sum(st.values()), SR, subtype="PCM_16")
        d = args.out / f"{name}.stems"; d.mkdir(exist_ok=True)
        for k, v in st.items():
            sf.write(d / f"{k}.wav", v, SR, subtype="PCM_16")
        truth_all[f"{name}.wav"] = truth
        cfg["key"] = key
        print(f"wrote {name}: {truth}")
    (args.out / "ground_truth.json").write_text(json.dumps(truth_all, indent=1))


if __name__ == "__main__":
    main()

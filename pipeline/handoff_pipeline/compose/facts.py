"""Musical facts for the composer and the critic, from a track's analysis.json (and its stems,
when it has them).

Two views of the same track:
  TrackData  per-bar numbers (band energy, loudness, vocal presence) for the critic's simulation;
  summary()  a compact, per-phrase, discretized description for the LLM. Never raw arrays:
             the model reasons better about "phrase at bar 64: breakdown, low energy, falling,
             light bass, no vocals" than about 200 floats.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .mix import best_key_shift, camelot_distance, choose_sync, key_shift_options, ride_sync

MIN_EXIT_BARS = 8   # a transition needs at least this much of the outgoing track left


@dataclass
class TrackData:
    id: str
    title: str
    artist: str
    bpm: float
    camelot: str
    key: str
    beatmatchable: bool
    has_stems: bool
    n_bars: int
    phrases: list[dict]
    sections: list[dict]
    low: np.ndarray        # per bar, 0..1 within the track
    mid: np.ndarray
    high: np.ndarray
    rms_db: np.ndarray     # per bar, absolute (comparable across tracks)
    presence: dict[str, np.ndarray] | None   # per stem, per bar, 0..1; None = unknown (no stems)
    cues: list[dict] = field(default_factory=list)    # TrackAnalysis v4: drops, builds, breakdowns, vocal in/out
    hooks: list[dict] = field(default_factory=list)   # TrackAnalysis v4: most repeated vocal / instrumental phrase

    @property
    def energy(self) -> np.ndarray:
        return (self.low + self.mid + self.high) / 3

    @property
    def vocals(self) -> np.ndarray | None:
        return None if self.presence is None else self.presence["vocals"]

    @property
    def drums(self) -> np.ndarray | None:
        return None if self.presence is None else self.presence["drums"]

    @property
    def tonal(self) -> np.ndarray | None:
        """Per bar: the strongest pitched part (bass, other, vocals). Drums have no key."""
        if self.presence is None:
            return None
        return np.maximum.reduce([self.presence[k] for k in ("bass", "other", "vocals")])


STEM_DB = (-24.0, -14.0)   # stem vs mix, per bar: 0 presence at the first, 1 at the second
STEM_NAMES = ("drums", "bass", "vocals", "other")


def stem_presence(track_dir: Path, analysis: dict) -> dict[str, np.ndarray] | None:
    """Per stem, per bar presence (0..1): how loud the stem is against the whole mix in that bar,
    from 0 at -24 dB to 1 at -14 dB. Cached as stems.json next to the track.

    Against the mix, not against the stem's own loud bars: an instrumental track's vocal stem
    holds only bleed (~45-65 dB under the mix on this library), which a relative measure called
    "lots of vocals" everywhere. Real vocals sat 3-17 dB under the mix (CR0SS, Protohype, zaza).
    The same scale finds beatless bars (drums stem near bleed level) and drums-only bars."""
    stems = analysis["audio"]["stems"]
    if not stems:
        return None
    if analysis["bars"] and "stems" in analysis["bars"][0]:   # TrackAnalysis v4 carries it per bar
        return {k: np.array([b["stems"][k] for b in analysis["bars"]]) for k in STEM_NAMES}
    cache = track_dir / "stems.json"
    starts = [b["start_s"] for b in analysis["bars"]]
    if cache.exists():
        c = json.loads(cache.read_text())
        if c.get("bars") == len(starts) and c.get("method") == "vs_mix" and set(c.get("presence", {})) == set(STEM_NAMES):
            return {k: np.array(v) for k, v in c["presence"].items()}
    import librosa   # only needed for tracks with stems
    sr = 11025
    gain = 10 ** ((analysis["audio"].get("stems_gain_db") or 0.0) / 20)
    mix, _ = librosa.load(track_dir / analysis["audio"]["mix"], sr=sr, mono=True)
    mix = mix - mix.mean()
    out = {}
    for name in STEM_NAMES:
        y, _ = librosa.load(track_dir / stems[name], sr=sr, mono=True)
        n = min(len(y), len(mix))
        y, m = (y[:n] - y[:n].mean()) * gain, mix[:n]
        edges = [int(s * sr) for s in starts] + [n]
        share = np.array([10 * np.log10((np.mean(y[a:max(b, a + 1)] ** 2) + 1e-12) / (np.mean(m[a:max(b, a + 1)] ** 2) + 1e-12))
                          for a, b in zip(edges[:-1], edges[1:])])
        lo, hi = STEM_DB
        out[name] = np.clip((share - lo) / (hi - lo), 0, 1)
    cache.write_text(json.dumps({"bars": len(starts), "method": "vs_mix",
                                 "presence": {k: [round(float(p), 3) for p in v] for k, v in out.items()}}))
    return out


def load_track(track_dir: Path) -> TrackData:
    a = json.loads((track_dir / "analysis.json").read_text())
    bars = a["bars"]
    band = lambda k: np.array([b["energy"][k] for b in bars], dtype=float)  # noqa: E731
    return TrackData(
        id=a["id"], title=a["title"], artist=a["artist"], bpm=a["tempo"]["bpm"], camelot=a["key"]["camelot"],
        key=a["key"]["name"], beatmatchable=a["tempo"]["beatmatchable"], has_stems=a["audio"]["stems"] is not None,
        n_bars=len(bars), phrases=a["phrases"], sections=a["sections"],
        low=band("low"), mid=band("mid"), high=band("high"), rms_db=np.array([b["rms_db"] for b in bars], dtype=float),
        presence=stem_presence(track_dir, a), cues=a.get("cues", []), hooks=a.get("hooks", []),
    )


def _level(x: float) -> str:
    return "low" if x < 0.35 else "mid" if x < 0.65 else "high"


def _amount(v: float) -> str:
    return "none" if v < 0.15 else "some" if v < 0.6 else "lots"


def summary(t: TrackData) -> dict:
    """Per-phrase facts, discretized."""
    phrases = []
    for p in t.phrases:
        s, e = p["start_bar"], p["start_bar"] + p["n_bars"]
        en = t.energy[s:e]
        half = max(1, len(en) // 2)
        delta = float(en[half:].mean() - en[:half].mean()) if len(en) > 1 else 0.0
        voc = "unknown" if t.vocals is None else _amount(float(t.vocals[s:e].mean()))
        drums = "unknown" if t.drums is None else _amount(float(t.drums[s:e].mean()))
        phrases.append({
            "bar": s, "bars": p["n_bars"], "section": t.sections[p["section_index"]]["label"],
            "energy": _level(float(en.mean())),
            "trend": "rising" if delta > 0.12 else "falling" if delta < -0.12 else "steady",
            "bass": {"low": "light", "mid": "medium", "high": "heavy"}[_level(float(t.low[s:e].mean()))],
            "vocals": voc, "drums": drums,
        })
    return {
        "title": f"{t.artist} - {t.title}", "bpm": round(t.bpm, 2), "key": f"{t.key} ({t.camelot})",
        "steady_tempo": t.beatmatchable, "stems": t.has_stems, "bars": t.n_bars,
        "loudness_db": round(float(np.median(t.rms_db)), 1), "phrases": phrases,
        # Structure (rule-based, see the system prompt): what each section is, the moments, the hooks.
        "sections": [{"bars": f"{s['start_bar']}-{s['end_bar']}", "label": s["label"], "energy": s["energy_level"],
                      "vocals": s.get("vocals", "unknown"), "group": s.get("group")} for s in t.sections],
        "cues": [{"kind": c["kind"], "bar": c["bar"], **({"bars": c["bars"]} if c["bars"] > 1 else {})} for c in t.cues],
        "hooks": [{"kind": h["kind"], "bars": h["bars"], "starts": h["starts"][:8]} for h in t.hooks],
        "four_bar_windows": [
            {"bar": s, "energy": round(float(t.energy[s:s + 4].mean()), 2),
             "bass": round(float(t.low[s:s + 4].mean()), 2),
             "vocals": None if t.vocals is None else round(float(t.vocals[s:s + 4].mean()), 2),
             # drums 0 = beatless (any tempo can run under it); tonal 0 = drums only (any key can sit over it)
             "drums": None if t.drums is None else round(float(t.drums[s:s + 4].mean()), 2),
             "tonal": None if t.tonal is None else round(float(t.tonal[s:s + 4].mean()), 2)}
            for s in range(0, t.n_bars, 4)
        ],
    }


def exit_bars(t: TrackData) -> list[int]:
    """Every phrase with enough runway, including the first half of a track."""
    return [p["start_bar"] for p in t.phrases
            if t.n_bars - p["start_bar"] >= MIN_EXIT_BARS]


def entry_bars(t: TrackData) -> list[int]:
    """B may enter at any phrase, including a later drop or vocal section."""
    return [p["start_bar"] for p in t.phrases if t.n_bars - p["start_bar"] >= MIN_EXIT_BARS]


def lock(a: TrackData, b: TrackData) -> tuple[float, float, float] | None:
    """How the pair can be beatmatched: (multiplier, out_rate, in_rate). Directly when B's tempo
    fader reaches (out_rate 1), else after a tempo ride on A. None if the tempos can't meet."""
    if not (a.beatmatchable and b.beatmatchable):
        return None
    sync = choose_sync(b.bpm, a.bpm)
    return (sync[0], 1.0, sync[1]) if sync else ride_sync(a.bpm, b.bpm)


def pair_facts(a: TrackData, b: TrackData) -> dict:
    steady = a.beatmatchable and b.beatmatchable
    sync = choose_sync(b.bpm, a.bpm) if steady else None
    ride = ride_sync(a.bpm, b.bpm) if steady else None
    dist = camelot_distance(a.camelot, b.camelot)
    return {
        "tempo": {"out_bpm": round(a.bpm, 2), "in_bpm": round(b.bpm, 2), "both_steady": steady,
                  "gap_pct": round((b.bpm / a.bpm - 1) * 100, 1),
                  "sync": None if sync is None else {"multiplier": sync[0], "rate_change_pct": round((sync[1] - 1) * 100, 2)},
                  "tempo_ride": None if ride is None else {"multiplier": ride[0], "out_rate_change_pct": round((ride[1] - 1) * 100, 2),
                                                           "in_rate_change_pct": round((ride[2] - 1) * 100, 2) + 0.0}},
        # Key lock: tempo changes don't move pitch, so the keys meet as written, unless B is
        # transposed (in_key_shift) before it comes in.
        "key": {"out": a.camelot, "in": b.camelot, "distance": dist, "compatible": dist <= 1,
                "shift_options": key_shift_options(a.camelot, b.camelot),
                "best_in_key_shift": best_key_shift(a.camelot, b.camelot)},
        "stems": {"out": a.has_stems, "in": b.has_stems},
        "exit_bars": exit_bars(a), "entry_bars": entry_bars(b),
    }

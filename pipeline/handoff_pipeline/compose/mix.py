"""Ports of the web board's mixing math (web/src/audio/mapping.ts, sync.ts, coDJ/lanes.ts), so
the critic simulates exactly what the board will do. Keep in step with the TypeScript;
tests/test_compose.py checks chooseSync against contracts/fixtures/sync_cases.json like the web."""
from __future__ import annotations

import math
import re

TEMPO_RANGE = 0.08
DEFAULTS = {"eqLow": 0.5, "eqMid": 0.5, "eqHigh": 0.5, "filter": 0.5, "volume": 0.8, "echo": 0.0, "tempo": 0.5,
            "stem.drums": 1.0, "stem.bass": 1.0, "stem.vocals": 1.0, "stem.other": 1.0, "xfader": 0.0}


def clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


def eq_db(v: float) -> float:
    v = clamp01(v)
    return 6 * (v - 0.5) / 0.5 if v >= 0.5 else -40 * ((0.5 - v) / 0.5) ** 1.3


def filter_params(v: float) -> tuple[float, float]:
    """(lowpass_hz, highpass_hz) for the one-knob filter."""
    v = clamp01(v)
    dead = 0.02
    if v < 0.5 - dead:
        d = (0.5 - dead - v) / (0.5 - dead)
        return 20000 * (60 / 20000) ** d, 10.0
    if v > 0.5 + dead:
        d = (v - 0.5 - dead) / (0.5 - dead)
        return 22000.0, 20 * (8000 / 20) ** d
    return 22000.0, 10.0


def volume_gain(v: float) -> float:
    return clamp01(v) ** 2


def xfade_gains(x: float) -> tuple[float, float]:
    """Equal-power gains (out, in) for an out(0) -> in(1) crossfader value."""
    th = clamp01(x) * math.pi / 2
    return math.cos(th), math.sin(th)


def choose_sync(my_bpm: float, other_bpm: float, rng: float = TEMPO_RANGE) -> tuple[float, float] | None:
    """(multiplier, rate) to play at my_bpm alongside other_bpm, preferring 1x, or None."""
    best = None
    for m in (1, 0.5, 2):
        rate = other_bpm * m / my_bpm
        if abs(rate - 1) <= rng + 1e-9 and (best is None or abs(rate - 1) < abs(best[1] - 1) - 1e-9):
            best = (m, rate)
    return best


def tempo_rate(v: float) -> float:
    """Tempo fader (0..1) -> playback rate, like mapping.ts tempoRate."""
    return 1 + (clamp01(v) - 0.5) * 2 * TEMPO_RANGE


def tempo_value(rate: float) -> float:
    """Playback rate -> tempo fader (0..1), like mapping.ts rateToTempo."""
    return clamp01(0.5 + (rate - 1) / (2 * TEMPO_RANGE))


def ride_sync(out_bpm: float, in_bpm: float, rng: float = TEMPO_RANGE) -> tuple[float, float, float] | None:
    """(multiplier, out_rate, in_rate) after a tempo ride: the outgoing deck glides as far as its
    tempo fader allows towards the incoming track's own tempo, and the incoming deck syncs to it
    for the rest. Reaches pairs up to about +-16% apart (8% each); the incoming deck plays at its
    own tempo whenever the gap is within the fader range. None if no multiplier gets there."""
    best = None
    for m in (1, 0.5, 2):
        r_out = min(1 + rng, max(1 - rng, in_bpm / (out_bpm * m)))
        r_in = out_bpm * r_out * m / in_bpm
        if abs(r_in - 1) <= rng + 1e-9 and (best is None or abs(r_in - 1) + abs(r_out - 1) < abs(best[2] - 1) + abs(best[1] - 1) - 1e-9):
            best = (m, r_out, r_in)
    return best


def pitch_offset(out_rate: float, in_rate: float) -> float:
    """Semitones the incoming deck sounds above the outgoing one, from their playback rates.
    The board has no key lock: speeding a deck up raises its pitch (6% = 1 semitone). Once two
    decks are locked, the offset is fixed by their tempo ratio, whichever deck moved."""
    return 12 * math.log2(in_rate / out_rate)


def transpose_camelot(c: str, semitones: int) -> str:
    """Camelot key shifted by whole semitones (one semitone = 7 steps round the wheel)."""
    p = re.fullmatch(r"(\d{1,2})([AB])", c or "")
    if not p:
        return c
    return f"{(int(p[1]) - 1 + 7 * semitones) % 12 + 1}{p[2]}"


DETUNE_FREE_CENTS = 40   # calibrated by ear: zaza <-> EvenFall blends, 41 cents apart, "worked really well"


def _detune(cents: float) -> float:
    """Out-of-tune cost of two tonal parts `cents` apart: nothing up to DETUNE_FREE_CENTS, then
    rising to half a key step at 60 cents (a deck that far off sits nearer the next semitone)."""
    return max(0.0, abs(cents) - DETUNE_FREE_CENTS) / 40


def key_relation(out_key: str, in_key: str, semitones: float) -> dict:
    """How the incoming key sounds against the outgoing one when it plays `semitones` higher:
    the kinder of the two neighbouring whole-semitone keys (a deck 50 cents off is as far from
    both), its Camelot distance from the outgoing key, and the leftover detune in cents."""
    ks = sorted({math.floor(semitones), math.ceil(semitones)},
                key=lambda k: max(0, camelot_distance(out_key, transpose_camelot(in_key, k)) - 1) + _detune(100 * (semitones - k)))
    heard = transpose_camelot(in_key, ks[0])
    return {"in_sounds_as": heard, "distance": camelot_distance(out_key, heard), "detune_cents": round(100 * (semitones - ks[0]))}


def tonal_clash(out_key: str, in_key: str, semitones: float) -> float:
    """0 = two tonal parts sit together fine; +1 per Camelot step past compatible (distance 1),
    plus the detune cost."""
    r = key_relation(out_key, in_key, semitones)
    return max(0, r["distance"] - 1) + _detune(r["detune_cents"])


def camelot_distance(a: str, b: str) -> int:
    pa, pb = re.fullmatch(r"(\d{1,2})([AB])", a or ""), re.fullmatch(r"(\d{1,2})([AB])", b or "")
    if not pa or not pb:
        return 99
    d = abs(int(pa[1]) - int(pb[1])) % 12
    return min(d, 12 - d) + (0 if pa[2] == pb[2] else 1)


def lane_value_at(points: list[list[float]], bar: float) -> float:
    """Same as web lanes.ts laneValueAt: hold, linear, steps take the later value."""
    i = -1
    while i + 1 < len(points) and points[i + 1][0] <= bar:
        i += 1
    if i < 0:
        return points[0][1]
    if i == len(points) - 1:
        return points[i][1]
    (b0, v0), (b1, v1) = points[i], points[i + 1]
    return v0 + (v1 - v0) * (bar - b0) / (b1 - b0)

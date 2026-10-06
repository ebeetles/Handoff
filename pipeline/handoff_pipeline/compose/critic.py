"""Rules-based critic: simulate a compiled transition bar by bar and score it.

The simulation runs every quarter bar from the transition start to its end. For each deck it
takes that bar's analysis (per-band energy 0..1, loudness in dB, vocal presence) and applies
what the recipe does to it, with the board's own curves (mix.py): crossfader (equal power),
volume, 3-band EQ, filter (2nd-order magnitude at band centers), and stem mutes (a fixed
stem->band profile). That gives per-deck band presence and total loudness, from which:

  bass clash    both decks' basslines strong at once        (muddy, the classic mistake). The bass
                part, not the whole low band: overlapping kicks are just a beatmatched blend.
                On tracks with stems a muted bass stem removes it; otherwise only the low EQ,
                filter, fader and crossfader do (they take the kick with it, as on a real mixer).
  vocal clash   both decks voiced and audible at once       (only when vocals are known)
  key clash     both decks' pitched parts (bass, other, vocals; drums have no key) audible at once,
                weighted by how far apart the keys sound. The board has no key lock, so a locked
                deck is transposed by its tempo change: the keys are compared as heard, with any
                detune (mix.tonal_clash). Without stems, the mid band stands in for pitched parts.
  rhythm clash  when the decks aren't beatmatched (tempos can't lock, or the plan doesn't sync):
                both decks' rhythmic parts (drums, bass, half of "other") audible at once. One bar
                of it is a train wreck, and the plan is invalid. A beat under a beatless breakdown,
                or a vocal floating over the other track's drums, is fine at any tempo.
  dip / spike   total loudness falls > 6 dB below, or rises > 3 dB above, the two tracks' own levels
  structure     A exits from a calm/outro phrase, B enters on a phrase start, energy carries on,
                overlap not endless, the plan not overstuffed, tempo stretch small
  clean blend   credit per bar both decks are heard together with no clash (up to 16 bars). Without
                it, never overlapping was the safest score: on the real library an echo out won
                172 of 240 pairs, so any blend lost to a cut.
  tempo ride    sliding A's pitch while its melody plays (a ride under drums-only bars is free)
  validity      no rhythm clash; without stems, any unlocked overlap

Deterministic and cheap: it scores many candidates and explains itself (reasons) for the UI and
the Chunk 10 voice. Weights are first guesses, to be calibrated against listening (Chunk 11).
"""
from __future__ import annotations

import math

import numpy as np

from .facts import TrackData
from .mix import (DEFAULTS, choose_sync, eq_db, filter_params, lane_value_at, pitch_offset, tempo_rate, tonal_clash,
                  volume_gain, xfade_gains)

BANDS = ("low", "mid", "high")
CENTERS = {"low": 90.0, "mid": 900.0, "high": 6000.0}
# Share of each band carried by each stem (amplitude; each band sums to 1).
STEM_BANDS = {"drums": (0.45, 0.2, 0.6), "bass": (0.55, 0.15, 0.05), "vocals": (0.0, 0.35, 0.15), "other": (0.0, 0.3, 0.2)}
STEP = 0.125  # half-beat volume gates need both halves represented
STRONG = 0.3      # band presence that counts as "there"
RHYTHM = {"drums": 1.0, "bass": 0.7, "other": 0.5}   # how rhythmic each stem is, for unlocked overlaps
W = {"bass_clash": 4.0, "vocal_clash": 3.0, "key_clash": 2.0, "ride_pitch": 0.5, "dip": 3.0, "spike": 2.0, "mid_phrase_entry": 5.0,
     "energy_drop": 6.0, "long_overlap": 0.5, "complexity": 2.0, "stretch": 4.0, "calm_exit": 4.0, "clean_blend": 0.5}
BASE = 88.0          # bonuses (up to +12) need headroom below the 100 cap
CLEAN_BLEND_MAX = 16  # bars of clean overlap that earn credit


def _lane(recipe: dict, deck: str | None, control: str):
    pts = next((l["points"] for l in recipe["lanes"] if l["deck"] == deck and l["control"] == control), None)
    return (lambda x: lane_value_at(pts, x)) if pts else (lambda x: DEFAULTS[control])


def _filter_gain(v: float, band: str) -> float:
    lp, hp = filter_params(v)
    f = CENTERS[band]
    return 1 / math.sqrt(1 + (f / lp) ** 4) / math.sqrt(1 + (hp / f) ** 4)


def _deck_state(recipe: dict, deck: str, x: float, xf_gain: float, has_stems: bool) -> dict:
    """Per-band amplitude factor and vocal factor for one deck at recipe bar x."""
    vol = volume_gain(_lane(recipe, deck, "volume")(x)) / volume_gain(0.8)
    on = {s: _lane(recipe, deck, f"stem.{s}")(x) if has_stems else 1.0 for s in STEM_BANDS}
    bands = {}
    for i, k in enumerate(BANDS):
        eq = 10 ** (eq_db(_lane(recipe, deck, {"low": "eqLow", "mid": "eqMid", "high": "eqHigh"}[k])(x)) / 20)
        stem = sum(w[i] * on[s] for s, w in STEM_BANDS.items())
        bands[k] = xf_gain * vol * eq * _filter_gain(_lane(recipe, deck, "filter")(x), k) * stem
    low_path = bands["low"] / max(sum(w[0] * on[s] for s, w in STEM_BANDS.items()), 1e-9)   # the low band without the stem mix
    # Each stem's level through the deck's path: its band profile, weighted by EQ and filter.
    path = [bands[k] / max(sum(w[i] * on[s] for s, w in STEM_BANDS.items()), 1e-9) for i, k in enumerate(BANDS)]
    stem_gain = {s: on[s] * sum(w[i] * path[i] for i in range(3)) / sum(w) for s, w in STEM_BANDS.items()}
    return {"bands": bands, "bass_gain": low_path * on["bass"], "stem_gain": stem_gain,
            "vocal_gain": bands["mid"] * on["vocals"] / max(sum(w[1] * on[s] for s, w in STEM_BANDS.items()), 1e-9)}


def _parts(t: TrackData, i: int, st: dict, pb: dict) -> tuple[float, float]:
    """(pitched, rhythmic) level of a deck in bar i: from the stems' presence when known, else
    the mid band (pitched) and the loudest band (rhythmic; unused: see the rhythm clash)."""
    if t.presence is None:
        return pb["mid"], max(pb.values())
    lv = {s: float(t.presence[s][i]) * st["stem_gain"][s] for s in STEM_BANDS}
    return max(lv["bass"], lv["other"], lv["vocals"]), max(lv[s] * w for s, w in RHYTHM.items())


def _at(xs: list[float]) -> str:
    """Where, in transition bars: " (bars 4-6, 12-13)" from the simulation steps that hit. Hits
    under half a bar apart join one span (a gate's chops would otherwise list every half beat)."""
    spans: list[list[float]] = []
    for x in xs:
        if spans and x - spans[-1][1] <= 0.5 + 1e-9:
            spans[-1][1] = x
        else:
            spans.append([x, x])
    parts = [f"{a:g}" if b - a < STEP / 2 else f"{a:g}-{b + STEP:g}" for a, b in spans[:4]]
    return f" (bar{'s' if len(spans) > 1 or spans[0][1] > spans[0][0] else ''} {', '.join(parts)}{', ...' if len(spans) > 4 else ''})" if spans else ""


def critique(recipe: dict, a: TrackData, b: TrackData, n_moves: int = 0) -> dict:
    anc, length = recipe["anchor"], recipe["bars"]
    start, fb = anc["out_start_bar"], anc["in_from_bar"]
    enter = next(e["at_bar"] for e in recipe["events"] if e["deck"] == "in" and e["command"] == "play")
    rolls = []
    roll_start = None
    for e in recipe["events"]:
        if e["command"] == "loop" and roll_start is None:
            roll_start = e["at_bar"]
        elif e["command"] == "loop_off" and roll_start is not None:
            rolls.append((roll_start, e["at_bar"]))
            roll_start = None
    if roll_start is not None:
        rolls.append((roll_start, length))
    xf = _lane(recipe, None, "xfader")
    rate_a = lambda x: tempo_rate(_lane(recipe, "out", "tempo")(x))   # noqa: E731  (A's ride; 1 = its own tempo)
    locked = recipe["requires"]["beatmatchable"]
    sync = choose_sync(b.bpm, a.bpm * rate_a(enter)) if a.beatmatchable and b.beatmatchable else None
    rate_b = sync[1] if locked and sync else 1.0
    m = {"bass_clash_bars": 0.0, "vocal_clash_bars": 0.0, "key_clash_bars": 0.0, "rhythm_clash_bars": 0.0,
         "ride_tonal_bars": 0.0, "dip_bars": 0.0, "spike_bars": 0.0, "overlap_bars": 0.0, "clean_overlap_bars": 0.0,
         "locked": locked, "vocals_known": a.vocals is not None and b.vocals is not None}
    where: dict[str, list[float]] = {k: [] for k in ("bass_clash", "vocal_clash", "key_clash", "rhythm_clash", "dip", "spike")}
    ride = [p[0] for p in next((l["points"] for l in recipe["lanes"] if l["deck"] == "out" and l["control"] == "tempo"), [])]
    ride_span = (min(ride), max(ride)) if ride else None
    ref_a = float(np.median(a.rms_db[max(0, start - 4):start + 1]))
    end_b = min(b.n_bars - 1, fb + int(length - enter))
    ref_b = float(np.median(b.rms_db[end_b:end_b + 4]))
    lo, hi = min(ref_a, ref_b) - 6, max(ref_a, ref_b) + 3
    echo_level, echo_from = 0.0, None

    x = 0.0
    while x < length:
        g_out, g_in = xfade_gains(xf(x))
        ia = start + int(next((s for s, e in rolls if s <= x < e), x))
        ia = min(ia, a.n_bars - 1)
        sa = _deck_state(recipe, "out", x, g_out, a.has_stems)
        pa = {k: float(getattr(a, k)[ia]) * sa["bands"][k] for k in BANDS}
        tonal_a, rhythm_a = _parts(a, ia, sa, pa)
        if ride_span and ride_span[0] <= x < ride_span[1] and tonal_a > STRONG:
            m["ride_tonal_bars"] += STEP
        power = 10 ** (a.rms_db[ia] / 10) * float(np.mean([sa["bands"][k] ** 2 for k in BANDS]))
        # Echo tail: what was sent keeps ringing, each 3/4-beat repeat 0.45x (about -7 dB).
        send = _lane(recipe, "out", "echo")(x)
        if send > 0.05 and sa["bands"]["mid"] > 0.2:
            echo_level, echo_from = 10 ** (a.rms_db[ia] / 10) * send ** 2, x
        elif echo_from is not None:
            power += echo_level * 0.45 ** (2 * (x - echo_from) * 4 / 0.75)
        in_on = x >= enter
        if in_on:
            ib = min(fb + int(x - enter), b.n_bars - 1)
            sb = _deck_state(recipe, "in", x, g_in, b.has_stems)
            pb = {k: float(getattr(b, k)[ib]) * sb["bands"][k] for k in BANDS}
            power += 10 ** (b.rms_db[ib] / 10) * float(np.mean([sb["bands"][k] ** 2 for k in BANDS]))
            both = max(pa.values()) > 0.15 and max(pb.values()) > 0.15
            if both:
                m["overlap_bars"] += STEP
            clash = False
            if a.low[ia] * sa["bass_gain"] > STRONG and b.low[ib] * sb["bass_gain"] > STRONG:
                m["bass_clash_bars"] += STEP
                where["bass_clash"].append(x)
                clash = True
            if m["vocals_known"] and a.vocals[ia] * sa["vocal_gain"] > STRONG and b.vocals[ib] * sb["vocal_gain"] > STRONG:
                m["vocal_clash_bars"] += STEP
                where["vocal_clash"].append(x)
                clash = True
            tonal_b, rhythm_b = _parts(b, ib, sb, pb)
            pitched = STRONG if a.presence is not None and b.presence is not None else 0.25
            if tonal_a > pitched and tonal_b > pitched:
                unit = tonal_clash(a.camelot, b.camelot, pitch_offset(rate_a(x), rate_b))
                if unit > 0:
                    m["key_clash_bars"] += STEP * unit
                    where["key_clash"].append(x)
                    clash = True
            # Without stems there's no telling a beat from a pad: any unlocked overlap counts.
            known = a.presence is not None and b.presence is not None
            if not locked and both and (not known or rhythm_a > STRONG and rhythm_b > STRONG):
                m["rhythm_clash_bars"] += STEP
                where["rhythm_clash"].append(x)
                clash = True
            if both and not clash:
                m["clean_overlap_bars"] += STEP
        total = 10 * math.log10(power + 1e-12)
        if total < lo:
            m["dip_bars"] += STEP
            where["dip"].append(x)
        elif total > hi:
            m["spike_bars"] += STEP
            where["spike"].append(x)
        x += STEP

    pen, reasons, valid = {}, [], True
    if locked and not sync:
        valid = False
        reasons.append("The plan beatmatches the tracks, but their tempos can't be locked (drifting or too far apart).")
    if m["rhythm_clash_bars"] >= 0.5:
        valid = False
        reasons.append(f"Two beats that aren't locked play together for {m['rhythm_clash_bars']:g} bars{_at(where['rhythm_clash'])}: a train wreck.")
    if m["bass_clash_bars"]:
        pen["bass_clash"] = W["bass_clash"] * m["bass_clash_bars"]
        reasons.append(f"Both basslines are up together for {m['bass_clash_bars']:g} bars{_at(where['bass_clash'])}.")
    if m["vocal_clash_bars"]:
        pen["vocal_clash"] = W["vocal_clash"] * m["vocal_clash_bars"]
        reasons.append(f"Two vocals overlap for {m['vocal_clash_bars']:g} bars{_at(where['vocal_clash'])}.")
    if m["key_clash_bars"]:
        pen["key_clash"] = W["key_clash"] * m["key_clash_bars"]
        semis = pitch_offset(rate_a(enter), rate_b)
        heard = f" (B sounds {semis:+.1f} semitones off its key at this tempo)" if abs(semis) > 0.2 else ""
        reasons.append(f"The keys ({a.camelot}, {b.camelot}{heard}) clash while both melodies are up{_at(where['key_clash'])}.")
    if m["ride_tonal_bars"]:
        semis = abs(pitch_offset(1.0, rate_a(ride_span[1])))
        pen["ride_pitch"] = W["ride_pitch"] * semis * m["ride_tonal_bars"]
        reasons.append(f"A's melody slides {semis:.1f} semitones during the tempo ride.")
    if m["dip_bars"] > 1:
        pen["dip"] = W["dip"] * (m["dip_bars"] - 1)
        reasons.append(f"The level drops away for {m['dip_bars']:g} bars{_at(where['dip'])}.")
    if m["spike_bars"] > 1:
        pen["spike"] = W["spike"] * (m["spike_bars"] - 1)
        reasons.append(f"The level jumps up for {m['spike_bars']:g} bars{_at(where['spike'])}.")
    if fb not in {p["start_bar"] for p in b.phrases}:
        pen["mid_phrase_entry"] = W["mid_phrase_entry"]
        reasons.append(f"B comes in at bar {fb}, mid-phrase.")
    ea = float(a.energy[max(0, start - 4):start + 1].mean())
    eb = float(b.energy[end_b:end_b + 4].mean())
    if ea - eb > 0.35:
        pen["energy_drop"] = W["energy_drop"]
        reasons.append("The energy falls a long way: B lands much calmer than A left.")
    if m["overlap_bars"] > 32:
        pen["long_overlap"] = W["long_overlap"] * (m["overlap_bars"] - 32)
        reasons.append(f"The two tracks overlap for {m['overlap_bars']:g} bars, which gets muddy.")
    if n_moves > 10:
        pen["complexity"] = W["complexity"] * (n_moves - 10)
        reasons.append(f"{n_moves} moves is a lot to follow.")
    if locked and abs(rate_b - 1) > 0.06:
        pen["stretch"] = W["stretch"]
        reasons.append(f"B is stretched {abs(rate_b - 1) * 100:.0f}%, which shifts its pitch.")
    bonus = {}
    section = a.sections[next(p["section_index"] for p in a.phrases if p["start_bar"] == start)]
    if section["label"] in ("outro", "break") or section["energy_level"] == "low":
        bonus["calm_exit"] = W["calm_exit"]
        reasons.append(f"A leaves from its {section['label']} ({section['energy_level']} energy).")
    if m["clean_overlap_bars"] >= 1:
        bonus["clean_blend"] = W["clean_blend"] * min(m["clean_overlap_bars"], CLEAN_BLEND_MAX)
        reasons.append(f"The two tracks blend cleanly for {m['clean_overlap_bars']:g} bars.")
    if not m["vocals_known"]:
        reasons.append("Vocal clashes not checked: no vocal stem for one of the tracks.")
    score = max(0.0, min(100.0, BASE + sum(bonus.values()) - sum(pen.values())))
    order = sorted(pen, key=pen.get, reverse=True)
    return {"valid": valid, "score": round(score if valid else 0.0, 1),
            "breakdown": {**{k: -round(v, 2) for k, v in pen.items()}, **{k: round(v, 2) for k, v in bonus.items()}},
            "measures": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()},
            "reasons": reasons, "worst": order[0] if order else None}

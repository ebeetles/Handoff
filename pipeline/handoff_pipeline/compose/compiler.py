"""Moves -> an anchored C5 recipe (contracts/recipe.schema.json v3), or the reasons it can't be.

Each move becomes segments on control lanes ("from wherever the lane is, go to v between bars
s and e"; s == e is a step) and/or events. Lanes are assembled in bar order. Two moves driving
the same control at overlapping times is an error, not a silent merge: the composer must say
what it means.
"""
from __future__ import annotations

from collections import defaultdict

from .facts import TrackData, lock
from .mix import DEFAULTS, choose_sync, key_value, lane_value_at, ride_sync, tempo_value, volume_gain, xfade_gains
from .moves import check_plan, normalize
from .recipes import recipe_errors

EQ_LEVEL = {"kill": 0.0, "dip": 0.3, "flat": 0.5, "boost": 0.62}
FILTER_TO = {("lowpass", "close"): 0.15, ("highpass", "close"): 0.82, ("lowpass", "open"): 0.5, ("highpass", "open"): 0.5}
ECHO_SEND = 0.7
IN_VOLUME = 0.8


def _q(x: float) -> float:
    return round(x * 4) / 4


def compile_plan(c: dict, a: TrackData, b: TrackData, rid: str) -> tuple[dict | None, list[str]]:
    errs = check_plan(c)
    if errs:
        return None, errs
    c = normalize(c)   # `loop` -> loop_roll / loop_hold
    moves = c["moves"]
    enter = next(m for m in moves if m["move"] == "in_enter")
    length = next(m for m in moves if m["move"] == "out_stop")["at_bar"]
    start, fb = c["out_start_bar"], enter["from_bar"]

    if start not in {p["start_bar"] for p in a.phrases}:
        errs.append(f"out_start_bar {start} is not one of A's phrase starts")
    if start + length > a.n_bars:
        errs.append(f"A ends at bar {a.n_bars}, before the transition does (bar {start + length:g})")
    if not 0 <= fb < b.n_bars:
        errs.append(f"in_enter from_bar {fb} is outside B (0..{b.n_bars - 1})")
    elif fb % 4:
        errs.append(f"in_enter from_bar {fb} is not on B's 4-bar grid")
    if enter["at_bar"] > length:
        errs.append("B must come in by the bar A stops")   # on that bar = a hard cut (after a brake, say)
    elif fb + (length - enter["at_bar"]) > b.n_bars:
        errs.append("B would run out before the transition ends")
    if length < 1:
        errs.append("the transition is shorter than a bar")

    segs: dict[tuple, list[tuple]] = defaultdict(list)   # (deck, control) -> [(s, e, value, move)]
    # A newly loaded/previously used deck may still have filters or stems left altered.
    # Every compiled plan explicitly establishes the incoming state it was scored against.
    init: dict[tuple, float] = {("in", c): DEFAULTS[c] for c in ("volume", "eqLow", "eqMid", "eqHigh", "filter", "echo")}
    if b.has_stems:
        init.update({("in", f"stem.{s}"): 1.0 for s in ("drums", "bass", "vocals", "other")})
    events: list[dict] = [{"at_bar": enter["at_bar"], "deck": "in", "command": "play"},
                          {"at_bar": length, "deck": "out", "command": "pause"}]
    stems = {"in"} if b.has_stems else set()
    if c.get("in_key_shift"):   # set while B is silent, before it comes in
        init[("in", "key")] = key_value(int(c["in_key_shift"]))
    both_stems = a.has_stems and b.has_stems
    has_stems = {"out": a.has_stems, "in": b.has_stems}

    ride = None
    for m in moves:
        k = m["move"]
        if k == "tempo_ride":
            ride = ride_sync(a.bpm, b.bpm) if a.beatmatchable and b.beatmatchable else None
            if ride is None:
                errs.append(f"tempo_ride can't bring {a.bpm:g} and {b.bpm:g} BPM together (or a tempo drifts)")
                continue
            pct = abs(ride[1] - 1) * 100
            if pct > m["end_bar"] - m["start_bar"] + 1e-9:
                errs.append(f"tempo_ride moves A {pct:.1f}% in {m['end_bar'] - m['start_bar']:g} bars; allow a bar per 1%")
            if enter["at_bar"] < m["end_bar"]:
                errs.append("tempo_ride: B must come in at or after the ride ends")
            # A ramp only: the engine applies tempo changes as they are written, not as exact steps.
            segs[("out", "tempo")].append((m["start_bar"], m["end_bar"], round(tempo_value(ride[1]), 4), k))
        elif k == "brake":
            stop = m["at_bar"] + m["beats"] / 4
            events.append({"at_bar": m["at_bar"], "deck": "out", "command": "brake", "beats": m["beats"]})
            segs[("out", "volume")].append((stop, stop, 0.0, k))   # a stopped deck holds a sample: silence it
        elif k == "crossfade":
            e = m["start_bar"] if m["shape"] == "cut" else m["end_bar"]
            segs[(None, "xfader")].append((m["start_bar"], e, m["to"], k))
        elif k == "bass_swap":
            x = m["at_bar"]
            ctl = "stem.bass" if both_stems else "eqLow"
            init[("in", ctl)] = 0.0
            segs[("in", ctl)].append((x, x, 1.0 if both_stems else 0.5, k))
            segs[("out", ctl)].append((x, x, 0.0, k))
            if both_stems:
                stems |= {"out", "in"}
        elif k == "eq":
            ctl = {"low": "eqLow", "mid": "eqMid", "high": "eqHigh"}[m["band"]]
            segs[(m["deck"], ctl)].append((m["start_bar"], m["end_bar"], EQ_LEVEL[m["to"]], k))
        elif k == "filter_sweep":
            segs[(m["deck"], "filter")].append((m["start_bar"], m["end_bar"], FILTER_TO[(m["kind"], m["direction"])], k))
        elif k == "echo_throw":
            x = m["at_bar"]
            segs[("out", "echo")].append((max(0.0, x - 1), max(0.0, x - 0.25), ECHO_SEND, k))
            segs[("out", "echo")].append((min(length, x + 1), min(length, x + 1), 0.0, k))
            if m["cut"]:
                segs[("out", "volume")].append((x, x, 0.0, k))
        elif k == "stem_drop":
            if not has_stems[m["deck"]]:
                errs.append(f"stem_drop on the {m['deck']} deck, which has no stems")
                continue
            stems.add(m["deck"])
            for s in m["stems"]:
                segs[(m["deck"], f"stem.{s}")].append((m["at_bar"], m["at_bar"], 0.0 if m["action"] == "mute" else 1.0, k))
        elif k == "loop_roll":
            s, n = m["start_bar"], m["end_bar"] - m["start_bar"]
            for at, beats in ((s, 0.5), (s + n / 2, 0.25), (s + 3 * n / 4, 0.125)):
                events.append({"at_bar": _q(at), "deck": "out", "command": "loop", "beats": beats})
            events.append({"at_bar": m["end_bar"], "deck": "out", "command": "loop_off"})
        elif k == "loop_hold":
            events.append({"at_bar": m["start_bar"], "deck": "out", "command": "loop", "beats": 4 * m["bars"]})
            events.append({"at_bar": m["end_bar"], "deck": "out", "command": "loop_off"})
        elif k == "volume":
            segs[(m["deck"], "volume")].append((m["start_bar"], m["end_bar"], m["to"], k))
        elif k == "rhythmic_gate":
            period = m["period_beats"] / 4
            s, end = m["start_bar"], m["end_bar"]
            ctl = (m["deck"], "volume")
            # Check the occupied interval, including gaps between the generated steps.
            for other in moves:
                if other is m:
                    continue
                affects = (other["move"] in ("volume", "rhythmic_gate") and other["deck"] == m["deck"]
                           or other["move"] in ("echo_throw", "brake") and other.get("cut", True) and m["deck"] == "out")
                if affects:
                    os = other.get("start_bar", other.get("at_bar"))
                    oe = other.get("end_bar", os + other.get("beats", 0) / 4)
                    if os <= end and oe >= s:
                        errs.append("rhythmic_gate overlaps another move on volume")
            segs[ctl].append((s, s, IN_VOLUME, k))
            while s < end:
                off = s + period / 2
                segs[ctl].append((off, off, IN_VOLUME * (1 - m["depth"]), k))
                s += period
                segs[ctl].append((s, s, IN_VOLUME, k))

    lanes = []
    for key in sorted(set(segs) | set(init), key=lambda k: (k[0] or "", k[1])):
        deck, ctl = key
        ss = sorted(segs.get(key, []), key=lambda t: (t[0], t[1]))
        for p, n in zip(ss, ss[1:]):
            if p[1] > n[0] or (p[0] == p[1] == n[0] == n[1]):
                errs.append(f"{p[3]} and {n[3]} both change {deck or ''}{'.' if deck else ''}{ctl} at once "
                            f"(bars {p[0]:g}-{p[1]:g} and {n[0]:g}-{n[1]:g})")
        # Steps on B before (or as) it comes in are its starting state, set while it's silent.
        while deck == "in" and ss and ss[0][0] == ss[0][1] <= enter["at_bar"]:
            init[key] = ss.pop(0)[2]
        v = init.get(key, DEFAULTS[ctl])
        pts = [[0.0, v]]
        for s, e, to, mv in ss:
            if e > length:
                errs.append(f"{mv} on {ctl} runs past out_stop")
            pts += [[s, v], [e, to]]
            v = to
        clean: list[list[float]] = []
        for p in pts:
            if not clean or p != clean[-1]:
                clean.append([float(p[0]), round(float(p[1]), 4)])
        lanes.append({"deck": deck, "control": ctl, "points": clean})

    for l in lanes:   # B's key shift is meant to last (key lock holds it after the transition)
        if l["deck"] == "in" and l["control"] != "key" and abs(l["points"][-1][1] - DEFAULTS[l["control"]]) > 1e-4:
            errs.append(f"incoming {l['control']} must return to neutral before out_stop")

    xf = next((l for l in lanes if l["control"] == "xfader"), None)
    if xf is None:
        errs.append("the crossfader never moves to B")
    elif xf["points"][-1][1] != 1:
        errs.append(f"the crossfader ends at {xf['points'][-1][1]:g}, not fully on B")
    if errs:
        return None, errs

    # Both decks audible at once (anywhere) means the overlap must be beatmatched, when the pair can
    # be locked at all (directly, or after the plan's tempo ride). When it can't, the overlap plays
    # unlocked, and the critic judges whether it's rhythm-free enough to work.
    pts = lambda d, c: next((l["points"] for l in lanes if l["deck"] == d and l["control"] == c), [[0, DEFAULTS[c]]])  # noqa: E731
    overlap = False
    x = enter["at_bar"]
    while x < length:
        g_out, g_in = xfade_gains(lane_value_at(xf["points"], x))
        if g_out * volume_gain(lane_value_at(pts("out", "volume"), x)) / volume_gain(0.8) > 0.2 and g_in > 0.2:
            overlap = True
            break
        x += 0.25

    recipe = {
        "schema_version": 4, "id": rid, "name": c["idea"], "description": c["rationale"], "bars": length,
        "requires": {"stems": "both" if stems == {"out", "in"} else (stems.pop() if stems else "none"),
                     "beatmatchable": overlap and lockable(a, b, ride is not None), "max_camelot_distance": None},
        "anchor": {"out_track": a.id, "in_track": b.id, "out_start_bar": start, "in_from_bar": fb},
        "events": sorted(events, key=lambda e: (e["at_bar"], e["deck"] != "out")),
        "lanes": lanes,
    }
    errs = recipe_errors(recipe)
    return (None, [f"compiler produced an invalid recipe: {e}" for e in errs]) if errs else (recipe, [])


def lockable(a: TrackData, b: TrackData, with_ride: bool) -> bool:
    """Can B be beatmatched to A by the time it plays: directly, or after this plan's ride."""
    if not (a.beatmatchable and b.beatmatchable):
        return False
    return choose_sync(b.bpm, a.bpm) is not None or (with_ride and lock(a, b) is not None)

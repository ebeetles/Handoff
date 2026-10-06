"""The moves vocabulary: what a composed transition is made of.

A plan is an anchor (where A exits, how long it runs) plus a list of moves, with bars counted
from the transition start (bar 0 = A's exit phrase). The same JSON schema is the composer's
structured-output format (so Claude's output always parses) and the compiler's input contract.
Structured outputs don't enforce numeric bounds, so check_plan() does.
"""
from __future__ import annotations

import math
import jsonschema

MOVES_VERSION = 5

DECK = {"type": "string", "enum": ["out", "in"]}
BAR = {"type": "number", "description": "Bars from the transition start (0 = A's exit phrase). Quarter bars allowed."}


def _move(name: str, description: str, props: dict) -> dict:
    return {
        "type": "object", "description": description, "additionalProperties": False,
        "required": ["move", *props], "properties": {"move": {"type": "string", "const": name}, **props},
    }


MOVES = [
    _move("in_enter", "B starts playing at at_bar, from its own bar from_bar (pick a phrase start: its intro, "
          "a breakdown, a vocal...). Exactly one.", {"at_bar": BAR, "from_bar": {"type": "integer"}}),
    _move("crossfade", "Move the crossfader towards B, to `to` (0 = all A, 1 = all B), ramped from start_bar to "
          "end_bar or cut at start_bar. Chain several to stage it. It must reach 1 before out_stop.",
          {"start_bar": BAR, "end_bar": BAR, "to": {"type": "number"}, "shape": {"type": "string", "enum": ["ramp", "cut"]}}),
    _move("bass_swap", "At at_bar, A's bass goes out and B's comes in, in one step (B's bass is held back "
          "until then). Uses stems when both tracks have them, otherwise the low EQ.", {"at_bar": BAR}),
    _move("eq", "Set one EQ band of a deck to a level between start_bar and end_bar (equal bars = a step): "
          "kill (-40 dB), dip (-12 dB), flat, boost (+3 dB). Carve space, e.g. dip A's mids under B's vocal.",
          {"deck": DECK, "band": {"type": "string", "enum": ["low", "mid", "high"]}, "start_bar": BAR, "end_bar": BAR,
           "to": {"type": "string", "enum": ["kill", "dip", "flat", "boost"]}}),
    _move("filter_sweep", "Sweep a deck's filter from start_bar to end_bar. close = into the effect (lowpass: "
          "muffled; highpass: thin), open = back to the full sound.",
          {"deck": DECK, "kind": {"type": "string", "enum": ["lowpass", "highpass"]},
           "direction": {"type": "string", "enum": ["close", "open"]}, "start_bar": BAR, "end_bar": BAR}),
    _move("echo_throw", "Build an echo on A in the bar before at_bar; with cut, A drops out at at_bar and the "
          "echo tail rings over whatever comes next.", {"at_bar": BAR, "cut": {"type": "boolean"}}),
    _move("stem_drop", "Mute or unmute stems of a deck at at_bar (only on tracks with stems): e.g. drop A to "
          "drums only, or bring in B's vocals later.",
          {"deck": DECK, "stems": {"type": "array", "items": {"type": "string", "enum": ["drums", "bass", "vocals", "other"]}},
           "action": {"type": "string", "enum": ["mute", "unmute"]}, "at_bar": BAR}),
    _move("loop", "A loops from start_bar until end_bar, then carries on in time as if it had played through. "
          "style roll: stutters faster and faster (half, quarter, eighth beat), a tension build before a drop (at least "
          "a bar). style hold_1/hold_2/hold_4: repeats that many bars of itself from start_bar (at least one full loop, "
          "at most 16 bars, from a bar line): loop a hook (a vocal line, a riff, a drum break) and let B build under or "
          "around it, a live mash-up. With stem_drop on A, loop just its vocal or its drums.",
          {"start_bar": BAR, "end_bar": BAR, "style": {"type": "string", "enum": ["roll", "hold_1", "hold_2", "hold_4"]}}),
]
# Internal forms of `loop` (the compiler, critic and rules composer use these; older saved plans too).
LOOP_FORMS = [
    _move("loop_roll", "A stutters faster and faster (half, quarter, eighth beat) from start_bar until end_bar, "
          "then carries on in time. A tension build before a drop.", {"start_bar": BAR, "end_bar": BAR}),
    _move("loop_hold", "A repeats `bars` bars of itself, starting at start_bar, until end_bar, then carries on "
          "in time as if it had played through. Loop a hook (a vocal line, a riff, a drum break) and let B build "
          "under or around it: a live mash-up. With stem_drop on A, loop just its vocal or its drums.",
          {"start_bar": BAR, "end_bar": BAR, "bars": {"type": "integer", "enum": [1, 2, 4]}}),
]

MOVES += [
    _move("volume", "Ramp or step a deck's volume: 0 is silence, 0.8 is normal playback. Use for a "
          "tease, intentional short gap before a drop, or a staged reveal. Restore B to 0.8 by out_stop.",
          {"deck": DECK, "start_bar": BAR, "end_bar": BAR, "to": {"type": "number", "description": "0..0.8"}}),
    _move("rhythmic_gate", "Rhythmic volume chops: normal volume on the first half of each period, "
          "ducked on the second half. Restores normal volume at end_bar. Reserve this deck's volume "
          "lane throughout; no volume or cutting echo_throw may overlap. Use briefly for a call-and-response or build.",
          {"deck": DECK, "start_bar": BAR, "end_bar": BAR,
           "period_beats": {"type": "integer", "enum": [1, 2, 4]},
           "depth": {"type": "number", "description": "0..1: 1 cuts to silence, 0 does nothing."}}),
    _move("tempo_ride", "Glide A's tempo towards B's between start_bar and end_bar (at most 1% per bar; "
          "the board computes the target from the pair's tempo_ride fact). B then plays at its own tempo, or syncs "
          "to A for the rest. It makes pairs up to ~16% apart beatmatchable. Key lock holds A's pitch while it "
          "rides. B must enter at or after end_bar.",
          {"start_bar": BAR, "end_bar": BAR}),
    _move("brake", "Turntable stop: A slows to a halt over `beats` beats from at_bar, pitch diving, then is "
          "silent. A hard genre or tempo switch; land B's drop right after it.",
          {"at_bar": BAR, "beats": {"type": "integer", "enum": [1, 2, 4]}}),
    _move("out_stop", "A stops at at_bar: the end of the transition. Exactly one, and the last thing that happens. "
          "B enters at or before it (entering on it is a hard cut, e.g. right after a brake).",
          {"at_bar": BAR}),
]

def _candidate(moves: list[dict], shift_required: bool) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["idea", "rationale", "out_start_bar", "moves"] + (["in_key_shift"] if shift_required else []),
        "properties": {
            "in_key_shift": {"type": "integer", "enum": [-2, -1, 0, 1, 2],
                             "description": "Semitones to transpose B before it comes in (key lock keeps it there for the rest of B). 0 = B's own key."},
            "idea": {"type": "string", "description": "A short name for the transition, e.g. 'Drum bridge into the vocal'."},
            "rationale": {"type": "string", "description": "One or two sentences: why this works for these two tracks, citing their facts (sections, energy, key, vocals)."},
            "out_start_bar": {"type": "integer", "description": "A's phrase start where the transition begins (one of exit_bars)."},
            "moves": {"type": "array", "items": {"anyOf": moves}},
        },
    }


# What Claude writes (structured outputs). The API caps the compiled grammar's size: a 14th move
# type was rejected ("compiled grammar is too large"), so rolls and held loops share one `loop`.
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["candidates"],
    "properties": {"candidates": {"type": "array", "items": _candidate(MOVES, True)}},
}
# What the compiler accepts: the same, plus the internal loop forms, and plans from before
# in_key_shift (moves v4 and older), which mean 0.
CANDIDATE = _candidate(MOVES + LOOP_FORMS, False)


def normalize(c: dict) -> dict:
    """A copy of a candidate with `loop` moves in their internal forms (loop_roll, loop_hold)."""
    def one(m: dict) -> dict:
        if m.get("move") != "loop" or not isinstance(m.get("style"), str):
            return m
        rest = {k: v for k, v in m.items() if k not in ("move", "style")}
        if m["style"] == "roll":
            return {"move": "loop_roll", **rest}
        return {"move": "loop_hold", **rest, "bars": int(m["style"].removeprefix("hold_") or 0)}
    return {**c, "moves": [one(m) for m in c.get("moves", [])]} if isinstance(c.get("moves"), list) else c

_BARS = ("at_bar", "start_bar", "end_bar")


def check_plan(c: dict) -> list[str]:
    """Structural checks the JSON schema can't express. Track-dependent checks are the compiler's."""
    c = normalize(c)
    errs = [f"schema: {e.message}" for e in jsonschema.Draft202012Validator(CANDIDATE).iter_errors(c)]
    if errs:
        return errs
    moves = c.get("moves", [])
    if len(moves) > 64:
        return ["a plan may have at most 64 moves"]
    if any(isinstance(v, (int, float)) and (isinstance(v, bool) or not math.isfinite(v))
           for m in moves for k, v in m.items() if k != "cut"):
        return ["move numbers must be finite"]
    for kind in ("in_enter", "out_stop"):
        n = sum(m["move"] == kind for m in moves)
        if n != 1:
            errs.append(f"needs exactly one {kind} (has {n})")
    for kind in ("tempo_ride", "brake"):
        if sum(m["move"] == kind for m in moves) > 1:
            errs.append(f"at most one {kind}")
    for m in moves:
        for k in _BARS:
            if k in m and (m[k] < 0 or abs(m[k] * 4 - round(m[k] * 4)) > 1e-9):
                errs.append(f"{m['move']}: {k} {m[k]} must be a non-negative quarter bar")
        if "start_bar" in m and "end_bar" in m and m["end_bar"] < m["start_bar"]:
            errs.append(f"{m['move']}: ends before it starts")
        if m["move"] == "crossfade" and not 0 <= m["to"] <= 1:
            errs.append(f"crossfade: to {m['to']} must be 0..1")
        if m["move"] == "volume" and not 0 <= m["to"] <= 0.8:
            errs.append("volume: to must be 0..0.8")
        if m["move"] == "rhythmic_gate":
            n = m["end_bar"] - m["start_bar"]
            if not 0 <= m["depth"] <= 1 or not 0 < n <= 8 or n * 4 % m["period_beats"]:
                errs.append("rhythmic_gate: depth 0..1, length 0..8 bars, and a whole number of periods required")
        if m["move"] == "stem_drop" and not m["stems"]:
            errs.append("stem_drop: no stems named")
        if m["move"] == "loop_roll" and m["end_bar"] - m["start_bar"] < 1:
            errs.append("loop_roll: needs at least a bar")
        if m["move"] == "tempo_ride" and m["end_bar"] - m["start_bar"] < 2:
            errs.append("tempo_ride: needs at least 2 bars")
        if m["move"] == "loop_hold" and not (m["bars"] <= m["end_bar"] - m["start_bar"] <= 16) or m["move"] == "loop_hold" and m["start_bar"] % 1:
            errs.append("loop_hold: starts on a bar line and runs at least one loop, at most 16 bars")
    stops = [m["at_bar"] for m in moves if m["move"] == "out_stop"]
    if stops and any(m.get(k, 0) > stops[0] for m in moves for k in _BARS if m["move"] != "out_stop"):
        errs.append("a move happens after out_stop")
    rolls = sorted((m["start_bar"], m["end_bar"]) for m in moves if m["move"] == "loop_roll")
    if any(a[1] >= b[0] for a, b in zip(rolls, rolls[1:])):
        errs.append("loop_roll intervals overlap or touch")
    # Each of these takes over A's playback rate or position; they can't share time.
    spans = sorted([(s, e, "loop_roll") for s, e in rolls]
                   + [(m["start_bar"], m["end_bar"], "loop_hold") for m in moves if m["move"] == "loop_hold"]
                   + [(m["start_bar"], m["end_bar"], "tempo_ride") for m in moves if m["move"] == "tempo_ride"]
                   + [(m["at_bar"], m["at_bar"] + m["beats"] / 4, "brake") for m in moves if m["move"] == "brake"])
    for p, n in zip(spans, spans[1:]):
        if (p[2] != n[2] or p[2] == "loop_hold") and p[1] > n[0]:
            errs.append(f"{p[2]} and {n[2]} overlap (both drive A's playback)")
    for m in moves:
        if m["move"] == "brake" and stops and m["at_bar"] + m["beats"] / 4 > stops[0]:
            errs.append("brake: A must have stopped by out_stop")
    return errs

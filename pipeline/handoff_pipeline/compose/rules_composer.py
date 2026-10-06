"""Rules composer: the presets re-expressed in the moves vocabulary, anchored to a pair by
simple rules. It is the baseline the AI composer is measured against (Chunk 11), and it lets the
whole pipeline run without an API key.

Anchor rules: A exits from its calmest phrase among the last three exits (ties: the later
one); B enters from its first phrase. Blends need a syncable tempo; cuts don't.
"""
from __future__ import annotations

from .facts import TrackData, exit_bars
from .mix import choose_sync


def _exit(a: TrackData, need: int) -> int | None:
    exits = [s for s in exit_bars(a) if a.n_bars - s >= need][-3:]
    if not exits:
        return None
    energy = {p["start_bar"]: float(a.energy[p["start_bar"]:p["start_bar"] + p["n_bars"]].mean()) for p in a.phrases}
    return min(reversed(exits), key=lambda s: energy[s])


def compose_rules(a: TrackData, b: TrackData) -> list[dict]:
    syncable = a.beatmatchable and b.beatmatchable and choose_sync(b.bpm, a.bpm) is not None
    fb = b.phrases[0]["start_bar"]
    out = []

    def add(idea: str, rationale: str, need: int, moves: list[dict]) -> None:
        start = _exit(a, need)
        if start is not None:
            out.append({"idea": idea, "rationale": rationale, "out_start_bar": start, "moves": moves})

    if syncable:
        add("Bass swap blend", "B comes in under A without its bass, then the basslines swap on bar 8.", 16, [
            {"move": "in_enter", "at_bar": 0, "from_bar": fb},
            {"move": "crossfade", "start_bar": 0, "end_bar": 4, "to": 0.5, "shape": "ramp"},
            {"move": "bass_swap", "at_bar": 8},
            {"move": "crossfade", "start_bar": 12, "end_bar": 16, "to": 1, "shape": "ramp"},
            {"move": "out_stop", "at_bar": 16}])
        add("Filter handoff", "B opens up from a low-pass while A thins out through a rising high-pass.", 16, [
            {"move": "in_enter", "at_bar": 0, "from_bar": fb},
            {"move": "filter_sweep", "deck": "in", "kind": "lowpass", "direction": "close", "start_bar": 0, "end_bar": 0},
            {"move": "filter_sweep", "deck": "in", "kind": "lowpass", "direction": "open", "start_bar": 0.25, "end_bar": 8},
            {"move": "crossfade", "start_bar": 0, "end_bar": 6, "to": 0.5, "shape": "ramp"},
            {"move": "filter_sweep", "deck": "out", "kind": "highpass", "direction": "close", "start_bar": 8, "end_bar": 16},
            {"move": "crossfade", "start_bar": 10, "end_bar": 16, "to": 1, "shape": "ramp"},
            {"move": "out_stop", "at_bar": 16}])
        add("Loop roll drop", "A rolls faster and faster under a rising high-pass, then B drops on the phrase.", 17, [
            {"move": "filter_sweep", "deck": "out", "kind": "highpass", "direction": "close", "start_bar": 12, "end_bar": 16},
            {"move": "loop_roll", "start_bar": 12, "end_bar": 16},
            {"move": "in_enter", "at_bar": 16, "from_bar": fb},
            {"move": "crossfade", "start_bar": 16, "end_bar": 16, "to": 1, "shape": "cut"},
            {"move": "out_stop", "at_bar": 17}])
        if a.has_stems and b.has_stems:
            add("Drum bridge", "B's drums join A, A drops to drums on bar 8 as B's bass and melody come in, then B's vocals.", 16, [
                {"move": "in_enter", "at_bar": 0, "from_bar": fb},
                {"move": "stem_drop", "deck": "in", "stems": ["bass", "vocals", "other"], "action": "mute", "at_bar": 0},
                {"move": "crossfade", "start_bar": 0, "end_bar": 4, "to": 0.5, "shape": "ramp"},
                {"move": "stem_drop", "deck": "out", "stems": ["vocals"], "action": "mute", "at_bar": 4},
                {"move": "stem_drop", "deck": "out", "stems": ["bass", "other"], "action": "mute", "at_bar": 8},
                {"move": "stem_drop", "deck": "in", "stems": ["bass", "other"], "action": "unmute", "at_bar": 8},
                {"move": "stem_drop", "deck": "in", "stems": ["vocals"], "action": "unmute", "at_bar": 12},
                {"move": "crossfade", "start_bar": 12, "end_bar": 16, "to": 1, "shape": "ramp"},
                {"move": "out_stop", "at_bar": 16}])
    add("Echo out", "A builds an echo and cuts on bar 8; B drops in under the tail. Works across any tempo or key.", 12, [
        {"move": "echo_throw", "at_bar": 8, "cut": True},
        {"move": "in_enter", "at_bar": 8, "from_bar": fb},
        {"move": "crossfade", "start_bar": 8, "end_bar": 8, "to": 1, "shape": "cut"},
        {"move": "out_stop", "at_bar": 12}])
    return out

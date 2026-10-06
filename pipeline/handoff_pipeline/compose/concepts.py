"""Creative direction for the composer: concept cards, memory of recent work, and novelty.

Left alone, the composer converged on a few formulas (2026-10-06, 71 saved ideas: "roll" or
"echo" in 35 titles; strip stems + echo cut, or highpass + roll + brake, again and again). The
critic rewards safe plans, and best-of-n plus refinement both select for its taste. So:

  - Each draft gets a different concept card, drawn at random from the ones these tracks can
    support, skipping concepts used recently.
  - The composer sees its recent work and is told not to repeat it.
  - When picking which draft to refine, similarity to recent work costs points (novelty).
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .facts import TrackData, lock
from .moves import normalize

# needs: "stems" (both decks), "lock" (beatmatchable pair), "vocals_out"/"vocals_in" (that deck
# sings somewhere), "beatless_in" (a beatless 4-bar window in B), "breakdown_out" (A has a
# breakdown or a beatless window), "build_out" (A has a build), "drop_in" (B has a drop),
# "hook_out"/"hook_in" (a detected hook in A / a vocal hook in B), "unlocked" (only for pairs
# that can't lock). Structure needs come from TrackAnalysis v4 cues and hooks.
CONCEPTS = [
    {"id": "build_handoff", "name": "Build handoff", "needs": ["lock", "build_out", "drop_in"],
     "idea": "A's build (a cue) rises as if to its own drop, and B's drop lands instead, exactly where A's would have: a bigger payoff than the crowd expects."},
    {"id": "hook_mashup", "name": "Hook mash-up", "needs": ["lock", "stems", "hook_in"],
     "idea": "B's vocal hook (its hooks list) arrives over A's peak instrumental, A's own vocal muted, before the rest of B takes over."},
    {"id": "hook_loop", "name": "Hook loop mash-up", "needs": ["lock", "hook_out"],
     "idea": "Loop a short, catchy piece of A (its vocal hook, a riff, a drum break; a loop with a hold style, with stem_drop to isolate it) and build B up underneath until B takes over."},
    {"id": "acapella_over", "name": "A cappella over the new groove", "needs": ["stems", "vocals_out"],
     "idea": "Strip A to its vocal and let it ride on B's beat or groove for a phrase before it lets go."},
    {"id": "vocal_preview", "name": "Vocal preview", "needs": ["stems", "vocals_in"],
     "idea": "Let B's vocal appear early over A's instrumental, as a preview of what's coming, then bring the rest of B in."},
    {"id": "false_drop", "name": "False drop", "needs": ["drop_in"],
     "idea": "Set the crowd up for B's drop, pull it away at the last second (a cut, a gap, a filter slam), then deliver it for real a bar or phrase later."},
    {"id": "call_response", "name": "Call and response", "needs": ["lock"],
     "idea": "A and B answer each other in alternating bars or half-bars (gates, crossfader cuts, stem swaps) before B wins the argument."},
    {"id": "breakdown_swap", "name": "Breakdown swap", "needs": ["breakdown_out"],
     "idea": "Use A's beatless breakdown as the doorway: B's beat arrives under it, and A's breakdown never resolves back into A's drop."},
    {"id": "intro_doorway", "name": "Intro doorway", "needs": ["beatless_in"],
     "idea": "B's beatless intro or pad floats in over A's last bars, so B starts as atmosphere before its first kick lands."},
    {"id": "energy_slam", "name": "Energy slam", "needs": [],
     "idea": "No blend at all: build A to its peak and hit B's biggest phrase on the one. All about the build and the impact."},
    {"id": "turntable_stop", "name": "Turntable stop", "needs": [],
     "idea": "A stops like a turntable losing power (brake) as the punchline, and B answers instantly."},
    {"id": "tempo_morph", "name": "Tempo morph", "needs": ["lock"],
     "idea": "A's tempo slides to meet B's (tempo_ride) while A is stripped back or filtered, so the switch of feel is gradual and seamless."},
    {"id": "filter_duel", "name": "Filter duel", "needs": ["lock"],
     "idea": "A closes through one filter while B opens from the other side, crossing in the middle of the phrase."},
    {"id": "layer_by_layer", "name": "Layer by layer", "needs": ["stems", "lock"],
     "idea": "Rebuild B one stem at a time while A loses one at a time, in an order that tells a story (drums, then bass, then music, then the vocal)."},
    {"id": "long_eq_blend", "name": "Long EQ blend", "needs": ["lock"],
     "idea": "A long, patient, classic blend: EQ carves so both tracks share the space, a bass swap on a phrase line, nothing flashy."},
    {"id": "echo_wash", "name": "Echo wash", "needs": [],
     "idea": "A dissolves in echo (one throw or several, as a rhythm) while B emerges through the tail."},
    {"id": "stutter_edit", "name": "Stutter edit", "needs": [],
     "idea": "Chop A into rhythmic stutters (rolls, gates) that accelerate into B, like a live edit."},
    {"id": "beat_under_vocal", "name": "Beat under a floating vocal", "needs": ["stems", "vocals_out", "unlocked"],
     "idea": "The tempos can't lock, so A's vocal floats free (no drums, no bass) over B's beat, then echoes away."},
]
RECENT = 12          # compositions the composer is shown, and that steer the concept draw
NOVELTY_WEIGHT = 8.0  # points a draft loses for using exactly the toolkit of a recent composition


def feasible(a: TrackData, b: TrackData) -> list[dict]:
    """Concepts these two tracks can support."""
    def beatless(t: TrackData) -> bool:
        return t.drums is not None and any(t.drums[s:s + 4].mean() < 0.15 for s in range(0, t.n_bars - 4, 4))
    def sings(t: TrackData) -> bool:
        return t.vocals is not None and (t.vocals > 0.5).sum() >= 8
    has = {"stems": a.has_stems and b.has_stems, "lock": lock(a, b) is not None, "unlocked": lock(a, b) is None,
           "vocals_out": sings(a), "vocals_in": sings(b), "beatless_in": beatless(b),
           "breakdown_out": beatless(a) or any(c["kind"] == "breakdown" for c in a.cues),
           "build_out": any(c["kind"] == "build" for c in a.cues), "drop_in": any(c["kind"] == "drop" for c in b.cues),
           "hook_out": bool(a.hooks), "hook_in": any(h["kind"] == "vocal" for h in b.hooks)}
    return [c for c in CONCEPTS if all(has[n] for n in c["needs"])]


def draw(a: TrackData, b: TrackData, n: int, recent: list[dict], rng: random.Random | None = None) -> list[dict]:
    """n different concepts for these tracks, preferring ones not used recently."""
    rng = rng or random.Random()
    pool = feasible(a, b)
    used = {r.get("concept") for r in recent}
    fresh = [c for c in pool if c["id"] not in used]
    picks = rng.sample(fresh, min(n, len(fresh)))
    rest = [c for c in pool if c not in picks]
    picks += rng.sample(rest, min(n - len(picks), len(rest)))
    return [{"id": c["id"], "name": c["name"], "idea": c["idea"]} for c in picks]


def toolkit(moves: list[dict]) -> set[str]:
    """The moves that shape a transition (every plan enters, stops and crossfades)."""
    return {m["move"] for m in normalize({"moves": moves})["moves"]} - {"in_enter", "out_stop", "crossfade"}


def similarity(moves: list[dict], recent: list[dict]) -> float:
    """0..1: how close this plan's toolkit is to the closest recent composition's (Jaccard)."""
    mine = toolkit(moves)
    best = 0.0
    for r in recent:
        theirs = set(r.get("toolkit", []))
        if mine or theirs:
            best = max(best, len(mine & theirs) / len(mine | theirs))
    return best


class History:
    """Recent compositions (pipeline/cache/composer_history.jsonl, not in git): what the
    composer is told not to repeat. One line per kept transition."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def recent(self, n: int = RECENT) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines()[-n:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def add(self, pair: tuple[str, str], candidate: dict, concept: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": int(time.time()), "pair": list(pair), "idea": candidate["idea"], "concept": concept,
               "toolkit": sorted(toolkit(candidate["plan"]["moves"]))}
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

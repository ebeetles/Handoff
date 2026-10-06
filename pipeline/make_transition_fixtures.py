"""Regenerate C6 fixtures from the synthetic demo library; no LLM/network calls."""
from __future__ import annotations

import json
from pathlib import Path

from handoff_pipeline.compose.compiler import compile_plan
from handoff_pipeline.compose.critic import critique
from handoff_pipeline.compose.facts import load_track, pair_facts
from plan_transitions import TRANSITIONS_VERSION, plan_pair

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    library = ROOT / "web/public/library"
    a = load_track(next(library.glob("demo-amber*")))
    b = load_track(next(library.glob("demo-teal*")))
    doc = {"schema_version": TRANSITIONS_VERSION, "generated_at": "2026-10-05T00:00:00+00:00", "pairs": [plan_pair(a, b, "rules", None, 4)]}
    (ROOT / "contracts/fixtures/sample_transitions.json").write_text(json.dumps(doc, indent=2) + "\n")
    candidate = {"idea": "Mid-track chopped relay", "rationale": "A rhythmic percussion handoff at A's early phrase into B's middle phrase.",
                 "out_start_bar": 16, "moves": [
        {"move": "in_enter", "at_bar": 0, "from_bar": 32},
        {"move": "crossfade", "start_bar": 0, "end_bar": 2, "to": 0.5, "shape": "ramp"},
        {"move": "eq", "deck": "out", "band": "mid", "start_bar": 2, "end_bar": 4, "to": "dip"},
        {"move": "bass_swap", "at_bar": 4},
        {"move": "rhythmic_gate", "deck": "out", "start_bar": 4, "end_bar": 6, "period_beats": 1, "depth": 0.9},
        {"move": "filter_sweep", "deck": "out", "start_bar": 6, "end_bar": 8, "kind": "highpass", "direction": "close"},
        {"move": "volume", "deck": "out", "start_bar": 6.25, "end_bar": 8, "to": 0},
        {"move": "crossfade", "start_bar": 6, "end_bar": 8, "to": 1, "shape": "ramp"},
        {"move": "out_stop", "at_bar": 8},
    ]}
    write(ROOT / "contracts/fixtures/sample_creative_transition.json", doc, candidate, a, b, "fixture_midtrack_relay")
    # Moves v3: A strips to drums and rides its tempo up to B's (124 -> 128), B comes in at its own
    # tempo, then A brakes to a halt on the bar B takes over.
    ride = {"idea": "Drum ride and brake", "rationale": "A's drums ride up to B's tempo, then A brakes as B takes over.",
            "out_start_bar": 16, "moves": [
        {"move": "stem_drop", "deck": "out", "stems": ["bass", "other", "vocals"], "action": "mute", "at_bar": 0},
        {"move": "tempo_ride", "start_bar": 0, "end_bar": 4},
        {"move": "in_enter", "at_bar": 4, "from_bar": 32},
        {"move": "crossfade", "start_bar": 4, "end_bar": 6, "to": 0.5, "shape": "ramp"},
        {"move": "brake", "at_bar": 7.5, "beats": 2},
        {"move": "crossfade", "start_bar": 8, "end_bar": 8, "to": 1, "shape": "cut"},
        {"move": "out_stop", "at_bar": 8},
    ]}
    write(ROOT / "contracts/fixtures/sample_ride_brake_transition.json", doc, ride, a, b, "fixture_ride_brake")
    # Moves v4: A's vocal hook (its bars 20-21) loops three times over B, then A carries on in time.
    hook = {"idea": "Hook loop over B", "rationale": "A's vocal loops while B's groove arrives; A lands back in time.",
            "out_start_bar": 16, "moves": [
        {"move": "in_enter", "at_bar": 0, "from_bar": 32},
        {"move": "crossfade", "start_bar": 0, "end_bar": 4, "to": 0.5, "shape": "ramp"},
        {"move": "loop_hold", "start_bar": 4, "end_bar": 10, "bars": 2},
        {"move": "stem_drop", "deck": "out", "stems": ["drums", "bass", "other"], "action": "mute", "at_bar": 4},
        {"move": "stem_drop", "deck": "out", "stems": ["drums", "bass", "other"], "action": "unmute", "at_bar": 10},
        {"move": "crossfade", "start_bar": 12, "end_bar": 12, "to": 1, "shape": "cut"},
        {"move": "out_stop", "at_bar": 12},
    ]}
    write(ROOT / "contracts/fixtures/sample_hook_loop_transition.json", doc, hook, a, b, "fixture_hook_loop")


def write(path: Path, doc: dict, candidate: dict, a, b, rid: str) -> None:
    recipe, errors = compile_plan(candidate, a, b, rid)
    assert not errors, errors
    critic = critique(recipe, a, b, len(candidate["moves"]))
    assert critic["valid"], critic
    doc = {**doc, "pairs": [{"out_track": a.id, "in_track": b.id, "facts": pair_facts(a, b), "composer": None, "best": 0,
                             "candidates": [{"id": recipe["id"], "source": "rules", "idea": candidate["idea"],
                                "rationale": candidate["rationale"], "plan": {"out_start_bar": candidate["out_start_bar"], "moves": candidate["moves"]},
                                "errors": [], "recipe": recipe, "critic": critic}]}]}
    path.write_text(json.dumps(doc, indent=2) + "\n")


if __name__ == "__main__":
    main()

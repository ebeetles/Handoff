"""Contract C5 (recipe.schema.json v4) checks shared by the compiler and the tests. The web
mirror is web/src/contracts/recipe.ts (assertRecipe); keep the rules in step."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
SCHEMA = json.loads((CONTRACTS / "recipe.schema.json").read_text())


def semantic_errors(r: dict) -> list[str]:
    errs = []
    seen = set()
    for lane in r["lanes"]:
        key = (lane["deck"], lane["control"])
        name = f"{lane['deck']}.{lane['control']}"
        if key in seen:
            errs.append(f"{name}: duplicate lane")
        seen.add(key)
        if (lane["control"] == "xfader") != (lane["deck"] is None):
            errs.append(f"{name}: xfader lanes (and only they) have deck null")
        bars = [p[0] for p in lane["points"]]
        if any(b2 < b1 for b1, b2 in zip(bars, bars[1:])):
            errs.append(f"{name}: bars must not decrease")
        if any(bars.count(b) > 2 for b in bars):
            errs.append(f"{name}: at most two points per bar (a step)")
        if max(bars) > r["bars"]:
            errs.append(f"{name}: point past the recipe's end")
        if lane["control"] == "key" and lane["deck"] != "in":
            errs.append(f"{name}: only the incoming deck's key is shifted by a recipe")
        if lane["control"] == "tempo":
            if lane["deck"] != "out":
                errs.append(f"{name}: only the outgoing deck's tempo can be automated")
            if any(b1 == b2 for b1, b2 in zip(bars, bars[1:])):
                errs.append(f"{name}: tempo lanes ramp; they can't step")
        if lane["control"].startswith("stem."):
            if r["requires"]["stems"] not in (lane["deck"], "both"):
                errs.append(f"{name}: stem lane on a deck the recipe doesn't require stems for")
            if any(p[1] not in (0, 1) for p in lane["points"]):
                errs.append(f"{name}: stem lanes are on/off (0 or 1)")
    for e in r["events"]:
        if e["at_bar"] > r["bars"]:
            errs.append(f"event {e}: past the recipe's end")
        if (e["command"] in ("loop", "brake")) != ("beats" in e):
            errs.append(f"event {e}: loop and brake need beats, and only they have them")
        if e["command"] == "brake" and e["deck"] != "out":
            errs.append(f"event {e}: only the outgoing deck brakes")
    if not any(e["deck"] == "in" and e["command"] == "play" for e in r["events"]):
        errs.append("the incoming deck never plays")
    if not any(e["deck"] == "out" and e["command"] == "pause" for e in r["events"]):
        errs.append("the outgoing deck never stops")
    return errs


def recipe_errors(r: dict) -> list[str]:
    """Schema errors, then semantic ones. Empty = a valid C5 recipe."""
    v = jsonschema.Draft202012Validator(SCHEMA)
    errs = [f"schema: {e.message}" for e in v.iter_errors(r)]
    return errs or semantic_errors(r)

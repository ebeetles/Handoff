"""Contract C5: every recipe in contracts/recipes/ validates against recipe.schema.json and
the rules the schema can't express. web/src/contracts/recipe.ts (assertRecipe) checks the
same rules; keep them in step."""
import json
from pathlib import Path

import sys

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handoff_pipeline.compose.recipes import semantic_errors  # noqa: E402

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "recipe.schema.json").read_text())
FILES = sorted((CONTRACTS / "recipes").glob("*.json"))


def test_there_are_recipes():
    assert len(FILES) >= 5


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_recipe_valid(path):
    r = json.loads(path.read_text())
    jsonschema.validate(r, SCHEMA)
    assert r["id"] == path.stem
    assert semantic_errors(r) == []


def test_semantic_checks_catch_mistakes():
    good = json.loads((CONTRACTS / "recipes" / "bass_swap.json").read_text())
    bad = json.loads(json.dumps(good))
    bad["lanes"][1]["points"] = [[8, 0], [4, 1]]                       # decreasing
    bad["lanes"].append({"deck": "in", "control": "stem.bass", "points": [[0, 0.5]]})
    bad["events"] = [e for e in bad["events"] if e["command"] != "pause"]
    errs = semantic_errors(bad)
    assert any("decrease" in e for e in errs)
    assert any("doesn't require stems" in e for e in errs)
    one_sided = json.loads(json.dumps(good))
    one_sided["requires"]["stems"] = "out"
    one_sided["lanes"].append({"deck": "out", "control": "stem.vocals", "points": [[0, 1], [4, 1], [4, 0]]})
    assert semantic_errors(one_sided) == []
    assert any("on/off" in e for e in errs)
    assert any("never stops" in e for e in errs)

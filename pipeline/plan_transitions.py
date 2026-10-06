"""Compose, compile, and score transitions for pairs of tracks (ROADMAP Chunk 7).

    python plan_transitions.py                                   # rules composer, every pair (free)
    python plan_transitions.py --composer both --max-pairs 3     # + Claude on 3 pairs (costs money)
    python plan_transitions.py --composer both --pairs demo-amber:demo-teal
    python plan_transitions.py --composer llm --estimate --max-pairs 10   # cost estimate, no calls
    python plan_transitions.py --recompile                       # re-score saved plans (after a compiler/critic change)

Writes web/public/library/transitions.json (contract C6), merging with pairs already there. Pairs
saved by an older version are recompiled and re-scored from their stored plans (no API calls).
Claude's replies are cached in pipeline/cache/compose/, so reruns are free. The API key comes
from ANTHROPIC_API_KEY or pipeline/.env.
"""
import argparse
import hashlib
from collections.abc import Callable
import json
import sys
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path

import jsonschema

from handoff_pipeline.compose.compiler import compile_plan
from handoff_pipeline.compose.composer import DEFAULT_MODEL, Composer
from handoff_pipeline.compose.critic import critique
from handoff_pipeline.compose.facts import load_track, pair_facts
from handoff_pipeline.compose.rules_composer import compose_rules

HERE = Path(__file__).resolve().parent
TRANSITIONS_VERSION = 3
SCHEMA = json.loads((HERE.parent / "contracts" / "transitions.schema.json").read_text())
# $ per million tokens: input, output, cache write (5 min), cache read. Opus 5.5 list prices.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.2)}


def cost(usage: dict | None, model: str) -> float:
    if not usage or model not in PRICES:
        return 0.0
    p_in, p_out, p_cw, p_cr = PRICES[model]
    return (usage.get("input_tokens", 0) * p_in + usage.get("output_tokens", 0) * p_out
            + (usage.get("cache_creation_input_tokens") or 0) * p_cw + (usage.get("cache_read_input_tokens") or 0) * p_cr) / 1e6


def plan_pair(a, b, composer_kind: str, composer: Composer | None, n: int, **options) -> dict:
    raw: list[tuple[str, dict]] = [("rules", c) for c in compose_rules(a, b)] if composer_kind in ("rules", "both") else []
    meta = None
    if composer_kind in ("llm", "both") and composer:
        cands, meta = composer.compose(a, b, n, **options)
        raw += [("llm", c) for c in cands]
    return _pair(a, b, _candidates(raw, a, b, options), meta)


def recompile_pair(a, b, saved: dict) -> dict:
    """A saved pair, rebuilt from its stored plans with the current compiler and critic (after a
    contract or critic change), without asking the composer again."""
    raw = [(c["source"], {"idea": c["idea"], "rationale": c["rationale"], **c["plan"]}) for c in saved["candidates"]]
    return _pair(a, b, _candidates(raw, a, b, {}), saved.get("composer"))


def load_saved(path: Path, track: Callable[[str], object | None]) -> dict[tuple[str, str], dict]:
    """Saved pairs by (out, in). Pairs from an older transitions version are recompiled (pairs
    whose tracks have left the library are dropped)."""
    if not path.exists():
        return {}
    doc = json.loads(path.read_text())
    pairs = {(p["out_track"], p["in_track"]): p for p in doc.get("pairs", [])}
    if doc.get("schema_version") == TRANSITIONS_VERSION:
        return pairs
    out = {}
    for (o, i), p in pairs.items():
        a, b = track(o), track(i)
        if a and b:
            out[(o, i)] = recompile_pair(a, b, p)
    return out


def merge_pair(saved: dict | None, new: dict) -> dict:
    """`new` composed for a pair, keeping candidates saved before (other exits, other composers:
    LLM compositions cost money). Same id = same plan, replaced by the new copy; best re-ranked."""
    candidates = {c["id"]: c for c in (saved or {}).get("candidates", []) + new["candidates"]}
    out = {**new, "candidates": list(candidates.values())}
    if new.get("composer") is None and saved:
        out["composer"] = saved.get("composer")
    valid = [i for i, c in enumerate(out["candidates"]) if c["recipe"] and c["critic"] and c["critic"]["valid"]]
    out["best"] = max(valid, key=lambda i: out["candidates"][i]["critic"]["score"]) if valid else None
    return out


def _pair(a, b, out: list[dict], meta: dict | None) -> dict:
    valid = [i for i, c in enumerate(out) if c["critic"] and c["critic"]["valid"]]
    best = max(valid, key=lambda i: out[i]["critic"]["score"]) if valid else None
    return {"out_track": a.id, "in_track": b.id, "facts": pair_facts(a, b), "composer": meta, "candidates": out, "best": best}


def _candidates(raw: list[tuple[str, dict]], a, b, options: dict) -> list[dict]:
    out = []
    for k, (source, c) in enumerate(raw):
        digest = hashlib.sha256(json.dumps([a.id, b.id, c], sort_keys=True).encode()).hexdigest()[:16]
        rid = f"{source}_{digest}"
        recipe, errors = compile_plan(c, a, b, rid)
        if source == "llm" and recipe:
            if c["out_start_bar"] < options.get("min_start_bar", 0):
                errors.append("exit is before the requested earliest bar")
            if options.get("out_start_bar") is not None and c["out_start_bar"] != options["out_start_bar"]:
                errors.append("exit does not match the requested phrase")
            if recipe["bars"] > options.get("max_bars", 32):
                errors.append("transition exceeds the requested duration")
            if errors:
                recipe = None
        verdict = critique(recipe, a, b, len(c.get("moves", []))) if recipe else None
        out.append({"id": rid, "source": source, "idea": c.get("idea", ""), "rationale": c.get("rationale", ""),
                    "plan": {"out_start_bar": c.get("out_start_bar"), "moves": c.get("moves", [])},
                    "errors": errors, "recipe": recipe, "critic": verdict})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", type=Path, default=HERE.parent / "web" / "public" / "library")
    ap.add_argument("--composer", choices=["rules", "llm", "both"], default="rules")
    ap.add_argument("--pairs", nargs="*", default=[], help="OUT:IN track-id prefixes, e.g. demo-amber:demo-teal")
    ap.add_argument("--max-pairs", type=int, help="compose at most this many pairs (required for the LLM without --pairs)")
    ap.add_argument("--candidates", type=int, default=4, help="LLM candidates per pair")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--estimate", action="store_true", help="print the request size and a rough cost; make no calls")
    ap.add_argument("--brief", default="Creative, energetic, track-specific transitions")
    ap.add_argument("--out-start-bar", type=int, help="compose for this exact phrase, including mid-track")
    ap.add_argument("--min-start-bar", type=int, default=0, help="ignore exits before this bar")
    ap.add_argument("--max-bars", type=int, default=32, help="maximum transition duration (4..32 bars)")
    ap.add_argument("--recompile", action="store_true", help="rebuild every saved pair from its stored plans and exit (no composing, no calls)")
    args = ap.parse_args()

    tracks = {d.name: load_track(d) for d in sorted(args.library.iterdir()) if (d / "analysis.json").exists()}
    find = lambda pre: next((t for i, t in tracks.items() if i.startswith(pre)), None)  # noqa: E731
    if args.pairs:
        pairs = []
        for spec in args.pairs:
            o, _, i = spec.partition(":")
            a, b = find(o), find(i)
            if not a or not b:
                sys.exit(f"No track matches {spec!r}.")
            pairs.append((a, b))
    else:
        pairs = list(permutations(tracks.values(), 2))
    if args.max_pairs:
        pairs = pairs[:args.max_pairs]
    if args.composer != "rules" and not (args.pairs or args.max_pairs):
        sys.exit(f"Composing all {len(pairs)} pairs with the LLM costs real money: choose --pairs or --max-pairs.")

    composer = Composer(HERE / "cache" / "compose", model=args.model, effort=args.effort) if args.composer != "rules" else None
    options = dict(brief=args.brief, out_start_bar=args.out_start_bar, min_start_bar=args.min_start_bar, max_bars=args.max_bars)
    if args.estimate:
        p = composer.params(*pairs[0], args.candidates, **options) if composer else None
        chars = len(json.dumps(p)) if p else 0
        print(f"{len(pairs)} pairs; first request ~{chars // 4} input tokens (system prompt cached after the first).")
        print("Output (thinking + plans) varies; at ~8k output tokens a pair costs roughly "
              f"${cost({'input_tokens': chars // 4, 'output_tokens': 8000}, args.model):.2f}.")
        return

    out_path = args.library / "transitions.json"
    # Pairs whose tracks have left the library go too.
    results = {k: p for k, p in load_saved(out_path, lambda tid: tracks.get(tid)).items() if k[0] in tracks and k[1] in tracks}
    if args.recompile:
        results = {k: recompile_pair(tracks[k[0]], tracks[k[1]], p) for k, p in results.items()}
        pairs = []
    spent, llm_total, llm_ok = 0.0, 0, 0
    for a, b in pairs:
        fresh = plan_pair(a, b, args.composer, composer, args.candidates, **options)
        r = results[(a.id, b.id)] = merge_pair(results.get((a.id, b.id)), fresh)
        meta = fresh["composer"] or {}
        if not meta.get("cached"):
            spent += cost(meta.get("usage"), meta.get("model", args.model))
        llm = [c for c in fresh["candidates"] if c["source"] == "llm"]
        llm_total += len(llm)
        llm_ok += sum(c["recipe"] is not None for c in llm)
        best = r["candidates"][r["best"]] if r["best"] is not None else None
        note = f" [LLM: {meta['error']}]" if meta.get("error") else (" [LLM cached]" if meta.get("cached") else "")
        print(f"{a.title[:22]:>22} -> {b.title[:22]:<22} " + (f"{best['critic']['score']:5.1f} {best['source']:5} {best['idea'][:40]}" if best else "  no valid candidate") + note)

    doc = {"schema_version": TRANSITIONS_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "pairs": sorted(results.values(), key=lambda p: (p["out_track"], p["in_track"]))}
    jsonschema.validate(doc, SCHEMA)
    out_path.write_text(json.dumps(doc, separators=(",", ":")))
    if args.composer != "rules":
        print(f"\nLLM: {llm_ok}/{llm_total} candidates compiled; {composer.calls} API calls; ~${spent:.2f} this run.")
    print(f"{len(doc['pairs'])} pairs in {out_path}")


if __name__ == "__main__":
    main()

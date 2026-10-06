// Mirror of contracts/transitions.schema.json (v3): composed transitions per ordered pair,
// written by pipeline/plan_transitions.py (Chunk 7) to library/transitions.json.
import { assertRecipe, type Recipe } from "./recipe";

export const TRANSITIONS_SCHEMA_VERSION = 3;

export interface CriticVerdict {
  valid: boolean;
  score: number;          // 0..100
  breakdown: Record<string, number>;
  measures: Record<string, number | boolean>;
  reasons: string[];
  worst: string | null;
}

export interface Candidate {
  id: string;
  source: "llm" | "rules";
  idea: string;
  rationale: string;
  plan: { out_start_bar: number | null; moves: Record<string, unknown>[] };
  errors: string[];
  recipe: Recipe | null;
  critic: CriticVerdict | null;
}

export interface PairTransitions {
  out_track: string;
  in_track: string;
  facts: Record<string, unknown>;
  composer: Record<string, unknown> | null;
  best: number | null;
  candidates: Candidate[];
}

export interface Transitions { schema_version: 3; generated_at: string; pairs: PairTransitions[] }

/** Checks the version and every compiled recipe (the player trusts them). */
export function assertTransitions(x: unknown): asserts x is Transitions {
  const t = x as Transitions;
  if (!t || t.schema_version !== TRANSITIONS_SCHEMA_VERSION || !Array.isArray(t.pairs)) {
    throw new Error(`transitions.json: expected schema_version ${TRANSITIONS_SCHEMA_VERSION}. Re-run pipeline/plan_transitions.py.`);
  }
  for (const p of t.pairs) {
    if (!p || typeof p.out_track !== "string" || typeof p.in_track !== "string" || !Array.isArray(p.candidates)) {
      throw new Error("transitions: invalid track pair");
    }
    if (p.best !== null && (!Number.isInteger(p.best) || p.best < 0 || p.best >= p.candidates.length)) {
      throw new Error("transitions: invalid best candidate index");
    }
    for (const c of p.candidates) {
      if (!c || typeof c.id !== "string" || !["llm", "rules"].includes(c.source) || typeof c.idea !== "string"
          || typeof c.rationale !== "string" || !c.plan || !Array.isArray(c.plan.moves)
          || !Array.isArray(c.errors) || c.errors.some((e) => typeof e !== "string")) {
        throw new Error("transitions: invalid candidate");
      }
      if (c.critic && (typeof c.critic.valid !== "boolean" || !Number.isFinite(c.critic.score)
          || c.critic.score < 0 || c.critic.score > 100 || !Array.isArray(c.critic.reasons))) {
        throw new Error("transitions: invalid critic verdict");
      }
      if (c.recipe) {
        assertRecipe(c.recipe);
        const a = c.recipe.anchor;
        if (!a || a.out_track !== p.out_track || a.in_track !== p.in_track
            || !Number.isInteger(a.out_start_bar) || a.out_start_bar < 0
            || !Number.isInteger(a.in_from_bar) || a.in_from_bar < 0 || !c.critic) {
          throw new Error("transitions: invalid recipe anchor or missing critic");
        }
      }
    }
    if (p.best !== null && (!p.candidates[p.best]!.recipe || !p.candidates[p.best]!.critic?.valid)) {
      throw new Error("transitions: best candidate isn't playable");
    }
  }
}

// Mirror of contracts/recipe.schema.json (v4): transition recipes. Presets are authored by hand
// in contracts/recipes/*.json; Chunk 7 compiles anchored ones per pair (transitions.json). pipeline/tests/test_recipes.py checks the same rules on the
// Python side (the Chunk 7 planner reads them too). Change both, and bump schema_version.

export const RECIPE_SCHEMA_VERSION = 4;
export const RECIPE_CONTROLS = ["eqLow", "eqMid", "eqHigh", "filter", "volume", "echo",
  "stem.drums", "stem.bass", "stem.vocals", "stem.other", "xfader", "tempo", "key"] as const;
export type RecipeControl = (typeof RECIPE_CONTROLS)[number];
export type RecipeDeck = "in" | "out";

/** [bar, value 0..1]. Same bar twice = a step. xfader values run out (0) -> in (1). tempo lanes
 *  (outgoing deck only) ramp, never step, and start from the deck's tempo at the time. */
export type Point = [number, number];
export interface Lane { deck: RecipeDeck | null; control: RecipeControl; points: Point[] }
interface EventBase { at_bar: number; deck: RecipeDeck }
export type RecipeEvent =
  | (EventBase & { command: "play" })
  | (EventBase & { command: "pause" })
  | (EventBase & { command: "loop_off" })
  | (EventBase & { command: "loop"; beats: number })
  | (EventBase & { command: "brake"; beats: number });   // outgoing deck: turntable stop over `beats` beats

export type StemNeed = "none" | "out" | "in" | "both";

/** Composed recipes start when the outgoing deck reaches out_start_bar; the incoming deck plays
 *  from in_from_bar. Presets have no anchor (next phrase; incoming deck from where it is). */
export interface RecipeAnchor { out_track?: string; in_track?: string; out_start_bar: number; in_from_bar: number }

export interface Recipe {
  schema_version: 4;
  id: string;
  name: string;
  description: string;
  bars: number;
  requires: { stems: StemNeed; beatmatchable: boolean; max_camelot_distance: number | null };
  anchor?: RecipeAnchor;
  events: RecipeEvent[];
  lanes: Lane[];
}

/** Structural + semantic check (the same rules as test_recipes.py). Throws with every problem. */
export function assertRecipe(x: unknown): asserts x is Recipe {
  const r = x as Recipe;
  const errs: string[] = [];
  if (!r || typeof r !== "object") throw new Error("recipe: not an object");
  if (r.schema_version !== RECIPE_SCHEMA_VERSION) errs.push(`schema_version ${String(r.schema_version)} != ${RECIPE_SCHEMA_VERSION}`);
  if (!(typeof r.bars === "number" && r.bars > 0)) errs.push("bars must be > 0");
  if (!Array.isArray(r.lanes) || !Array.isArray(r.events) || !r.requires) errs.push("missing lanes/events/requires");
  else {
    const seen = new Set<string>();
    for (const l of r.lanes) {
      const name = `${l.deck}.${l.control}`;
      if (!RECIPE_CONTROLS.includes(l.control)) errs.push(`${name}: unknown control`);
      if (seen.has(name)) errs.push(`${name}: duplicate lane`);
      seen.add(name);
      if ((l.control === "xfader") !== (l.deck === null)) errs.push(`${name}: xfader lanes (and only they) have deck null`);
      if (!Array.isArray(l.points) || l.points.length === 0) { errs.push(`${name}: no points`); continue; }
      const bars = l.points.map((p) => p[0]);
      if (bars.some((b, i) => i > 0 && b < bars[i - 1]!)) errs.push(`${name}: bars must not decrease`);
      if (bars.some((b) => bars.filter((c) => c === b).length > 2)) errs.push(`${name}: at most two points per bar (a step)`);
      if (Math.max(...bars) > r.bars || Math.min(...bars) < 0) errs.push(`${name}: point outside 0..bars`);
      if (l.points.some((p) => !(p[1] >= 0 && p[1] <= 1))) errs.push(`${name}: values must be 0..1`);
      if (l.control === "key" && l.deck !== "in") errs.push(`${name}: only the incoming deck's key is shifted by a recipe`);
      if (l.control === "tempo") {
        if (l.deck !== "out") errs.push(`${name}: only the outgoing deck's tempo can be automated`);
        if (bars.some((b, i) => i > 0 && b === bars[i - 1])) errs.push(`${name}: tempo lanes ramp; they can't step`);
      }
      if (l.control.startsWith("stem.")) {
        if (r.requires.stems !== l.deck && r.requires.stems !== "both") errs.push(`${name}: stem lane on a deck the recipe doesn't require stems for`);
        if (l.points.some((p) => p[1] !== 0 && p[1] !== 1)) errs.push(`${name}: stem lanes are on/off (0 or 1)`);
      }
    }
    for (const e of r.events) {
      if (e.at_bar < 0 || e.at_bar > r.bars) errs.push(`event at bar ${e.at_bar}: outside 0..bars`);
      const hasBeats = e.command === "loop" || e.command === "brake";
      if (hasBeats !== ("beats" in e)) errs.push(`event at bar ${e.at_bar}: loop and brake need beats, and only they have them`);
      if (e.command === "brake" && e.deck !== "out") errs.push(`event at bar ${e.at_bar}: only the outgoing deck brakes`);
    }
    if (!r.events.some((e) => e.deck === "in" && e.command === "play")) errs.push("the incoming deck never plays");
    if (!r.events.some((e) => e.deck === "out" && e.command === "pause")) errs.push("the outgoing deck never stops");
  }
  if (errs.length) throw new Error(`recipe "${r.id ?? "?"}" is invalid: ${errs.join("; ")}`);
}

// Pure recipe math: lane interpolation, steps, key distance, and whether a recipe can run on
// the two loaded decks. No audio, no clock; automation.ts drives these.
import { tempoRate } from "../audio/mapping";
import { chooseSync } from "../audio/sync";
import type { DeckSnapshot } from "../audio/engine";
import type { ControlId, DeckId } from "../control/controls";
import type { Lane, Point, Recipe } from "../contracts/recipe";

/** Lane value at a (fractional, recipe-relative) bar. Held before the first point and after
 *  the last; linear between points; at a step (two points at one bar) the later value wins. */
export function laneValueAt(points: readonly Point[], bar: number): number {
  let i = -1;
  while (i + 1 < points.length && points[i + 1]![0] <= bar) i++;
  if (i < 0) return points[0]![1];
  if (i === points.length - 1) return points[i]![1];
  const [b0, v0] = points[i]!, [b1, v1] = points[i + 1]!;
  return v0 + ((v1 - v0) * (bar - b0)) / (b1 - b0);
}

/** The steps in a lane: where it jumps, and to what. These land exactly on the audio clock. */
export function laneSteps(points: readonly Point[]): { bar: number; value: number }[] {
  const out: { bar: number; value: number }[] = [];
  for (let i = 1; i < points.length; i++) {
    if (points[i]![0] === points[i - 1]![0]) out.push({ bar: points[i]![0], value: points[i]![1] });
  }
  return out;
}

export const isToggleLane = (l: Lane) => l.control.startsWith("stem.");

/** The outgoing deck's tempo ride, if the recipe has one. */
export const rideLane = (r: Recipe): Lane | undefined => r.lanes.find((l) => l.deck === "out" && l.control === "tempo");

/** A ride lane starting from the deck's current tempo `v`: the compiled lane assumes the deck's own
 *  tempo (0.5) until the ride's first change; a deck already pitched elsewhere glides from there. */
export function rideFrom(l: Lane, v: number): Lane {
  const first = l.points.findIndex((p) => p[1] !== l.points[0]![1]);
  return { ...l, points: l.points.map((p, i) => (first < 0 || i < first ? [p[0], v] : p)) };
}

/** Steps round the Camelot wheel, +1 for a major/minor (A/B) change. 8A-8B = 1, 8A-9A = 1,
 *  12A-1A = 1, 8A-10B = 3. Unparseable keys count as far apart. */
export function camelotDistance(a: string, b: string): number {
  const pa = /^(\d{1,2})([AB])$/.exec(a), pb = /^(\d{1,2})([AB])$/.exec(b);
  if (!pa || !pb) return 99;
  const d = Math.abs(Number(pa[1]) - Number(pb[1])) % 12;
  return Math.min(d, 12 - d) + (pa[2] === pb[2] ? 0 : 1);
}

/** The store control a lane drives, once "in"/"out" are known. */
export function laneControl(l: Lane, out: DeckId, inn: DeckId): ControlId {
  if (l.deck === null) return "xfader";
  return `${l.deck === "out" ? out : inn}.${l.control}` as ControlId;
}

/** Recipe values run out (0) -> in (1) for the crossfader; the store's run A (0) -> B (1). */
export function toStoreValue(l: Lane, v: number, out: DeckId): number {
  return l.control === "xfader" && out === "B" ? 1 - v : v;
}

/** Why this recipe can't run from `out` into `inn` right now (empty = it can). */
export function blockers(r: Recipe, out: DeckSnapshot, inn: DeckSnapshot): string[] {
  const why: string[] = [];
  if (!out.loaded || !out.playing || out.pending) why.push("the outgoing deck isn't playing");
  if (!inn.loaded) why.push("the other deck has no track");
  else if (inn.playing) why.push("the incoming deck is already playing");
  if (why.length) return why;
  const stems = r.requires.stems;
  if ((stems === "out" || stems === "both") && !out.hasStems) why.push("it needs stems on the outgoing track");
  if ((stems === "in" || stems === "both") && !inn.hasStems) why.push("it needs stems on the incoming track");
  if (r.anchor?.out_track && (r.anchor.out_track !== out.trackId || r.anchor.in_track !== inn.trackId)) {
    why.push("it was composed for a different pair of tracks");
  }
  if (r.requires.beatmatchable) {
    // A tempo ride takes the outgoing deck to the lane's last tempo before the incoming deck syncs.
    const ride = rideLane(r);
    const outBpm = ride ? out.trackBpm * tempoRate(ride.points[ride.points.length - 1]![1]) : out.bpm;
    if (!(out.beatmatchable && inn.beatmatchable)) why.push("one of the tracks has a drifting tempo");
    else if (!chooseSync(inn.trackBpm, outBpm)) why.push(`the tempos are too far apart (${inn.trackBpm.toFixed(0)} vs ${outBpm.toFixed(0)} BPM)`);
  }
  const max = r.requires.max_camelot_distance;
  if (max !== null && camelotDistance(out.camelot, inn.camelot) > max) {
    why.push(`the keys clash (${out.camelot} and ${inn.camelot})`);
  }
  return why;
}

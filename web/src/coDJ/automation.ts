// Plays a recipe over the board's own controls, the way a human would: lanes write to the
// ControlStore with source "auto", events and exact steps go out as Commands. Never calls the
// engine directly (AGENTS.md); it only READS the engine's clock through TransportView.
//
// Timing ("two clocks"): a timer every TICK_MS looks LOOKAHEAD_S ahead on the audio clock.
//   - Steps (a lane jumping on a bar) and "play" events are scheduled for their exact
//     AudioContext time (setControlAt / playAt), so they land on the bar, not on a tick.
//   - Ramps are sampled every tick into the store (the engine smooths each write over ~12 ms).
//   - A brake is scheduled for its exact time too (brakeAt).
//   - A tempo ride (the outgoing deck's tempo lane) is a ramp from wherever that deck's tempo is.
//     The incoming deck then syncs as it comes in, to the tempo the ride arrived at.
//   - Loops (rolls, held hooks) slip: the outgoing deck's bar clock runs on through them.
//   - Other events (pause, loop, loop_off) fire on the first tick at or after their bar. The
//     engine snaps loops to the grid, so landing a few ms late is right, and early would be wrong.
// Bars are counted on the OUTGOING deck's grid from the transition's start (a phrase boundary);
// during a roll its clock is where playback would be without the roll (engine.barAt).
//
// Takeover: if a human touches a lane's control (writes it, or holds it), that lane stops
// for the rest of the recipe. Lanes on the playing deck glide from their current value over
// the first bar instead of jumping. At the end, the outgoing deck's touched controls go back
// to neutral, ready for its next track.
import type { DeckSnapshot } from "../audio/engine";
import type { Command } from "../control/commands";
import { CONTROL_DEFS, type ControlId, type ControlStore, type DeckId } from "../control/controls";
import type { Lane, Recipe, RecipeEvent } from "../contracts/recipe";
import { blockers, isToggleLane, laneControl, laneSteps, laneValueAt, rideFrom, rideLane, toStoreValue } from "./lanes";

export const TICK_MS = 25;
export const LOOKAHEAD_S = 0.1;
const MIN_LEAD_BARS = 1;   // time to sync and schedule the incoming deck before the transition

/** What the player reads from the engine (AudioEngine implements this). */
export interface TransportView {
  readonly ctx: { readonly currentTime: number };
  snapshot(d: DeckId): DeckSnapshot;
  barAt(d: DeckId, t: number): number;
  barToCtx(d: DeckId, bar: number): number;
  nextPhraseBar(d: DeckId, minBarsAhead: number): number | null;
}

export interface Timer { every(ms: number, fn: () => void): () => void }
const realTimer: Timer = {
  every: (ms, fn) => { const id = setInterval(fn, ms); return () => clearInterval(id); },
};

export type PlayerState = "idle" | "armed" | "running" | "done" | "stopped" | "error";
export interface PlayerStatus {
  state: PlayerState;
  message: string;
  recipe: Recipe | null;
  out: DeckId | null;
  in: DeckId | null;
  startBar: number;
  bar: number;               // recipe-relative, negative while armed
  takenOver: ControlId[];
}

interface LivePlan {
  recipe: Recipe;
  out: DeckId;
  inn: DeckId;
  startBar: number;
  lanes: { lane: Lane; id: ControlId; steps: { bar: number; value: number; done: boolean }[]; glideFrom: number | null; last: number | null; settled?: boolean }[];
  events: (RecipeEvent & { done: boolean })[];
  taken: Set<ControlId>;
}

const IDLE: PlayerStatus = { state: "idle", message: "", recipe: null, out: null, in: null, startBar: 0, bar: 0, takenOver: [] };

export class AutomationPlayer {
  private plan: LivePlan | null = null;
  private st: PlayerStatus = IDLE;
  private stopTimer: (() => void) | null = null;
  private listeners = new Set<() => void>();
  /** Every exact step scheduled, with the recipe bar it belongs to (for tests: does it land on the bar?). */
  readonly stepLog: { control: ControlId; bar: number; at: number }[] = [];

  constructor(private readonly view: TransportView, private readonly store: ControlStore,
              private readonly dispatch: (c: Command) => void, private readonly timer: Timer = realTimer) {
    store.subscribeAll((id, _v, source) => {
      if (this.plan && source !== "auto" && source !== "engine" && this.plan.lanes.some((l) => l.id === id)) this.takeOver(id);
    });
  }

  status(): PlayerStatus { return this.st; }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Why `recipe` can't start now (empty = it can), and which way it would go. */
  check(recipe: Recipe): { out: DeckId; inn: DeckId; reasons: string[] } {
    const a = this.view.snapshot("A"), b = this.view.snapshot("B");
    // A composed recipe goes out of the deck holding its outgoing track. Otherwise the outgoing
    // deck is the one playing; if both are, the one the crossfader favours.
    const anchored = recipe.anchor?.out_track;
    const out: DeckId = anchored ? (b.trackId === anchored ? "B" : "A")
      : a.playing && b.playing ? (this.store.get("xfader") <= 0.5 ? "A" : "B") : b.playing ? "B" : "A";
    const inn: DeckId = out === "A" ? "B" : "A";
    const reasons = this.plan ? ["a transition is already running"] : blockers(recipe, this.view.snapshot(out), this.view.snapshot(inn));
    if (!reasons.length && recipe.anchor && this.view.barAt(out, this.view.ctx.currentTime) > recipe.anchor.out_start_bar - MIN_LEAD_BARS) {
      reasons.push(`the outgoing track is already past bar ${recipe.anchor.out_start_bar}, where this transition starts`);
    }
    return { out, inn, reasons };
  }

  /** Arm `recipe` to start on the outgoing deck's next phrase (at least a bar away). */
  start(recipe: Recipe): PlayerStatus {
    const { out, inn, reasons } = this.check(recipe);
    if (reasons.length) return this.set({ ...IDLE, state: "error", recipe, message: `Can't start ${recipe.name}: ${reasons.join("; ")}.` });
    let startBar: number | null;
    if (recipe.anchor) {
      startBar = recipe.anchor.out_start_bar;
      const bar = this.view.barAt(out, this.view.ctx.currentTime);
      if (bar > startBar - MIN_LEAD_BARS) {
        return this.set({ ...IDLE, state: "error", recipe, message: `The outgoing track is already past bar ${startBar}, where this transition starts.` });
      }
    } else {
      startBar = this.view.nextPhraseBar(out, MIN_LEAD_BARS);
      if (startBar === null) return this.set({ ...IDLE, state: "error", recipe, message: "No phrase boundary left on the outgoing track." });
    }

    const riding = rideLane(recipe) !== undefined;
    if (recipe.requires.beatmatchable && !riding) this.dispatch({ type: "sync", deck: inn });
    const lanes = recipe.lanes.map((compiled) => {
      const id = laneControl(compiled, out, inn);
      const lane = compiled.control === "tempo" ? rideFrom(compiled, this.store.get(id)) : compiled;
      const first = toStoreValue(lane, lane.points[0]![1], out);
      // The incoming deck is silent: set its starting values now. Lanes on what's audible
      // (outgoing deck, crossfader) glide from where they are over the first bar.
      if (lane.deck === "in") this.store.set(id, first, "auto");
      const glideFrom = lane.deck === "in" || isToggleLane(lane) || lane.control === "tempo" ? null : this.store.get(id);
      return { lane, id, glideFrom, last: null, steps: laneSteps(lane.points).map((s) => ({ ...s, done: false })) };
    });
    this.plan = { recipe, out, inn, startBar, lanes, events: recipe.events.map((e) => ({ ...e, done: false })), taken: new Set() };
    this.stopTimer = this.timer.every(TICK_MS, () => this.tick());
    this.set({ state: "armed", message: "", recipe, out, in: inn, startBar, bar: this.relBar(this.view.ctx.currentTime), takenOver: [] });
    this.tick();
    return this.st;
  }

  /** Abandon the transition where it is (nothing is reset). */
  stop(message = "Stopped."): void {
    if (!this.plan) return;
    this.finish("stopped", message, false);
  }

  /** One scheduler pass. Public for tests (they drive a fake clock). */
  tick(): void {
    const p = this.plan;
    if (!p) return;
    const now = this.view.ctx.currentTime, horizon = now + LOOKAHEAD_S;
    const bar = this.relBar(now);
    const at = (b: number) => this.view.barToCtx(p.out, p.startBar + b);

    if (bar >= 0 && !this.view.snapshot(p.out).playing && p.events.some((e) => !e.done && e.deck === "out" && e.command === "pause")) {
      return this.finish("stopped", "The outgoing deck stopped, so the transition stopped.", false);
    }

    for (const e of p.events) {
      if (e.done) continue;
      let t = at(e.at_bar);
      const deck = e.deck === "out" ? p.out : p.inn;
      const exact = e.command === "play" || e.command === "brake";
      if (exact ? t <= horizon : now >= t) {
        e.done = true;
        if (e.command === "play" && e.deck === "in") t = this.syncAfterRide(p, e.at_bar) ?? t;
        this.dispatch(e.command === "play" ? { type: "playAt", deck, at: t, ...(p.recipe.anchor && e.deck === "in" ? { fromBar: p.recipe.anchor.in_from_bar } : {}) }
          : e.command === "brake" ? { type: "brakeAt", deck, at: t, beats: e.beats }
          : e.command === "pause" ? { type: "pause", deck }
          : e.command === "loop_off" ? { type: "loopOff", deck }
          : { type: "loop", deck, beats: e.beats, slip: true });
      }
    }

    for (const l of p.lanes) {
      if (p.taken.has(l.id) || l.settled) continue;
      if (this.store.isHeld(l.id)) { this.takeOver(l.id); continue; }
      for (const s of l.steps) {
        if (s.done) continue;
        const t = at(s.bar);
        if (t > horizon) continue;
        s.done = true;
        const v = toStoreValue(l.lane, s.value, p.out);
        this.dispatch({ type: "setControlAt", control: l.id, value: v, at: t });
        this.stepLog.push({ control: l.id, bar: s.bar, at: t });
        l.last = v;
      }
      if (bar < 0 || isToggleLane(l.lane)) continue;
      let v = laneValueAt(l.lane.points, bar);
      if (l.glideFrom !== null && bar < 1) v = this.fromStore(l, l.glideFrom) + (v - this.fromStore(l, l.glideFrom)) * bar;
      const sv = toStoreValue(l.lane, v, p.out);
      // Any change, however small: a "close enough" threshold skipped the last bit of a ramp
      // when a tick landed just before its end (the lane then stopped 2e-5 short).
      if (sv !== l.last) {
        // A step scheduled within the lookahead hasn't sounded yet: don't pre-empt it in the store.
        const pending = l.steps.some((s) => s.done && at(s.bar) > now && Math.abs(laneValueAt(l.lane.points, s.bar) - v) > 1e-9);
        if (!pending) { this.store.set(l.id, sv, "auto"); l.last = sv; }
      }
    }

    if (bar >= p.recipe.bars && p.events.every((e) => e.done)) return this.finish("done", `${p.recipe.name} done.`, true);
    const state: PlayerState = bar >= 0 ? "running" : "armed";
    if (state !== this.st.state || Math.floor(bar) !== Math.floor(this.st.bar)) this.set({ ...this.st, state, bar });
    else this.st = { ...this.st, bar };
  }

  /** Before the incoming deck starts after a tempo ride: land the ride on its final value (it may
   *  be a tick short; the lookahead runs ~100 ms early), sync the incoming deck to the tempo the
   *  outgoing one now has, and return the entry time on the re-anchored clock. */
  private syncAfterRide(p: LivePlan, bar: number): number | null {
    const ride = p.lanes.find((l) => l.lane.deck === "out" && l.lane.control === "tempo");
    if (!ride) return null;
    if (!p.taken.has(ride.id)) {
      const v = laneValueAt(ride.lane.points, bar);
      if (v !== ride.last) { this.store.set(ride.id, v, "auto"); ride.last = v; }
      ride.settled = true;   // the ride is over: later ticks mustn't pull it back a few ms
    }
    if (p.recipe.requires.beatmatchable) this.dispatch({ type: "sync", deck: p.inn });
    return this.view.barToCtx(p.out, p.startBar + bar);
  }

  private relBar(t: number): number {
    return this.plan ? this.view.barAt(this.plan.out, t) - this.plan.startBar : 0;
  }

  /** A store value back in recipe terms (only the crossfader differs). */
  private fromStore(l: LivePlan["lanes"][number], v: number): number {
    return toStoreValue(l.lane, v, this.plan!.out);   // the mapping is its own inverse
  }

  private takeOver(id: ControlId): void {
    const p = this.plan!;
    if (p.taken.has(id)) return;
    p.taken.add(id);
    this.set({ ...this.st, takenOver: [...p.taken] });
  }

  private finish(state: PlayerState, message: string, cleanUp: boolean): void {
    const p = this.plan!;
    this.stopTimer?.();
    this.stopTimer = null;
    if (cleanUp) {
      for (const l of p.lanes) {
        if (l.lane.deck === "out" && !p.taken.has(l.id)) this.store.set(l.id, CONTROL_DEFS[l.id].default, "auto");
      }
    }
    this.plan = null;
    this.set({ ...this.st, state, message, takenOver: [...p.taken] });
  }

  private set(s: PlayerStatus): PlayerStatus {
    this.st = s;
    this.listeners.forEach((fn) => fn());
    return s;
  }
}

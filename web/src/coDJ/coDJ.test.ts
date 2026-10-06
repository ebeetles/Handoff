import { describe, expect, it } from "vitest";
import type { DeckSnapshot } from "../audio/engine";
import type { Command } from "../control/commands";
import { ControlStore, type DeckId } from "../control/controls";
import { assertRecipe, type Recipe } from "../contracts/recipe";
import { AutomationPlayer, LOOKAHEAD_S, TICK_MS, type TransportView } from "./automation";
import { blockers, camelotDistance, laneSteps, laneValueAt, rideFrom } from "./lanes";
import { RECIPES } from "./recipes";
import { playable } from "./transitions";
import { assertTransitions } from "../contracts/transitions";
import transitionsFixture from "../../../contracts/fixtures/sample_transitions.json";
import rideFixture from "../../../contracts/fixtures/sample_ride_brake_transition.json";

const recipe = (id: string): Recipe => RECIPES.find((r) => r.id === id)!;

function snap(o: Partial<DeckSnapshot> = {}): DeckSnapshot {
  return {
    loaded: true, trackId: "t", loading: null, error: null, title: "t", artist: "a", camelot: "8A", keyName: "", duration: 300,
    position: 0, playing: false, pending: false, rate: 1, trackBpm: 120, bpm: 120, barPos: 0, beatInBar: 0,
    phraseIndex: 0, phraseCount: 8, phraseBars: 16, barInPhrase: 0, barsToNextPhrase: 16, sectionLabel: "", loop: null,
    cue: 0, level: 0, synced: false, hasStems: false, beatmatchable: true, ...o,
  };
}

/** A fake engine: the outgoing deck's bar 0 is at t0, 2 s per bar (120 BPM), phrases every 16 bars. */
function rig(out: DeckId = "A", o: { inSnap?: Partial<DeckSnapshot>; outSnap?: Partial<DeckSnapshot>; now?: number } = {}) {
  const clock = { now: o.now ?? 101, t0: 100, barDur: 2 };
  const inn: DeckId = out === "A" ? "B" : "A";
  const snaps: Record<DeckId, DeckSnapshot> = {
    [out]: snap({ playing: true, ...o.outSnap }), [inn]: snap(o.inSnap),
  } as Record<DeckId, DeckSnapshot>;
  const view: TransportView = {
    ctx: { get currentTime() { return clock.now; } },
    snapshot: (d) => snaps[d],
    barAt: (_d, t) => (t - clock.t0) / clock.barDur,
    barToCtx: (_d, bar) => clock.t0 + bar * clock.barDur,
    nextPhraseBar: (_d, min) => Math.ceil(((clock.now - clock.t0) / clock.barDur + min) / 16) * 16,
  };
  const store = new ControlStore();
  const sent: { cmd: Command; now: number }[] = [];
  const player = new AutomationPlayer(view, store, (cmd) => sent.push({ cmd, now: clock.now }), { every: () => () => {} });
  const runTo = (t: number, each?: (now: number) => void) => {
    while (clock.now < t) { clock.now += TICK_MS / 1000; player.tick(); each?.(clock.now); }
  };
  const barTime = (b: number) => clock.t0 + (16 + b) * clock.barDur;   // transitions start on bar 16 here
  return { clock, snaps, store, sent, player, runTo, barTime, out, inn };
}

describe("lane math", () => {
  const pts: [number, number][] = [[0, 0], [4, 0.5], [8, 0.5], [8, 1], [16, 0]];
  it("holds, ramps, and steps", () => {
    expect(laneValueAt(pts, -1)).toBe(0);
    expect(laneValueAt(pts, 2)).toBeCloseTo(0.25);
    expect(laneValueAt(pts, 7.99)).toBeCloseTo(0.5);
    expect(laneValueAt(pts, 8)).toBe(1);                 // the step's later value
    expect(laneValueAt(pts, 12)).toBeCloseTo(0.5);
    expect(laneValueAt(pts, 20)).toBe(0);
    expect(laneSteps(pts)).toEqual([{ bar: 8, value: 1 }]);
  });
  it("Camelot distance", () => {
    expect(camelotDistance("8A", "8A")).toBe(0);
    expect(camelotDistance("8A", "8B")).toBe(1);
    expect(camelotDistance("12A", "1A")).toBe(1);
    expect(camelotDistance("8A", "10B")).toBe(3);
    expect(camelotDistance("8A", "?")).toBe(99);
  });
  it("blockers say why a recipe can't run", () => {
    const out = snap({ playing: true }), inn = snap();
    expect(blockers(recipe("bass_swap"), out, inn)).toEqual([]);
    expect(blockers(recipe("bass_swap"), snap(), inn)).toEqual(["the outgoing deck isn't playing"]);
    expect(blockers(recipe("drum_bridge"), out, inn)).toEqual(["it needs stems on the outgoing track", "it needs stems on the incoming track"]);
    expect(blockers(recipe("bass_swap"), out, snap({ camelot: "3B" }))[0]).toMatch(/keys clash/);
    expect(blockers(recipe("bass_swap"), out, snap({ trackBpm: 174 }))[0]).toMatch(/too far apart/);
    expect(blockers(recipe("bass_swap"), out, snap({ beatmatchable: false }))[0]).toMatch(/drifting/);
    expect(blockers(recipe("echo_out"), out, snap({ beatmatchable: false, camelot: "3B", trackBpm: 174 }))).toEqual([]);
  });
});

describe("recipe library (contract C5)", () => {
  it("loads every recipe, all valid", () => {
    expect(RECIPES.map((r) => r.id).sort()).toEqual(["bass_swap", "drum_bridge", "echo_out", "filter_handoff", "loop_roll"]);
  });
  it("rejects a broken recipe with every reason", () => {
    const bad = JSON.parse(JSON.stringify(recipe("bass_swap"))) as Recipe;
    bad.lanes[1]!.points = [[8, 0], [4, 1]];
    bad.lanes.push({ deck: "in", control: "stem.bass", points: [[0, 0.5]] });
    bad.events = bad.events.filter((e) => e.command !== "pause");
    expect(() => assertRecipe(bad)).toThrow(/decrease.*doesn't require stems.*on\/off.*never stops/);
  });
});

describe("composed transitions (contract C6)", () => {
  it("the shared fixture passes, and playable() keeps compiled, valid candidates, best first", () => {
    assertTransitions(transitionsFixture);
    const p = transitionsFixture.pairs[0]!;
    const list = playable(transitionsFixture, p.out_track, p.in_track);
    expect(list.length).toBeGreaterThan(0);
    expect(list.every((c) => c.recipe && c.critic!.valid)).toBe(true);
    expect(list.map((c) => c.critic!.score)).toEqual([...list.map((c) => c.critic!.score)].sort((x, y) => y - x));
    expect(list[0]!.recipe!.anchor!.out_track).toBe(p.out_track);
    expect(playable(transitionsFixture, p.in_track, p.out_track)).toEqual([]);   // ordered pairs
  });
  it("lists Claude's compositions before the rules baseline, each best first", () => {
    const t: unknown = structuredClone(transitionsFixture);
    assertTransitions(t);
    const cs = t.pairs[0]!.candidates.filter((c) => c.recipe && c.critic?.valid);
    const low = { ...structuredClone(cs[cs.length - 1]!), id: "llm_low", source: "llm" as const };
    low.critic = { ...low.critic!, score: 50 };
    t.pairs[0]!.candidates.push(low);
    const list = playable(t, t.pairs[0]!.out_track, t.pairs[0]!.in_track);
    expect(list[0]!.id).toBe("llm_low");
    expect(list.slice(1).map((c) => c.critic!.score)).toEqual([...list.slice(1).map((c) => c.critic!.score)].sort((x, y) => y - x));
  });
  it("rejects a wrong version", () => {
    expect(() => assertTransitions({ ...transitionsFixture, schema_version: 999 })).toThrow(/schema_version 3/);
  });
});

describe("AutomationPlayer", () => {
  it("bass swap A -> B: syncs, starts B on the phrase, steps exactly on bar 8, ramps the crossfader, cleans up", () => {
    const r = rig("A");
    r.store.set("xfader", 0.3, "mouse");                // not where the recipe starts: it must glide
    r.store.set("A.eqLow", 0.5, "mouse");
    const st = r.player.start(recipe("bass_swap"));
    expect(st.state).toBe("armed");
    expect(st.startBar).toBe(16);
    expect(r.sent[0]!.cmd).toEqual({ type: "sync", deck: "B" });
    expect(r.store.get("B.eqLow")).toBe(0);              // incoming deck set up while silent
    expect(r.store.get("B.volume")).toBe(0.8);

    let glideAtHalfBar = NaN, xfAt4 = NaN;
    r.runTo(r.barTime(16) + 1, (now) => {
      if (Math.abs(now - r.barTime(0.5)) < 0.013) glideAtHalfBar = r.store.get("xfader");
      if (Math.abs(now - r.barTime(4)) < 0.013) xfAt4 = r.store.get("xfader");
    });
    const play = r.sent.find((s) => s.cmd.type === "playAt")!;
    expect(play.cmd).toEqual({ type: "playAt", deck: "B", at: r.barTime(0) });
    expect(play.now).toBeLessThanOrEqual(r.barTime(0));
    expect(play.now).toBeGreaterThanOrEqual(r.barTime(0) - LOOKAHEAD_S - 0.03);
    const steps = r.sent.filter((s) => s.cmd.type === "setControlAt").map((s) => s.cmd);
    expect(steps).toEqual([
      { type: "setControlAt", control: "B.eqLow", value: 0.5, at: r.barTime(8) },
      { type: "setControlAt", control: "A.eqLow", value: 0, at: r.barTime(8) },
    ]);
    expect(glideAtHalfBar).toBeCloseTo(0.3 + (0.0625 - 0.3) * 0.5, 2);   // halfway from 0.3 to the lane
    expect(xfAt4).toBeCloseTo(0.5, 2);
    expect(r.store.get("xfader")).toBe(1);
    const pause = r.sent.find((s) => s.cmd.type === "pause")!;
    expect(pause.cmd).toEqual({ type: "pause", deck: "A" });
    expect(pause.now).toBeGreaterThanOrEqual(r.barTime(16));
    expect(r.player.status().state).toBe("done");
    expect(r.store.get("A.eqLow")).toBe(0.5);           // outgoing deck back to neutral
  });

  it("a ramp ends exactly on its last value, whatever the tick phase", () => {
    // Ticks land 1 ms before bar 8, where the incoming filter's ramp ends: the last step to
    // 0.5 is then tiny (2e-5). It used to be skipped as "no change", leaving 0.49998.
    const r = rig("A", { now: 101.024 });
    r.player.start(recipe("filter_handoff"));
    r.runTo(r.barTime(16) + 1);
    expect(r.store.get("B.filter")).toBe(0.5);
  });

  it("B -> A runs the crossfader the other way", () => {
    const r = rig("B");
    r.store.set("xfader", 1, "mouse");
    r.player.start(recipe("bass_swap"));
    r.runTo(r.barTime(16) + 1);
    expect(r.sent.find((s) => s.cmd.type === "playAt")!.cmd).toMatchObject({ deck: "A" });
    expect(r.store.get("xfader")).toBe(0);
  });

  it("a human touching a lane takes it over until the end; the rest carries on", () => {
    const r = rig("A");
    r.player.start(recipe("bass_swap"));
    r.runTo(r.barTime(2));
    r.store.set("B.eqLow", 0.3, "hand");
    r.runTo(r.barTime(16) + 1);
    expect(r.player.status().takenOver).toEqual(["B.eqLow"]);
    expect(r.sent.some((s) => s.cmd.type === "setControlAt" && s.cmd.control === "B.eqLow")).toBe(false);
    expect(r.sent.some((s) => s.cmd.type === "setControlAt" && s.cmd.control === "A.eqLow")).toBe(true);
    expect(r.store.get("B.eqLow")).toBe(0.3);
    expect(r.store.get("xfader")).toBe(1);
  });

  it("holding a control (without moving it) also takes the lane", () => {
    const r = rig("A");
    r.player.start(recipe("filter_handoff"));
    r.runTo(r.barTime(3));
    r.store.setHeld("A.filter", true);
    const before = r.store.get("A.filter");
    r.runTo(r.barTime(16) + 1);
    expect(r.player.status().takenOver).toContain("A.filter");
    expect(r.store.get("A.filter")).toBe(before);
  });

  it("loop events fire on or just after their bar, never before", () => {
    const r = rig("A");
    r.player.start(recipe("loop_roll"));
    r.runTo(r.barTime(17) + 1);
    const loops = r.sent.filter((s) => s.cmd.type === "loop" || s.cmd.type === "loopOff");
    expect(loops.map((s) => s.cmd)).toEqual([
      { type: "loop", deck: "A", beats: 0.5 }, { type: "loop", deck: "A", beats: 0.25 },
      { type: "loop", deck: "A", beats: 0.125 }, { type: "loopOff", deck: "A" },
    ]);
    for (const [s, bar] of loops.map((s, i) => [s, [12, 14, 15, 16][i]!] as const)) {
      expect(s.now).toBeGreaterThanOrEqual(r.barTime(bar));
      expect(s.now).toBeLessThan(r.barTime(bar) + 0.03);
    }
  });

  it("an anchored (composed) recipe waits for its exit bar and starts B from its entry bar", () => {
    const anchored: Recipe = { ...recipe("bass_swap"), anchor: { out_track: "a", in_track: "b", out_start_bar: 32, in_from_bar: 48 } };
    const r = rig("A", { outSnap: { trackId: "a" }, inSnap: { trackId: "b" } });
    expect(r.player.start(anchored).startBar).toBe(32);
    r.runTo(r.clock.t0 + 33 * r.clock.barDur);
    expect(r.player.status().state).toBe("running");
    expect(r.sent.find((s) => s.cmd.type === "playAt")!.cmd).toEqual({ type: "playAt", deck: "B", at: r.clock.t0 + 32 * r.clock.barDur, fromBar: 48 });
  });

  it("an anchored recipe refuses another pair, or a track already past its exit bar", () => {
    const anchored: Recipe = { ...recipe("bass_swap"), anchor: { out_track: "a", in_track: "b", out_start_bar: 32, in_from_bar: 0 } };
    const wrong = rig("A", { outSnap: { trackId: "a" }, inSnap: { trackId: "c" } });
    expect(wrong.player.start(anchored).message).toMatch(/different pair/);
    const late = rig("A", { outSnap: { trackId: "a" }, inSnap: { trackId: "b" }, now: 100 + 31.5 * 2 });
    expect(late.player.start(anchored).message).toMatch(/already past bar 32/);
    const fromB = rig("B", { outSnap: { trackId: "a" }, inSnap: { trackId: "b" } });   // the outgoing track is on deck B
    fromB.player.start(anchored);
    expect(fromB.player.status()).toMatchObject({ state: "armed", out: "B", in: "A" });
  });

  it("refuses with a reason, and stops if the outgoing deck stops", () => {
    const r = rig("A");
    expect(r.player.start(recipe("drum_bridge")).message).toMatch(/needs stems on the outgoing track/);
    expect(r.player.status().state).toBe("error");
    r.player.start(recipe("bass_swap"));
    r.runTo(r.barTime(3));
    r.snaps.A = { ...r.snaps.A, playing: false };
    r.runTo(r.barTime(4));
    expect(r.player.status().state).toBe("stopped");
    expect(r.player.status().message).toMatch(/outgoing deck stopped/);
  });

  it("a tempo ride starts from the deck's own tempo; B syncs after the ride, as it comes in; the brake is exact", () => {
    assertTransitions(rideFixture);
    const ride = rideFixture.pairs[0]!.candidates[0]!.recipe as Recipe;
    const anc = ride.anchor!;
    const r = rig("A", { outSnap: { trackId: anc.out_track, trackBpm: 124, bpm: 124, hasStems: true },
                         inSnap: { trackId: anc.in_track!, trackBpm: 128, bpm: 128, hasStems: true } });
    r.store.set("A.tempo", 0.55, "mouse");             // A is already pitched up a little
    expect(r.player.start(ride).state).toBe("armed");
    expect(r.sent.some((x) => x.cmd.type === "sync")).toBe(false);   // not yet: the ride comes first
    const target = ride.lanes.find((l) => l.control === "tempo")!.points.at(-1)![1];
    const tempoAt: { now: number; v: number }[] = [];
    r.runTo(r.barTime(8) + 1, (now) => tempoAt.push({ now, v: r.store.get("A.tempo") }));
    const at = (bar: number) => tempoAt.find((x) => x.now >= r.barTime(bar))!.v;
    expect(at(-0.5)).toBe(0.55);                       // held, not snapped to the compiled 0.5
    expect(at(2)).toBeCloseTo((0.55 + target) / 2, 2);
    const iSync = r.sent.findIndex((x) => x.cmd.type === "sync");
    const iPlay = r.sent.findIndex((x) => x.cmd.type === "playAt");
    expect(iSync).toBeGreaterThan(-1);
    expect(iPlay).toBe(iSync + 1);
    expect(r.sent[iSync]!.cmd).toEqual({ type: "sync", deck: "B" });
    expect(r.sent[iPlay]!.cmd).toEqual({ type: "playAt", deck: "B", at: r.barTime(4), fromBar: 32 });
    // The ride has landed when B syncs, and isn't pulled back by the last ticks of the ramp.
    expect(tempoAt.filter((x) => x.now >= r.sent[iSync]!.now && x.now <= r.barTime(4.5)).every((x) => x.v === target)).toBe(true);
    const brake = r.sent.find((x) => x.cmd.type === "brakeAt")!;
    expect(brake.cmd).toEqual({ type: "brakeAt", deck: "A", at: r.barTime(7.5), beats: 2 });
    expect(brake.now).toBeLessThanOrEqual(r.barTime(7.5));
    expect(brake.now).toBeGreaterThan(r.barTime(7.5) - LOOKAHEAD_S - TICK_MS / 1000);
    expect(r.player.status().state).toBe("done");
    expect(r.store.get("A.tempo")).toBe(0.5);          // the outgoing deck is reset for its next track
  });

  it("a ride lane keeps the deck's tempo until its first change", () => {
    expect(rideFrom({ deck: "out", control: "tempo", points: [[0, 0.5], [4, 0.7]] }, 0.55).points).toEqual([[0, 0.55], [4, 0.7]]);
    expect(rideFrom({ deck: "out", control: "tempo", points: [[0, 0.5], [2, 0.5], [6, 0.3]] }, 0.6).points)
      .toEqual([[0, 0.6], [2, 0.6], [6, 0.3]]);
  });

  it("blockers judge sync by where the ride leaves the outgoing deck", () => {
    const ride = structuredClone(rideFixture.pairs[0]!.candidates[0]!.recipe) as Recipe;
    ride.anchor = undefined;
    ride.lanes = ride.lanes.map((l) => (l.control === "tempo" ? { ...l, points: [[0, 0.5], [8, 0]] } : l));   // 145 -> 133.4
    const out = snap({ playing: true, trackBpm: 145, bpm: 145, hasStems: true }), inn = snap({ trackBpm: 128, hasStems: true });
    expect(blockers(ride, out, inn)).toEqual([]);
    const noRide = { ...ride, lanes: ride.lanes.filter((l) => l.control !== "tempo") };
    expect(blockers(noRide, out, inn)[0]).toMatch(/too far apart/);
  });

  it("recipes v3: tempo only ramps on the outgoing deck; only the outgoing deck brakes", () => {
    const base = structuredClone(rideFixture.pairs[0]!.candidates[0]!.recipe) as Recipe;
    expect(() => assertRecipe(base)).not.toThrow();
    const step = { ...base, lanes: base.lanes.map((l) => (l.control === "tempo" ? { ...l, points: [[0, 0.5], [4, 0.5], [4, 0.7]] as [number, number][] } : l)) };
    expect(() => assertRecipe(step)).toThrow(/can't step/);
    const inBrake = { ...base, events: [...base.events, { at_bar: 1, deck: "in" as const, command: "brake" as const, beats: 2 }] };
    expect(() => assertRecipe(inBrake)).toThrow(/only the outgoing deck brakes/);
  });
});

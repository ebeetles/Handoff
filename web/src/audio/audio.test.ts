import { describe, expect, it } from "vitest";
import fixture from "../../../contracts/fixtures/sample_analysis.json";
import { assertTrackAnalysis } from "../contracts/track";
import { BeatGrid, loopRange } from "./grid";
import { ctxTimeAt, positionAt, reanchor, type Anchor } from "./transport";
import { alignedLaunchBar, barPhaseDelta, chooseSync, frac } from "./sync";
import { eqDb, filterParams, rateToTempo, tempoRate, xfadeGains } from "./mapping";

const close = (a: number, b: number, eps = 1e-6) => expect(Math.abs(a - b)).toBeLessThan(eps);

describe("contract", () => {
  it("pipeline fixture passes the web-side check", () => {
    expect(() => assertTrackAnalysis(fixture)).not.toThrow();
  });
  it("rejects a wrong schema version", () => {
    expect(() => assertTrackAnalysis({ ...fixture, schema_version: 2 })).toThrow(/schema_version/);
  });
});

describe("BeatGrid", () => {
  const g = new BeatGrid([1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5], 1); // 120 BPM, downbeat at 1.5s
  it("round-trips time <-> beat, inside and outside the beat list", () => {
    for (const t of [0, 0.3, 1, 1.75, 3.2, 4.5, 6.1]) close(g.timeAtBeat(g.beatAt(t)), t);
  });
  it("bars start at the first downbeat", () => {
    close(g.barAt(1.5), 0);
    close(g.barAt(3.5), 1);
    close(g.timeAtBar(2), 5.5); // extrapolated past the list
  });
  it("floor / next / nearest", () => {
    close(g.floorTime(2.2, "beat"), 2);
    close(g.nextTime(2.2, "beat"), 2.5);
    close(g.floorTime(3.4, "bar"), 1.5);
    close(g.nextTime(3.4, "bar"), 3.5);
    close(g.nearestBeatTime(2.3), 2.5);
    close(g.bpmAt(2.2), 120);
  });
  it("works on the real fixture", () => {
    const a = fixture as unknown as { beats: number[]; first_downbeat_index: number; tempo: { bpm: number } };
    const fg = new BeatGrid(a.beats, a.first_downbeat_index);
    close(fg.bpmAt(30), a.tempo.bpm, 0.01);
    close(fg.barAt(fg.timeAtBar(17)), 17);
  });
});

describe("loopRange", () => {
  const g = new BeatGrid([1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5], 1); // 120 BPM, bars start at 1.5, 3.5
  it("bar-length loops start on the bar, 1-3 beats on the beat", () => {
    const four = loopRange(g, 2.7, 4, true);
    close(four.start, 1.5); close(four.end, 3.5);
    const one = loopRange(g, 2.7, 1, true);
    close(one.start, 2.5); close(one.end, 3);
  });
  it("rolls start on their own grid, with the playhead already inside", () => {
    for (const [beats, start] of [[0.5, 2.5], [0.25, 2.625], [0.125, 2.6875], [0.0625, 2.6875]] as const) {
      const r = loopRange(g, 2.7, beats, true);
      close(r.start, start);
      close(r.end - r.start, beats * 0.5);
      expect(r.start <= 2.7 && 2.7 < r.end).toBe(true);
    }
  });
  it("snap off: starts where the playhead is", () => {
    const r = loopRange(g, 2.7, 0.25, false);
    close(r.start, 2.7); close(r.end, 2.825);
  });
});

describe("transport", () => {
  const a: Anchor = { playing: true, ctxTime: 10, pos: 5, rate: 1.04, loop: null };
  it("advances at rate", () => close(positionAt(a, 12), 5 + 2 * 1.04));
  it("re-anchoring on a rate change keeps position continuous", () => {
    const b = reanchor(a, 12, { rate: 0.96 });
    close(positionAt(b, 12), positionAt(a, 12));
    close(positionAt(b, 13), positionAt(a, 12) + 0.96);
  });
  it("wraps inside a loop like AudioBufferSourceNode", () => {
    const l: Anchor = { playing: true, ctxTime: 0, pos: 2, rate: 1, loop: { start: 2, end: 4, beats: 4 } };
    close(positionAt(l, 1), 3);
    close(positionAt(l, 2.5), 2.5);
    close(positionAt(l, 7), 3);
  });
  it("ctxTimeAt inverts positionAt", () => close(positionAt(a, ctxTimeAt(a, 9)), 9));
  it("a pending (future) start holds position and survives a rate change", () => {
    const p: Anchor = { playing: true, ctxTime: 20, pos: 4, rate: 1, loop: null };
    close(positionAt(p, 19.5), 4);
    const q = reanchor(p, 19.5, { rate: 1.05 });
    expect(q.ctxTime).toBe(20);
    close(positionAt(q, 21), 4 + 1.05);
  });
});

describe("sync", () => {
  it("picks the plain ratio when in range", () => {
    const s = chooseSync(124, 128)!;
    expect(s.multiplier).toBe(1);
    close(s.rate, 128 / 124);
  });
  it("uses half/double time across big tempo gaps", () => {
    expect(chooseSync(87, 174)!.multiplier).toBe(0.5); // I play at half their tempo
    expect(chooseSync(174, 87)!.multiplier).toBe(2);
    expect(chooseSync(100, 140)).toBeNull();
  });
  it("phase delta moves the shorter way", () => {
    close(barPhaseDelta(3.1, 7.3, 1), 0.2);
    close(barPhaseDelta(3.9, 7.1, 1), 0.2);   // across the bar line
    close(barPhaseDelta(3.0, 7.75, 1), -0.25);
    close(barPhaseDelta(0, 1.5, 0.5), -0.25); // half-time: my bar = their 2 bars
  });
  it("aligned launch lands on matching phase in the future", () => {
    const target = alignedLaunchBar(10.3, 1, 0.0, 0.05);
    close(target, 11);
    const t2 = alignedLaunchBar(10.3, 1, 0.5, 0.05);
    close(t2, 10.5);
    const t3 = alignedLaunchBar(10.3, 0.5, 0.25, 0.05); // their bars scaled by 1/2
    close(frac(t3 * 0.5), 0.25);
    expect(t3).toBeGreaterThan(10.35);
  });
});

describe("mapping", () => {
  it("eq: flat at center, kill at 0", () => {
    close(eqDb(0.5), 0);
    close(eqDb(0), -40);
    close(eqDb(1), 6);
  });
  it("filter: off at center, sweeps at the ends", () => {
    const c = filterParams(0.5);
    expect(c.lowpassHz).toBeGreaterThan(20000);
    expect(c.highpassHz).toBeLessThan(21);
    close(filterParams(0).lowpassHz, 60, 1e-3);
    close(filterParams(1).highpassHz, 8000, 1e-3);
  });
  it("crossfader is equal-power", () => {
    for (const x of [0, 0.2, 0.5, 0.9, 1]) {
      const { a, b } = xfadeGains(x);
      close(a * a + b * b, 1);
    }
  });
  it("tempo round-trips", () => {
    for (const r of [0.93, 1, 1.032]) close(tempoRate(rateToTempo(r)), r);
  });
});

import syncCases from "../../../contracts/fixtures/sync_cases.json";

describe("shared sync fixture (Python port must match)", () => {
  for (const c of syncCases.cases) {
    it(`${c.my_bpm} -> ${c.other_bpm}`, () => {
      const got = chooseSync(c.my_bpm, c.other_bpm);
      if (c.expect === null) return expect(got).toBeNull();
      expect(got).not.toBeNull();
      expect(got!.multiplier).toBe(c.expect.multiplier);
      close(got!.rate, c.expect.rate, 1e-5);
    });
  }
});

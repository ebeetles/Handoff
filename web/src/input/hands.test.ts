import { describe, expect, it } from "vitest";
import { ControlStore } from "../control/controls";
import type { Command } from "../control/commands";
import { GestureController, TWIST_RANGE_RAD, type PointerSample } from "./gesture";
import { cameraErrorMessage } from "./handAdapter";
import {
  DEFAULT_HAND_TUNING, HandFrameProcessor, assignIds, pinchRatio, pointerPosition, rotationBetween, userHand, type HandFrame, type Pt,
} from "./handTracking";
import { OneEuroFilter } from "./oneEuro";

const VP = { width: 1000, height: 800 };
const ASPECT = 4 / 3;            // 640x480 camera
const FRAME_MS = 1000 / 30;
const DEG = Math.PI / 180;

/** Deterministic Gaussian noise (mulberry32 + Box-Muller). */
function rng(seed: number) {
  let a = seed;
  const u = () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return () => Math.sqrt(-2 * Math.log(u() + 1e-12)) * Math.cos(2 * Math.PI * u());
}

// Schematic hand, palm to the camera, fingers up, as seen on screen (x right, y down), in
// units of hand length (wrist -> middle knuckle = 1). Index 4 (thumb tip) is placed by the
// pinch gap: `gap` to the left of the index tip. `curl` (0..1) bends the index down toward
// the thumb, the way a real pinch does.
const SHAPE: [number, number][] = [
  [0, 1], [-0.3, 0.8], [-0.45, 0.55], [-0.5, 0.3], [0, 0],
  [-0.25, 0.02], [-0.27, -0.33], [-0.28, -0.5], [-0.28, -0.74],
  [0, 0], [0, -0.4], [0, -0.62], [0, -0.8],
  [0.22, 0.04], [0.24, -0.33], [0.25, -0.52], [0.25, -0.68],
  [0.42, 0.12], [0.46, -0.15], [0.48, -0.3], [0.5, -0.42],
];

interface HandOpts {
  gap?: number; curl?: number; rot?: number; noise?: () => number; sigma?: number; label?: string;
  thumb?: [number, number];   // place the thumb tip here (shape units) instead of by `gap`
  squash?: number;            // image foreshortening along the 45 degree diagonal (hand tilt)
}

/** Where the fingertips meet in a pinch (gap 0.1): the point a real twist turns about. */
const PIVOT: [number, number] = [-0.33, -0.74];

/** A hand whose index/middle knuckle midpoint (the cursor anchor) lands at screen (sx, sy)
 *  when unrotated, turned `rot` radians clockwise on screen about PIVOT (like twisting a
 *  knob), with pinch ratio `gap`. Image coords are unmirrored, normalized per axis like
 *  MediaPipe's; world coords are metres. */
function hand(sx: number, sy: number, o: HandOpts = {}): { lm: Pt[]; world: Pt[]; label: string } {
  const gap = o.gap ?? 0.6, curl = o.curl ?? 0, rot = o.rot ?? 0, size = 0.12, metres = 0.09;
  const shape = SHAPE.map(([x, y]) => [x, y] as [number, number]);
  for (const [i, k] of [[6, 0.3], [7, 0.6], [8, 1]] as const) shape[i] = [shape[i]![0] - 0.1 * k * curl, shape[i]![1] + 0.3 * k * curl];
  shape[4] = o.thumb ?? [shape[8]![0] - gap, shape[8]![1]];
  const kx = (shape[5]![0] + shape[9]![0]) / 2, ky = (shape[5]![1] + shape[9]![1]) / 2;
  const [qx, qy] = PIVOT;
  const cos = Math.cos(rot), sin = Math.sin(rot);
  const cx = 1 - (0.1 + (sx / VP.width) * 0.8), cy = 0.1 + (sy / VP.height) * 0.8;   // undo mirror + stretch
  const n = () => (o.noise ? o.noise() * (o.sigma ?? 0) : 0);
  const lm: Pt[] = [], world: Pt[] = [];
  const sq = (o.squash ?? 1) - 1, u = Math.SQRT1_2;
  for (const [x, y] of shape) {
    const dx = cos * (x - qx) - sin * (y - qy) + qx - kx, dy = sin * (x - qx) + cos * (y - qy) + qy - ky;
    const along = sq * (dx * u + dy * u);   // tilt only changes the image, not the metric hand
    const ix = dx + along * u, iy = dy + along * u;
    lm.push({ x: cx - (ix * size) / ASPECT + n(), y: cy + iy * size + n(), z: 0 });
    world.push({ x: dx * metres + n() * 0.75, y: dy * metres + n() * 0.75, z: 0 });
  }
  return { lm, world, label: o.label ?? "Left" };
}

function frame(...hands: { lm: Pt[]; world: Pt[]; label: string }[]): HandFrame {
  return {
    landmarks: hands.map((h) => h.lm),
    worldLandmarks: hands.map((h) => h.world),
    handedness: hands.map((h) => [{ categoryName: h.label, score: 0.9 }]),
  };
}

function fakeEl(left: number, top: number, w: number, h: number): HTMLElement {
  const rect = { left, top, right: left + w, bottom: top + h, width: w, height: h };
  return { getBoundingClientRect: () => rect, dataset: {} } as unknown as HTMLElement;
}

function rig() {
  const store = new ControlStore();
  const cmds: Command[] = [];
  const gestures = new GestureController(store, (c) => cmds.push(c));
  const knob = fakeEl(450, 350, 100, 100);               // twist knob; mouse travel 160 px
  gestures.register(knob, { kind: "continuous", control: "A.filter", axis: "y", twist: true });
  const fader = fakeEl(100, 300, 60, 300);               // travel 300 px
  gestures.register(fader, { kind: "continuous", control: "B.volume", axis: "y" });
  const pad = fakeEl(700, 100, 80, 60);
  gestures.register(pad, { kind: "pad", command: { type: "togglePlay", deck: "A" } });
  const proc = new HandFrameProcessor(gestures);
  let t = 0;
  const step = (f: HandFrame) => { t += FRAME_MS; proc.process(f, t, VP, ASPECT); };
  const hold = (n: number, f: () => HandFrame) => { for (let i = 0; i < n; i++) step(f()); };
  return { store, gestures, knob, fader, pad, proc, step, hold, cmds };
}

describe("OneEuroFilter", () => {
  it("smooths a still, jittery signal", () => {
    const f = new OneEuroFilter(), g = rng(1);
    const out: number[] = [];
    for (let i = 0; i < 300; i++) out.push(f.filter(500 + 3 * g(), i * FRAME_MS));
    const tail = out.slice(60);
    const mean = tail.reduce((a, b) => a + b, 0) / tail.length;
    const sd = Math.sqrt(tail.reduce((a, b) => a + (b - mean) ** 2, 0) / tail.length);
    // Input sd 3 px. A 1 Hz one-pole at 30 fps passes sd * sqrt(a / (2 - a)), a = 0.173:
    // 0.92 px. The speed-dependent cutoff must not make it noisier than that.
    expect(sd).toBeLessThan(0.95);
  });
  it("lags far less than a fixed 1 Hz low-pass when moving fast", () => {
    const adaptive = new OneEuroFilter(), fixed = new OneEuroFilter({ minCutoff: 1, beta: 0, dCutoff: 1 });
    let a = 0, b = 0, x = 0;
    for (let i = 0; i < 60; i++) { x = i * FRAME_MS; a = adaptive.filter(x, i * FRAME_MS); b = fixed.filter(x, i * FRAME_MS); } // 1000 px/s
    expect(x - a).toBeLessThan(25);
    expect(x - b).toBeGreaterThan(100);
  });
});

describe("hand geometry", () => {
  it("pinch ratio is the thumb-tip to index-tip gap over hand length", () => {
    expect(pinchRatio(hand(500, 400, { gap: 0.1 }).world)).toBeCloseTo(0.1, 6);
    expect(pinchRatio(hand(500, 400, { gap: 0.6, rot: 40 * DEG }).world)).toBeCloseTo(0.6, 6);
  });
  it("pinch sweet spot: sloppy and pad pinches press; a relaxed thumb at the joint doesn't", () => {
    const presses = (o: HandOpts, down?: number) => {
      const r = rig();
      if (down !== undefined) r.proc.setPinchDown(down);
      r.hold(5, () => frame(hand(740, 130)));
      r.hold(3, () => frame(hand(740, 130, o)));
      return r.cmds.length === 1;
    };
    const joint: [number, number] = [-0.28 - 0.044, -0.5];    // thumb 4 mm from the index's last joint
    expect(pinchRatio(hand(0, 0, { thumb: joint }).world)).toBeLessThan(DEFAULT_HAND_TUNING.pinchDown);  // tips alone can't tell
    expect(presses({ thumb: joint })).toBe(false);             // relaxed hand
    expect(presses({ gap: 0.12 })).toBe(true);                 // tips touching
    expect(presses({ gap: 0.23 })).toBe(true);                 // sloppy pinch (v0.3's 0.20 missed it)
    expect(presses({ thumb: [-0.28 - 0.12, -0.62] })).toBe(true);   // pad to pad, mid-segment
    expect(presses({ gap: 0.28 })).toBe(false);                // apart
    expect(presses({ gap: 0.28 }, 0.3)).toBe(true);            // ... unless the slider says easy
    expect(presses({ gap: 0.2 }, 0.1)).toBe(false);            // clamped to 0.18 at the strict end
  });
  it("mirrors x and stretches the active region to the viewport", () => {
    const at = (x: number, y: number) => pointerPosition(Array.from({ length: 21 }, () => ({ x, y, z: 0 })), VP, 0.8);
    const near = (p: { x: number; y: number }, x: number, y: number) => {
      expect(p.x).toBeCloseTo(x, 9);
      expect(p.y).toBeCloseTo(y, 9);
    };
    near(at(0.5, 0.5), 500, 400);
    near(at(0.9, 0.1), 0, 0);          // camera-right = user's left = screen left
    near(at(0.05, 0.97), 1000, 800);   // outside the region clamps
  });
  it("palm rotation: exact, and blind to translation and scale", () => {
    const ref = [{ x: 0, y: 1 }, { x: -0.25, y: 0 }, { x: 0, y: 0 }, { x: 0.22, y: 0 }, { x: 0.42, y: 0.1 }];
    const turn = (a: number, s: number, tx: number) => ref.map((p) => ({
      x: s * (Math.cos(a) * p.x - Math.sin(a) * p.y) + tx, y: s * (Math.sin(a) * p.x + Math.cos(a) * p.y) - tx,
    }));
    expect(rotationBetween(ref, turn(30 * DEG, 1, 0))).toBeCloseTo(30 * DEG, 9);
    expect(rotationBetween(ref, turn(-50 * DEG, 0.6, 3))).toBeCloseTo(-50 * DEG, 9);
  });
  it("swaps MediaPipe's handedness for an unmirrored frame", () => {
    expect(userHand("Left")).toBe("hand-right");
    expect(userHand("Right")).toBe("hand-left");
    expect(userHand(undefined)).toBeNull();
  });
});

describe("assignIds", () => {
  const prev = new Map([["hand-left", { x: 200, y: 400 }], ["hand-right", { x: 800, y: 400 }]] as const);
  it("keeps ids by position even when labels flip", () => {
    expect(assignIds([{ x: 790, y: 410, label: "hand-left" }, { x: 210, y: 390, label: "hand-left" }], prev, 250))
      .toEqual(["hand-right", "hand-left"]);
  });
  it("uses the label for a new hand, and never gives two hands one id", () => {
    expect(assignIds([{ x: 500, y: 400, label: "hand-right" }], new Map(), 250)).toEqual(["hand-right"]);
    expect(assignIds([{ x: 100, y: 0, label: "hand-left" }, { x: 900, y: 0, label: "hand-left" }], new Map(), 250))
      .toEqual(["hand-left", "hand-right"]);
  });
});

describe("cursor and hit-testing", () => {
  it("pinching doesn't move the cursor (the fingers move, the knuckles don't)", () => {
    const { gestures, hold, step } = rig();
    const seen: { x: number; y: number }[] = [];
    hold(10, () => frame(hand(500, 400)));
    for (let i = 0; i <= 10; i++) {                                       // close the pinch over 1/3 s
      step(frame(hand(500, 400, { gap: 0.6 - 0.05 * i, curl: i / 10 })));
      const p = gestures.pointers.get("hand-right")!;
      seen.push({ x: p.x, y: p.y });
    }
    expect(gestures.pointers.get("hand-right")!.down).toBe(true);
    for (const p of seen) {
      expect(p.x).toBeCloseTo(500, 6);
      expect(p.y).toBeCloseTo(400, 6);
    }
    // What the old fingertip-midpoint cursor did over the same pinch:
    const tipMid = (h: { lm: Pt[] }) => {
      const t = h.lm[4]!, i = h.lm[8]!, m = { x: (t.x + i.x) / 2, y: (t.y + i.y) / 2, z: 0 };
      return pointerPosition(Array.from({ length: 21 }, () => m), VP, 0.8);
    };
    const a = tipMid(hand(500, 400)), b = tipMid(hand(500, 400, { gap: 0.1, curl: 1 }));
    expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(15);
  });
  it("twisting a knob doesn't move the cursor (the knuckles swing, the correction cancels it)", () => {
    const { store, gestures, knob, hold, step } = rig();
    const cur = () => { const p = gestures.pointers.get("hand-right")!; return { x: p.x, y: p.y }; };
    hold(10, () => frame(hand(500, 400)));
    hold(5, () => frame(hand(500, 400, { gap: 0.1 })));
    const c0 = cur();
    let worst = 0;
    for (let i = 1; i <= 30; i++) {                                      // 60 degrees in 1 s
      step(frame(hand(500, 400, { gap: 0.1, rot: (60 * DEG * i) / 30 })));
      worst = Math.max(worst, Math.hypot(cur().x - c0.x, cur().y - c0.y));
    }
    hold(30, () => frame(hand(500, 400, { gap: 0.1, rot: 60 * DEG })));
    const knuckles = pointerPosition(hand(500, 400, { gap: 0.1, rot: 60 * DEG }).lm, VP, 0.8);
    expect(Math.hypot(knuckles.x - c0.x, knuckles.y - c0.y)).toBeGreaterThan(60);   // what the cursor used to do
    expect(worst).toBeLessThan(6);                                       // filter lag only
    expect(Math.hypot(cur().x - c0.x, cur().y - c0.y)).toBeLessThan(1);
    expect(store.get("A.filter")).toBeCloseTo(0.5 + (60 * DEG) / TWIST_RANGE_RAD, 2);
    expect(knob.dataset.grabbed).toBe("true");
  });
  it("a pinch that flickers open mid-twist keeps the knob (no reset, no re-press)", () => {
    const { store, knob, fader, hold, step, proc } = rig();
    hold(10, () => frame(hand(500, 400)));
    hold(5, () => frame(hand(500, 400, { gap: 0.1 })));
    for (let i = 1; i <= 15; i++) step(frame(hand(500, 400, { gap: 0.1, rot: (40 * DEG * i) / 15 })));
    hold(2, () => frame(hand(500, 400, { gap: 0.4, rot: 40 * DEG })));   // flicker open (67 ms)
    expect(knob.dataset.grabbed).toBe("true");
    hold(2, () => frame(hand(500, 400, { gap: 0.1, rot: 40 * DEG })));   // and closed again
    expect(knob.dataset.grabbed).toBe("true");
    expect(proc.stats.pinchFlickers).toBe(1);
    for (let i = 1; i <= 15; i++) step(frame(hand(500, 400, { gap: 0.1, rot: 40 * DEG + (20 * DEG * i) / 15 })));
    hold(30, () => frame(hand(500, 400, { gap: 0.1, rot: 60 * DEG })));
    expect(store.get("A.filter")).toBeCloseTo(0.5 + (60 * DEG) / TWIST_RANGE_RAD, 2);
    expect(fader.dataset.grabbed).toBeUndefined();
  });
  it("a flicker on a pad presses it once; a quick double pinch on a knob doesn't reset it (hands)", () => {
    const { cmds, hold } = rig();
    hold(5, () => frame(hand(740, 130)));
    hold(3, () => frame(hand(740, 130, { gap: 0.1 })));
    hold(2, () => frame(hand(740, 130, { gap: 0.5 })));                 // 67 ms blip
    hold(3, () => frame(hand(740, 130, { gap: 0.1 })));
    expect(cmds).toHaveLength(1);
    const r = rig();
    r.store.set("A.filter", 0.9, "mouse");
    r.hold(5, () => frame(hand(500, 400)));
    r.hold(2, () => frame(hand(500, 400, { gap: 0.1 })));
    r.hold(5, () => frame(hand(500, 400, { gap: 0.6 })));               // open 167 ms: a real release
    r.hold(2, () => frame(hand(500, 400, { gap: 0.1 })));
    expect(r.store.get("A.filter")).toBe(0.9);
  });
  it("after letting go, untwisting doesn't move the cursor, holding still doesn't creep, travelling bleeds the correction off", () => {
    const { gestures, hold, step } = rig();
    const cur = () => { const p = gestures.pointers.get("hand-right")!; return { x: p.x, y: p.y }; };
    hold(10, () => frame(hand(500, 400)));
    hold(5, () => frame(hand(500, 400, { gap: 0.1 })));
    const c0 = cur();
    for (let i = 1; i <= 30; i++) step(frame(hand(500, 400, { gap: 0.1, rot: (60 * DEG * i) / 30 })));
    hold(5, () => frame(hand(500, 400, { gap: 0.6, rot: 60 * DEG })));   // let go, still turned
    for (let i = 1; i <= 30; i++) step(frame(hand(500, 400, { gap: 0.6, rot: 60 * DEG - (60 * DEG * i) / 30 })));
    hold(30, () => frame(hand(500, 400, { gap: 0.6 })));
    expect(Math.hypot(cur().x - c0.x, cur().y - c0.y)).toBeLessThan(2);

    // Turn again, let go, and hold the open hand still (jittery) for 10 s: no creep.
    for (let i = 1; i <= 30; i++) step(frame(hand(500, 400, { gap: 0.1, rot: (60 * DEG * i) / 30 })));
    hold(5, () => frame(hand(500, 400, { gap: 0.6, rot: 60 * DEG })));
    const g = rng(3);
    hold(30, () => frame(hand(500, 400, { gap: 0.6, rot: 60 * DEG, noise: g, sigma: 0.0015 })));
    const c1 = cur();
    hold(300, () => frame(hand(500, 400, { gap: 0.6, rot: 60 * DEG, noise: g, sigma: 0.0015 })));
    expect(Math.hypot(cur().x - c1.x, cur().y - c1.y)).toBeLessThan(3);

    // Travel 400 px with the open hand: most of the correction bleeds off.
    const knuckles = (x: number) => pointerPosition(hand(x, 400, { gap: 0.6, rot: 60 * DEG }).lm, VP, 0.8);
    const offset = (x: number) => Math.hypot(cur().x - knuckles(x).x, cur().y - knuckles(x).y);
    const before = offset(500);
    expect(before).toBeGreaterThan(40);
    for (let i = 1; i <= 30; i++) step(frame(hand(500 - (400 * i) / 30, 400, { gap: 0.6, rot: 60 * DEG })));
    hold(30, () => frame(hand(100, 400, { gap: 0.6, rot: 60 * DEG })));
    expect(offset(100)).toBeLessThan(0.3 * before);
  });
  it("turning the hand while sliding a fader doesn't move the fader", () => {
    const { store, hold, step } = rig();
    hold(10, () => frame(hand(130, 450)));
    hold(5, () => frame(hand(130, 450, { gap: 0.1 })));
    const v0 = store.get("B.volume");
    for (let i = 1; i <= 30; i++) step(frame(hand(130, 450, { gap: 0.1, rot: (40 * DEG * i) / 30 })));
    hold(30, () => frame(hand(130, 450, { gap: 0.1, rot: 40 * DEG })));
    expect(Math.abs(store.get("B.volume") - v0)).toBeLessThan(0.01);
  });
  it("buttons and sliders are exact: a pinch beside them grabs nothing", () => {
    const { pad, fader, hold, cmds } = rig();
    hold(5, () => frame(hand(740, 130)));
    hold(3, () => frame(hand(740, 130, { gap: 0.1 })));
    expect(cmds).toEqual([{ type: "togglePlay", deck: "A" }]);
    expect(pad.dataset.pressed).toBe("true");
    const r = rig();
    r.hold(5, () => frame(hand(690, 130)));                                // 10 px left of the pad
    r.hold(3, () => frame(hand(690, 130, { gap: 0.1 })));
    r.hold(5, () => frame(hand(170, 450)));                                // 10 px right of the fader
    r.hold(3, () => frame(hand(170, 450, { gap: 0.1 })));
    expect(r.cmds).toEqual([]);
    expect(fader.dataset.grabbed).toBeUndefined();
    expect(r.fader.dataset.grabbed).toBeUndefined();
  });
  it("seek is clamped to the strip", () => {
    const store = new ControlStore();
    const cmds: Command[] = [];
    const g = new GestureController(store, (c) => cmds.push(c));
    g.register(fakeEl(100, 100, 400, 30), { kind: "seek", deck: "A" });
    const m = (x: number, down: boolean): PointerSample => ({ id: "mouse-1", x, y: 115, down, source: "mouse" });
    g.pointer(m(200, true));
    g.pointer(m(50, true));                                                // dragged off the left end
    expect(cmds).toEqual([
      { type: "seekFraction", deck: "A", fraction: 0.25 },
      { type: "seekFraction", deck: "A", fraction: 0 },
    ]);
  });
});

describe("knob lock-on (hands only, knobs only)", () => {
  const ptr = (x: number, y: number, down = false, source: PointerSample["source"] = "hand"): PointerSample =>
    ({ id: source === "hand" ? "hand-right" : "mouse-1", x, y, down, source, angle: 0 });

  it("a hand near a knob locks to it: cursor on the knob's center, pinch grabs it", () => {
    const { gestures, knob } = rig();
    gestures.pointer(ptr(420, 400));                     // 30 px left of the knob
    expect(knob.dataset.locked).toBe("true");
    expect(gestures.cursor("hand-right")).toEqual({ x: 500, y: 400, locked: true });
    gestures.pointer(ptr(420, 400, true));
    expect(knob.dataset.grabbed).toBe("true");
    gestures.pointer({ ...ptr(380, 360, true), angle: 0.4 });   // hand drifts while twisting
    expect(gestures.cursor("hand-right")).toEqual({ x: 500, y: 400, locked: true });
  });
  it("too far, or the mouse: no lock", () => {
    const { gestures, knob } = rig();
    gestures.pointer(ptr(400, 400));                     // 50 px away
    expect(knob.dataset.locked).toBeUndefined();
    gestures.pointer(ptr(440, 400, false, "mouse"));
    gestures.pointer(ptr(440, 400, true, "mouse"));
    expect(knob.dataset.grabbed).toBeUndefined();
  });
  it("being inside a button or slider beats a knob's pull", () => {
    const store = new ControlStore();
    const g = new GestureController(store, () => {});
    const knob = fakeEl(300, 300, 70, 90), fader = fakeEl(380, 260, 44, 300);   // 10 px apart
    g.register(knob, { kind: "continuous", control: "A.eqLow", axis: "y", twist: true });
    g.register(fader, { kind: "continuous", control: "A.volume", axis: "y" });
    g.pointer(ptr(375, 340));                            // in the gap: knob
    expect(knob.dataset.locked).toBe("true");
    g.pointer(ptr(385, 340));                            // inside the fader
    expect(knob.dataset.locked).toBeUndefined();
    g.pointer(ptr(385, 340, true));
    expect(fader.dataset.grabbed).toBe("true");
  });
  it("stacked knobs: the nearest wins, with a little hysteresis", () => {
    const store = new ControlStore();
    const g = new GestureController(store, () => {});
    const hi = fakeEl(300, 300, 70, 90), mid = fakeEl(300, 390, 70, 90);       // touching at y = 390
    g.register(hi, { kind: "continuous", control: "A.eqHigh", axis: "y", twist: true });
    g.register(mid, { kind: "continuous", control: "A.eqMid", axis: "y", twist: true });
    g.pointer(ptr(390, 370));                            // 20 px right of both, beside hi
    expect(hi.dataset.locked).toBe("true");
    g.pointer(ptr(390, 395));                            // both 20 px away now: keep hi
    expect(hi.dataset.locked).toBe("true");
    g.pointer(ptr(335, 410));                            // inside mid, 20 px from hi
    expect(mid.dataset.locked).toBe("true");
    expect(hi.dataset.locked).toBeUndefined();
  });
  it("hands never seek; the mouse does", () => {
    const store = new ControlStore();
    const cmds: Command[] = [];
    const g = new GestureController(store, (c) => cmds.push(c));
    g.register(fakeEl(100, 100, 400, 30), { kind: "seek", deck: "A" });
    g.pointer(ptr(200, 115)); g.pointer(ptr(200, 115, true));
    expect(cmds).toEqual([]);
    g.pointer(ptr(200, 115, false, "mouse")); g.pointer(ptr(200, 115, true, "mouse"));
    expect(cmds).toEqual([{ type: "seekFraction", deck: "A", fraction: 0.25 }]);
  });
  it("unmounting a locked knob drops the lock", () => {
    const store = new ControlStore();
    const g = new GestureController(store, () => {});
    const k = fakeEl(300, 300, 70, 90);
    const off = g.register(k, { kind: "continuous", control: "A.eqLow", axis: "y", twist: true });
    g.pointer(ptr(320, 320));
    expect(k.dataset.locked).toBe("true");
    off();
    expect(k.dataset.locked).toBeUndefined();
    expect(g.cursor("hand-right")?.locked).toBe(false);
  });
});

describe("twisting with lock-on", () => {
  const twist = (r: ReturnType<typeof rig>, from: number, to: number, frames = 20, o: HandOpts = {}) => {
    for (let i = 1; i <= frames; i++) r.step(frame(hand(500, 400, { gap: 0.1, ...o, rot: from + ((to - from) * i) / frames })));
    r.hold(45, () => frame(hand(500, 400, { gap: 0.1, ...o, rot: to })));
  };
  it("re-grabbing quickly to keep turning never resets the knob (no double-pinch for hands)", () => {
    const r = rig();
    r.hold(10, () => frame(hand(500, 400)));
    r.hold(3, () => frame(hand(500, 400, { gap: 0.1 })));
    twist(r, 0, 30 * DEG, 10, {});
    const after1 = r.store.get("A.filter");
    r.hold(5, () => frame(hand(500, 400, { gap: 0.6, rot: 30 * DEG })));   // let go 167 ms
    for (let i = 1; i <= 5; i++) r.step(frame(hand(500, 400, { gap: 0.6, rot: 30 * DEG - (30 * DEG * i) / 5 })));  // wind back
    r.hold(2, () => frame(hand(500, 400, { gap: 0.1 })));                // re-grab, < 350 ms after the first
    twist(r, 0, 30 * DEG, 10, {});
    expect(after1).toBeCloseTo(0.5 + (30 * DEG) / TWIST_RANGE_RAD, 2);
    expect(r.store.get("A.filter")).toBeCloseTo(0.5 + (60 * DEG) / TWIST_RANGE_RAD, 2);
  });
  it("turning back from past an end responds at once", () => {
    const r = rig();
    r.hold(10, () => frame(hand(500, 400)));
    r.hold(3, () => frame(hand(500, 400, { gap: 0.1 })));
    twist(r, 0, 110 * DEG);                                              // 0.5 + 110/150 > 1
    expect(r.store.get("A.filter")).toBe(1);
    twist(r, 110 * DEG, 95 * DEG);                                       // back 15 degrees
    expect(r.store.get("A.filter")).toBeCloseTo(1 - (15 * DEG) / TWIST_RANGE_RAD, 2);
  });
  it("a fader dragged past its end responds at once on the way back", () => {
    const { store, gestures } = rig();
    const m = (y: number, down: boolean): PointerSample => ({ id: "mouse-1", x: 130, y, down, source: "mouse" });
    gestures.pointer(m(450, true));
    gestures.pointer(m(300, true));                                      // 150 px up: 0.8 + 0.5 -> 1
    gestures.pointer(m(330, true));                                      // 30 px back down
    expect(store.get("B.volume")).toBeCloseTo(1 - 30 / 300, 6);
  });
  it("a hand that tilted since it appeared still twists true (reference taken at the pinch)", () => {
    const r = rig();
    r.hold(10, () => frame(hand(500, 400, { squash: 0.6 })));            // first seen tilted
    r.hold(10, () => frame(hand(500, 400)));                             // straightens up
    r.hold(3, () => frame(hand(500, 400, { gap: 0.1 })));
    const v0 = r.store.get("A.filter");
    for (const deg of [15, 30, 45, 60]) {
      twist(r, (deg - 15) * DEG, deg * DEG, 8);
      expect(r.store.get("A.filter") - v0).toBeCloseTo((deg * DEG) / TWIST_RANGE_RAD, 2);   // linear
    }
  });
});

describe("HandFrameProcessor -> GestureController", () => {
  it("pinch grabs a fader, a vertical move slides it, opening releases it", () => {
    const { store, fader, hold, step } = rig();
    hold(5, () => frame(hand(130, 450)));
    hold(5, () => frame(hand(130, 450, { gap: 0.1 })));
    expect(fader.dataset.grabbed).toBe("true");
    for (let i = 1; i <= 30; i++) step(frame(hand(130, 450 - (60 * i) / 30, { gap: 0.1 })));   // up 60 px in 1 s
    hold(30, () => frame(hand(130, 390, { gap: 0.1 })));
    expect(store.get("B.volume")).toBeCloseTo(0.8 + 60 / 300, 2);
    step(frame(hand(130, 390, { gap: 0.28 })));                                          // inside hysteresis
    expect(fader.dataset.grabbed).toBe("true");
    hold(3, () => frame(hand(130, 390, { gap: 0.6 })));                                 // open < 100 ms: pending
    expect(fader.dataset.grabbed).toBe("true");
    step(frame(hand(130, 390, { gap: 0.6 })));
    expect(fader.dataset.grabbed).toBeUndefined();
  });

  it("twisting a pinched knob turns it; moving the hand doesn't", () => {
    const { store, knob, hold, step } = rig();
    hold(10, () => frame(hand(500, 400)));
    hold(5, () => frame(hand(500, 400, { gap: 0.1 })));
    expect(knob.dataset.grabbed).toBe("true");
    for (let i = 1; i <= 30; i++) step(frame(hand(500, 400, { gap: 0.1, rot: (45 * DEG * i) / 30 })));
    hold(45, () => frame(hand(500, 400, { gap: 0.1, rot: 45 * DEG })));
    const turned = store.get("A.filter");
    expect(turned).toBeCloseTo(0.5 + (45 * DEG) / TWIST_RANGE_RAD, 2);   // 0.8
    for (let i = 1; i <= 30; i++) step(frame(hand(500, 400 - 2 * i, { gap: 0.1, rot: 45 * DEG })));
    hold(30, () => frame(hand(500, 340, { gap: 0.1, rot: 45 * DEG })));
    expect(Math.abs(store.get("A.filter") - turned)).toBeLessThan(0.005);
    for (let i = 1; i <= 20; i++) step(frame(hand(500, 340, { gap: 0.1, rot: 45 * DEG - (90 * DEG * i) / 20 })));
    hold(45, () => frame(hand(500, 340, { gap: 0.1, rot: -45 * DEG })));
    expect(store.get("A.filter")).toBeCloseTo(0.5 - (45 * DEG) / TWIST_RANGE_RAD, 2);  // counter-clockwise
  });

  it("the mouse still drags a twist knob vertically", () => {
    const { store, gestures } = rig();
    const m = (y: number, down: boolean): PointerSample => ({ id: "mouse-1", x: 500, y, down, source: "mouse" });
    gestures.pointer(m(400, true));
    gestures.pointer(m(360, true));
    gestures.pointer(m(360, false));
    expect(store.get("A.filter")).toBeCloseTo(0.5 + 40 / 160, 6);
  });

  it("survives a short tracking dropout, releases after the grace period", () => {
    const { knob, hold, step, proc } = rig();
    hold(5, () => frame(hand(500, 400, { gap: 0.1 })));
    hold(6, () => frame());                                                // 200 ms gap
    step(frame(hand(500, 400, { gap: 0.1 })));
    expect(knob.dataset.grabbed).toBe("true");
    expect(proc.stats.droppedGrabs).toBe(0);
    hold(9, () => frame());                                                // 300 ms gap
    expect(knob.dataset.grabbed).toBeUndefined();
    expect(proc.stats.droppedGrabs).toBe(1);
  });

  it("holding still drifts < 1%/s under landmark jitter (simulated): fader and twist knob", () => {
    for (const [x, y, control] of [[130, 450, "B.volume"], [500, 400, "A.filter"]] as const) {
      const { store, hold, step } = rig();
      const g = rng(7);
      // sigma 0.0015 of the frame is ~1.9 px of pointer jitter per landmark on this viewport.
      const still = () => frame(hand(x, y, { gap: 0.1, noise: g, sigma: 0.0015 }));
      hold(30, still);
      const v0 = store.get(control);
      let worst = 0;
      for (let i = 0; i < 300; i++) {                                      // 10 s
        step(still());
        worst = Math.max(worst, Math.abs(store.get(control) - v0));
      }
      expect(Math.abs(store.get(control) - v0) / 10, control).toBeLessThan(0.01);
      expect(worst, control).toBeLessThan(0.01);                          // never wanders > 1%
    }
  });

  it("two hands hold two controls at once, ids stable when labels flip", () => {
    const { store, knob, fader, hold, step } = rig();
    const pair = (rot: number, fy: number, lk = "Left", lf = "Right") =>
      frame(hand(500, 400, { gap: 0.1, rot, label: lk }), hand(130, fy, { gap: 0.1, label: lf }));
    hold(5, () => pair(0, 450));
    expect(knob.dataset.grabbed).toBe("true");
    expect(fader.dataset.grabbed).toBe("true");
    for (let i = 1; i <= 30; i++) step(pair(i * DEG, 450 + i, i % 2 ? "Left" : "Right", "Left"));  // labels flicker
    hold(30, () => pair(30 * DEG, 480));
    expect(store.get("A.filter")).toBeGreaterThan(0.6);
    expect(store.get("B.volume")).toBeLessThan(0.75);
    expect(knob.dataset.grabbed).toBe("true");
    expect(fader.dataset.grabbed).toBe("true");
  });

  it("the mouse keeps working while a hand holds a control", () => {
    const { store, gestures, fader, hold } = rig();
    hold(5, () => frame(hand(500, 400, { gap: 0.1 })));
    const m = (y: number, down: boolean): PointerSample => ({ id: "mouse-1", x: 130, y, down, source: "mouse" });
    gestures.pointer(m(450, true));
    gestures.pointer(m(410, true));
    gestures.pointer(m(410, false));
    expect(store.get("B.volume")).toBeCloseTo(0.8 + 40 / 300, 6);
    expect(fader.dataset.grabbed).toBeUndefined();
  });

  it("releaseAll lets go of everything", () => {
    const { knob, hold, proc } = rig();
    hold(5, () => frame(hand(500, 400, { gap: 0.1 })));
    proc.releaseAll();
    expect(knob.dataset.grabbed).toBeUndefined();
  });
});

describe("camera errors", () => {
  it("tell the user what to do", () => {
    expect(cameraErrorMessage(new DOMException("x", "NotAllowedError"))).toMatch(/Allow the camera/);
    expect(cameraErrorMessage(new DOMException("x", "NotFoundError"))).toMatch(/No camera found/);
    expect(cameraErrorMessage(new DOMException("x", "NotReadableError"))).toMatch(/busy/);
    expect(cameraErrorMessage(new Error("boom"))).toMatch(/failed to start: boom/);
  });
});

// Hand landmarks -> PointerSamples. Pure (no camera, no DOM, no MediaPipe runtime), so the
// whole path from landmarks to GestureController is unit-testable with synthetic hands.
// handAdapter.ts owns the camera and the model and feeds frames in here.
//
// Per hand, per frame:
//   pointer = midpoint of the index and middle knuckles (5, 9), x mirrored for the selfie view,
//             with the central `activeRegion` of the frame stretched to the whole viewport
//             (so you never have to reach the frame edge), then One Euro filtered in px.
//             Knuckles, not fingertips: pinching curls the index and lifts the thumb, which
//             moved a fingertip cursor at the moment of the click (and of the release). The
//             knuckles don't move when the fingers do.
//   cursor  = pointer + a rotation correction. Twisting a knob turns the hand about the
//             pinch point, which swings the knuckles (and so the cursor) around it. We keep
//             d = knuckles - pinch point (captured at each pinch start, rotated with the
//             palm angle) and subtract the knuckle motion that rotation explains:
//             offset -= R(angle - angleRef) d - R(prevAngle - angleRef) d. This telescopes,
//             so noise can't accumulate. The correction persists after release (a pinch
//             that flickers mid-twist must re-press where it was, and untwisting must not
//             move the cursor). It bleeds off only while the open hand travels across the
//             screen (past a jitter deadband), so the cursor never creeps on its own.
//   pinch   = |p4 - p8| / |p0 - p9|: thumb tip to index tip, over the wrist-to-middle-knuckle
//             length (so it doesn't depend on distance from the camera). Measured on the 3D
//             metric world landmarks: a tilted palm shortens the 2D hand length and would
//             fake a pinch. Hysteresis: down below pinchDown, up above pinchUp; the gap keeps
//             a grab through a twist, which distorts the fingers' landmarks.
//             A press also needs the thumb at the fingertip, not resting back at the index's
//             last joint (7): tip ratio - joint ratio < jointMargin. A relaxed thumb against
//             that joint reads ~0.24 tip-to-tip, the same as a sloppy real pinch, so no single
//             threshold separates them (v0.2 at 0.30 pressed on relaxed hands; v0.3 at 0.20
//             missed real pinches). The release ignores it, so a twist can't drop a grab.
//   angle   = in-plane rotation of the palm (landmarks 0, 5, 9, 13, 17; the fingers don't
//             move it): the least-squares 2D rotation from a reference palm shape, in screen
//             orientation (mirrored, aspect-corrected), unwrapped and One Euro filtered.
//             The reference is re-taken at every pinch, so a twist is measured against the
//             hand as it was when it grabbed. Against an old reference, a change in the
//             hand's tilt since then read as rotation and bent the twist.
//   id      = "hand-left" / "hand-right", kept stable by matching each hand to where a known
//             hand was last frame. MediaPipe's handedness label is only used for a new hand:
//             it flickers, and it sometimes gives both hands the same label.
// A hand that disappears keeps its grab for graceMs (one or two missed frames are common)
// before GestureController.lost() releases it. Likewise a pinch must stay open for
// releaseHoldMs before it counts as a release: twisting briefly opens it, and a release +
// re-press would fire a pad twice or trigger the double-press reset. While a release is
// pending the hand sends nothing, so an opening hand can't drag what it held.
import type { PointerSample } from "./gesture";
import { DEFAULT_ONE_EURO, OneEuroFilter, type OneEuroParams } from "./oneEuro";

export interface Pt { x: number; y: number; z: number }
/** Structural subset of MediaPipe's HandLandmarkerResult. */
export interface HandFrame {
  landmarks: Pt[][];
  worldLandmarks: Pt[][];
  handedness: { categoryName: string; score: number }[][];
}
export interface Viewport { width: number; height: number }
export interface PointerSink { pointer(p: PointerSample): void; lost(id: string): void }

export const LM = { WRIST: 0, THUMB_TIP: 4, INDEX_MCP: 5, INDEX_DIP: 7, INDEX_TIP: 8, MIDDLE_MCP: 9 } as const;
/** Pinch sensitivity range offered in the UI (grab threshold); release is always +0.1. */
export const PINCH_DOWN_RANGE = { min: 0.18, max: 0.32 } as const;
const PALM = [0, 5, 9, 13, 17] as const;
const ABSORB_PX = 200;          // open-hand travel that removes ~63% of the rotation correction
const ABSORB_DEADBAND_PX = 2;   // per-frame movement treated as jitter (doesn't absorb)
export type HandId = "hand-left" | "hand-right";
const IDS: readonly HandId[] = ["hand-left", "hand-right"];

export interface HandTuning {
  activeRegion: number;   // fraction of the camera frame mapped to the full viewport
  pinchDown: number;
  pinchUp: number;
  jointMargin: number;
  graceMs: number;
  releaseHoldMs: number;
  matchMaxFrac: number;   // max jump (fraction of viewport width) to count as the same hand
  filter: OneEuroParams;
  angleFilter: OneEuroParams;   // in degrees (beta per deg/s)
}

export const DEFAULT_HAND_TUNING: HandTuning = {
  activeRegion: 0.8, pinchDown: 0.25, pinchUp: 0.35, jointMargin: 0.1, graceMs: 250, releaseHoldMs: 100, matchMaxFrac: 0.25, filter: DEFAULT_ONE_EURO,
  angleFilter: { minCutoff: 1.0, beta: 0.02, dCutoff: 1.0 },
};

/** MediaPipe labels handedness assuming a mirrored (selfie) image. We feed it the raw,
 *  unmirrored camera frame, so its "Left" is the user's right hand. */
export function userHand(categoryName: string | undefined): HandId | null {
  if (categoryName === "Left") return "hand-right";
  if (categoryName === "Right") return "hand-left";
  return null;
}

const dist3 = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

export function pinchRatio(lm: readonly Pt[]): number {
  const size = dist3(lm[LM.WRIST]!, lm[LM.MIDDLE_MCP]!);
  return size > 1e-9 ? dist3(lm[LM.THUMB_TIP]!, lm[LM.INDEX_TIP]!) / size : Infinity;
}

/** Thumb tip to the index's last joint (7), over the same hand length. */
export function thumbToJointRatio(lm: readonly Pt[]): number {
  const size = dist3(lm[LM.WRIST]!, lm[LM.MIDDLE_MCP]!);
  return size > 1e-9 ? dist3(lm[LM.THUMB_TIP]!, lm[LM.INDEX_DIP]!) / size : Infinity;
}

/** Palm points in screen orientation: x mirrored and scaled by the frame's aspect ratio
 *  (normalized coords are per-axis), so rotations are true angles. */
export function palmPoints(lm: readonly Pt[], aspect: number): { x: number; y: number }[] {
  return PALM.map((i) => ({ x: (1 - lm[i]!.x) * aspect, y: lm[i]!.y }));
}

/** Knuckle midpoint minus thumb/index tip midpoint, in camera-isotropic units (x mirrored
 *  and scaled by aspect, so rotations are true angles; same space as palmPoints). */
function isoVector(lm: readonly Pt[], aspect: number): { u: number; v: number } {
  const iso = (p: Pt) => ({ u: (1 - p.x) * aspect, v: p.y });
  const k1 = iso(lm[LM.INDEX_MCP]!), k2 = iso(lm[LM.MIDDLE_MCP]!), t = iso(lm[LM.THUMB_TIP]!), i = iso(lm[LM.INDEX_TIP]!);
  return { u: (k1.u + k2.u - t.u - i.u) / 2, v: (k1.v + k2.v - t.v - i.v) / 2 };
}

/** Least-squares rotation (radians, clockwise on screen) taking point set `from` to `to`:
 *  the 2D Kabsch angle atan2(sum cross, sum dot) over centered points. */
export function rotationBetween(from: readonly { x: number; y: number }[], to: readonly { x: number; y: number }[]): number {
  const c = (ps: readonly { x: number; y: number }[]) => ({
    x: ps.reduce((s, p) => s + p.x, 0) / ps.length, y: ps.reduce((s, p) => s + p.y, 0) / ps.length,
  });
  const cf = c(from), ct = c(to);
  let cross = 0, dot = 0;
  from.forEach((f, i) => {
    const t = to[i]!;
    const fx = f.x - cf.x, fy = f.y - cf.y, tx = t.x - ct.x, ty = t.y - ct.y;
    cross += fx * ty - fy * tx;
    dot += fx * tx + fy * ty;
  });
  return Math.atan2(cross, dot);
}

/** Viewport px for normalized image landmarks: the index/middle knuckle midpoint, x
 *  mirrored, active region stretched. */
export function pointerPosition(lm: readonly Pt[], vp: Viewport, activeRegion: number): { x: number; y: number } {
  const a = lm[LM.INDEX_MCP]!, b = lm[LM.MIDDLE_MCP]!;
  const margin = (1 - activeRegion) / 2;
  const stretch = (v: number) => Math.min(1, Math.max(0, (v - margin) / activeRegion));
  return { x: stretch(1 - (a.x + b.x) / 2) * vp.width, y: stretch((a.y + b.y) / 2) * vp.height };
}

/** Give each candidate an id: nearest previous hand within maxDist first, then its
 *  handedness label if that id is free, then whatever id is left. */
export function assignIds(
  cands: readonly { x: number; y: number; label: HandId | null }[],
  prev: ReadonlyMap<HandId, { x: number; y: number }>,
  maxDist: number,
): (HandId | null)[] {
  const out: (HandId | null)[] = cands.map(() => null);
  const free = new Set<HandId>(IDS);
  const pairs: { c: number; id: HandId; d: number }[] = [];
  cands.forEach((c, ci) => prev.forEach((p, id) => {
    const d = Math.hypot(c.x - p.x, c.y - p.y);
    if (d <= maxDist) pairs.push({ c: ci, id, d });
  }));
  pairs.sort((a, b) => a.d - b.d);
  for (const { c, id } of pairs) {
    if (out[c] === null && free.has(id)) { out[c] = id; free.delete(id); }
  }
  cands.forEach((c, ci) => {
    if (out[ci] !== null) return;
    const id = c.label && free.has(c.label) ? c.label : [...free][0];
    if (id) { out[ci] = id; free.delete(id); }
  });
  return out;
}

interface HandState {
  fx: OneEuroFilter;
  fy: OneEuroFilter;
  fa: OneEuroFilter;
  x: number;
  y: number;
  palmRef: { x: number; y: number }[];   // palm shape at the last pinch (or first sight)
  angleBase: number;                     // raw angle when palmRef was taken
  rawAngle: number;                      // unwrapped, radians
  angle: number;                         // filtered, radians
  prevAngle: number;
  angleRef: number;                      // palm angle when dRef was captured
  dRef: { u: number; v: number };        // knuckles - pinch point then, camera-isotropic units
  off: { u: number; v: number };         // rotation correction, camera-isotropic units
  cx: number;                            // cursor (viewport px): pointer + correction
  cy: number;
  down: boolean;
  openSinceMs: number | null;            // pinch opened while down; release pending
  lastSeenMs: number;
  downSinceMs: number;
  trail: { t: number; x: number; y: number }[];   // positions while down, last ~1 s
}

export interface HandStats {
  frames: number;
  handsVisible: number;
  /** Grabs released because tracking lost the hand (not because the fingers opened). */
  droppedGrabs: number;
  /** Pinch opened and closed again within releaseHoldMs: absorbed, no release sent. */
  pinchFlickers: number;
  /** Per held hand: pointer displacement over the last 1 s of the hold, px/s. Hold still to read drift. */
  holdDriftPxPerS: Partial<Record<HandId, number>>;
  pinch: Partial<Record<HandId, number>>;
}

const DRIFT_WINDOW_MS = 1000;

export class HandFrameProcessor {
  private hands = new Map<HandId, HandState>();
  private s: HandStats = { frames: 0, handsVisible: 0, droppedGrabs: 0, pinchFlickers: 0, holdDriftPxPerS: {}, pinch: {} };

  private pinchDown: number;
  private pinchUp: number;

  constructor(private readonly sink: PointerSink, private readonly tuning: HandTuning = DEFAULT_HAND_TUNING) {
    this.pinchDown = tuning.pinchDown;
    this.pinchUp = tuning.pinchUp;
  }

  /** Pinch sensitivity: the grab threshold (clamped to PINCH_DOWN_RANGE); release keeps the
   *  same hysteresis gap above it. */
  setPinchDown(v: number): void {
    this.pinchDown = Math.min(PINCH_DOWN_RANGE.max, Math.max(PINCH_DOWN_RANGE.min, v));
    this.pinchUp = this.pinchDown + (this.tuning.pinchUp - this.tuning.pinchDown);
  }

  get pinchThreshold(): number { return this.pinchDown; }

  get stats(): Readonly<HandStats> { return this.s; }

  /** aspect = camera frame width / height (landmark x and y are normalized per axis). */
  process(frame: HandFrame, tMs: number, vp: Viewport, aspect = 4 / 3): void {
    const T = this.tuning;
    this.s.frames++;
    this.s.handsVisible = frame.landmarks.length;
    const cands = frame.landmarks.slice(0, IDS.length).map((lm, k) => ({
      ...pointerPosition(lm, vp, T.activeRegion),
      label: userHand(frame.handedness[k]?.[0]?.categoryName),
      ratio: pinchRatio(frame.worldLandmarks[k] ?? lm),
      joint: thumbToJointRatio(frame.worldLandmarks[k] ?? lm),
      palm: palmPoints(lm, aspect),
      knuckleToPinch: isoVector(lm, aspect),
    }));
    const prev = new Map([...this.hands].map(([id, h]) => [id, { x: h.x, y: h.y }] as const));
    const ids = assignIds(cands, prev, T.matchMaxFrac * vp.width);

    const seen = new Set<HandId>();
    cands.forEach((c, k) => {
      const id = ids[k];
      if (!id) return;
      seen.add(id);
      let h = this.hands.get(id);
      if (!h) {
        h = { fx: new OneEuroFilter(T.filter), fy: new OneEuroFilter(T.filter), fa: new OneEuroFilter(T.angleFilter),
              x: c.x, y: c.y, palmRef: c.palm, angleBase: 0, rawAngle: 0, angle: 0, prevAngle: 0, angleRef: 0,
              dRef: c.knuckleToPinch, off: { u: 0, v: 0 }, cx: c.x, cy: c.y, down: false, openSinceMs: null,
              lastSeenMs: tMs, downSinceMs: 0, trail: [] };
        this.hands.set(id, h);
      }
      h.x = h.fx.filter(c.x, tMs);
      h.y = h.fy.filter(c.y, tMs);
      const a = h.angleBase + rotationBetween(h.palmRef, c.palm);
      h.rawAngle += Math.atan2(Math.sin(a - h.rawAngle), Math.cos(a - h.rawAngle));   // unwrap
      h.angle = (h.fa.filter((h.rawAngle * 180) / Math.PI, tMs) * Math.PI) / 180;
      h.lastSeenMs = tMs;
      const wasDown = h.down;
      if (wasDown) {
        if (c.ratio < this.pinchUp) {
          if (h.openSinceMs !== null) this.s.pinchFlickers++;
          h.openSinceMs = null;
        } else {
          h.openSinceMs ??= tMs;
          if (tMs - h.openSinceMs >= T.releaseHoldMs) { h.down = false; h.openSinceMs = null; }
        }
      } else {
        h.down = c.ratio < this.pinchDown && c.ratio - c.joint < T.jointMargin;
      }
      if (h.down && !wasDown) {
        h.downSinceMs = tMs;
        h.trail = [];
        // The fingertips touch now: that's the pivot a twist will turn about, and the palm
        // shape a twist is measured against (continuous: same raw angle, new reference).
        h.dRef = c.knuckleToPinch;
        h.palmRef = c.palm;
        h.angleBase = h.rawAngle;
        // Start the twist from where the hand IS, not where the smoothed angle has got to:
        // after a fast unwind the filter lags, and the knob would then drift back by itself.
        h.fa.reset();
        h.angle = (h.fa.filter((h.rawAngle * 180) / Math.PI, tMs) * Math.PI) / 180;
        h.angleRef = h.angle;
        h.prevAngle = h.angle;
      }
      this.correct(h, vp, aspect);
      this.track(id, h, tMs);
      this.s.pinch[id] = c.ratio;
      if (h.openSinceMs !== null) return;   // release pending: hold still
      this.sink.pointer({ id, x: h.cx, y: h.cy, down: h.down, source: "hand", angle: h.angle });
    });

    for (const [id, h] of this.hands) {
      if (seen.has(id) || tMs - h.lastSeenMs <= T.graceMs) continue;
      if (h.down) this.s.droppedGrabs++;
      this.drop(id);
    }
  }

  /** Release every hand (camera stopped). */
  releaseAll(): void {
    for (const id of [...this.hands.keys()]) this.drop(id);
  }

  private drop(id: HandId): void {
    this.hands.delete(id);
    delete this.s.holdDriftPxPerS[id];
    delete this.s.pinch[id];
    this.sink.lost(id);
  }

  /** Remove the knuckle motion explained by rotation about the pinch point; bleed the
   *  correction off while the open hand travels. Sets the cursor (h.cx, h.cy). */
  private correct(h: HandState, vp: Viewport, aspect: number): void {
    const turn = (rad: number) => {
      const cos = Math.cos(rad), sin = Math.sin(rad);
      return { u: cos * h.dRef.u - sin * h.dRef.v, v: sin * h.dRef.u + cos * h.dRef.v };
    };
    const now = turn(h.angle - h.angleRef), before = turn(h.prevAngle - h.angleRef);
    h.off = { u: h.off.u - (now.u - before.u), v: h.off.v - (now.v - before.v) };
    h.prevAngle = h.angle;
    // Camera-isotropic units -> viewport px (same stretch as pointerPosition).
    const r = this.tuning.activeRegion;
    const at = () => ({ x: h.x + (h.off.u / aspect / r) * vp.width, y: h.y + (h.off.v / r) * vp.height });
    // Travel = how far the corrected cursor moved (real translation; a twist doesn't count).
    let c = at();
    const travel = Math.hypot(c.x - h.cx, c.y - h.cy) - ABSORB_DEADBAND_PX;
    if (!h.down && travel > 0) {
      const keep = Math.exp(-travel / ABSORB_PX);
      h.off = { u: h.off.u * keep, v: h.off.v * keep };
      c = at();
    }
    h.cx = Math.min(vp.width, Math.max(0, c.x));
    h.cy = Math.min(vp.height, Math.max(0, c.y));
  }

  private track(id: HandId, h: HandState, tMs: number): void {
    if (!h.down) { delete this.s.holdDriftPxPerS[id]; return; }
    h.trail.push({ t: tMs, x: h.cx, y: h.cy });
    while (h.trail.length > 1 && tMs - h.trail[0]!.t > DRIFT_WINDOW_MS) h.trail.shift();
    const first = h.trail[0]!;
    if (tMs - h.downSinceMs >= DRIFT_WINDOW_MS && tMs > first.t) {
      this.s.holdDriftPxPerS[id] = Math.hypot(h.cx - first.x, h.cy - first.y) / ((tMs - first.t) / 1000);
    }
  }
}

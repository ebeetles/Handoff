// GestureController: turns abstract pointers into control changes.
//
// A "pointer" is anything with a screen position and a down/up state: the mouse, a touch,
// or (Chunk 5) a tracked hand where down = pinch. UI components never handle input
// themselves; they register hit targets here. So adding hands = writing one adapter that
// calls pointer(...) with pinch state. Nothing in the UI or engine changes.
//
// Interaction model (chosen to work for an imprecise, jittery hand):
//  - continuous controls are RELATIVE: grab anywhere on the control, then move.
//    value = valueAtGrab + displacement / travel   (up/right = increase)
//    Relative beats absolute for hands: no jump on grab, and travel sets sensitivity.
//  - pads fire on press; toggles flip on press.
//  - double-press a continuous control within 350 ms to reset it to its default (mouse and
//    touch only: hands re-grab quickly to keep turning a knob, which would reset it).
//  - turning or dragging past either end re-bases the grab, so reversing responds at once
//    instead of first unwinding the overshoot.
//  - KNOB LOCK-ON (hands only): a hand near a twist knob locks to it, its cursor sits on the
//    knob's center, and a pinch grabs it. Twisting then keeps the cursor on the knob. Only
//    knobs: buttons and sliders hit-test exactly (on this tightly packed board, lock-on for
//    everything felt worse than none), and being inside any of them beats a knob's pull.
//    Among knobs the nearest wins, with a little hysteresis; frozen while grabbing.
//  - Seek strips are mouse-only: a stray hand pinch would jump the track.
//  - TWIST: a continuous target with `twist` is a rotary knob. A pointer that reports `angle`
//    (a hand) turns it by rotating: TWIST_RANGE_RAD of rotation spans the full 0..1. Pointers
//    without an angle (the mouse) drag it on `axis` as before.
import type { Command } from "../control/commands";
import type { ControlId, ControlSource, ControlStore, DeckId } from "../control/controls";

export type PointerSource = "mouse" | "touch" | "hand";

export interface PointerSample {
  id: string;            // stable per pointer, e.g. "mouse", "hand-left"
  x: number;             // viewport px
  y: number;
  down: boolean;         // mouse button / touch contact / pinch
  source: PointerSource;
  /** Optional rotation in radians, clockwise on screen, arbitrary zero; only changes are used.
   *  Hands report the palm's in-plane rotation. Added 2026-10-04 for twistable knobs. */
  angle?: number;
}

export type TargetSpec =
  | { kind: "continuous"; control: ControlId; axis: "x" | "y"; travelPx?: number; twist?: boolean;
      /** Value the control clicks into while dragged or twisted (its default), like a mixer's
       *  centre detent. Added 2026-10-06 (optional, non-breaking). */
      detent?: number }
  | { kind: "toggle"; control: ControlId }
  | { kind: "pad"; command: Command }
  | { kind: "seek"; deck: DeckId };

interface Grab {
  el: HTMLElement;
  spec: TargetSpec;
  startX: number;
  startY: number;
  startValue: number;         // in "raw" travel space: the value, before any detent (detentToRaw)
  twistFrom: number | null;   // angle at grab when twisting a knob, else null
}

/** Half-width of a detent, as a share of the control's travel: ~6 px of a 160 px drag, or ~6
 *  degrees of a twist, on each side of the default. */
export const DETENT = 0.04;

/** Travel ("raw", 0..1) -> value with a detent at d: a dead zone of +-DETENT around d holds the
 *  value exactly at d; the rest is stretched so 0 and 1 are still reached and nothing jumps. */
export function detentFromRaw(raw: number, d: number): number {
  const r = Math.min(1, Math.max(0, raw)), lo = Math.max(0, d - DETENT), hi = Math.min(1, d + DETENT);
  if (r < lo) return (r * d) / lo;
  if (r > hi) return d + ((r - hi) * (1 - d)) / (1 - hi);
  return d;
}

/** The inverse, for picking a drag up where the value is. */
export function detentToRaw(v: number, d: number): number {
  const lo = Math.max(0, d - DETENT), hi = Math.min(1, d + DETENT);
  if (v < d) return (v * lo) / d;
  if (v > d) return hi + ((v - d) * (1 - hi)) / (1 - d);
  return d;
}

const DOUBLE_PRESS_MS = 350;
/** Knob lock-on for hands: lock within lockPx of a knob, keep it until unlockPx, and switch
 *  to another knob only when it is switchPx closer. */
export const KNOB_LOCK = { lockPx: 36, unlockPx: 52, switchPx: 8 } as const;

type Rect = { left: number; top: number; right: number; bottom: number; width: number; height: number };
const rectDistance = (r: Rect, x: number, y: number) =>
  Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom));
const isKnob = (s: TargetSpec) => s.kind === "continuous" && s.twist === true;
/** Hand rotation for the full 0..1 of a twist knob. A wrist turns ~+/-80 degrees comfortably,
 *  so +/-75 from center reaches both ends (the drawn knob sweeps 288, so it turns ~1.9x). */
export const TWIST_RANGE_RAD = (150 * Math.PI) / 180;

export class GestureController {
  private targets = new Map<HTMLElement, TargetSpec>();
  private grabs = new Map<string, Grab>();
  private wasDown = new Map<string, boolean>();
  private lastPress = new Map<HTMLElement, number>();
  private knobLocks = new Map<string, HTMLElement>();
  /** Latest sample per pointer, for drawing hand cursors. */
  readonly pointers = new Map<string, PointerSample>();

  constructor(private store: ControlStore, private dispatch: (c: Command) => void) {}

  /** Is this element (or one it sits in) a registered control? */
  isTarget(node: EventTarget | null): boolean {
    for (let el = node instanceof Element ? node : null; el; el = el.parentElement) {
      if (this.targets.has(el as HTMLElement)) return true;
    }
    return false;
  }

  register(el: HTMLElement, spec: TargetSpec): () => void {
    this.targets.set(el, spec);
    return () => {
      this.targets.delete(el);
      for (const [id, k] of this.knobLocks) if (k === el) this.setKnobLock(id, null);
    };
  }

  /** Where to draw a pointer's cursor: on its locked knob's center, else at the pointer. */
  cursor(id: string): { x: number; y: number; locked: boolean } | null {
    const p = this.pointers.get(id);
    if (!p) return null;
    const k = this.knobLocks.get(id);
    if (!k) return { x: p.x, y: p.y, locked: false };
    const r = k.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, locked: true };
  }

  /** Feed one pointer sample. Adapters call this on every move/press/release. */
  pointer(p: PointerSample): void {
    this.pointers.set(p.id, p);
    if (p.source === "hand" && !this.grabs.has(p.id)) this.updateKnobLock(p);
    const was = this.wasDown.get(p.id) ?? false;
    this.wasDown.set(p.id, p.down);
    if (p.down && !was) this.press(p);
    else if (p.down && was) this.drag(p);
    else if (!p.down && was) this.release(p);
  }

  /** Pointer left (hand lost, mouse left window): release anything it held. */
  lost(id: string): void {
    const p = this.pointers.get(id);
    if (p && this.wasDown.get(id)) this.release({ ...p, down: false });
    this.setKnobLock(id, null);
    this.pointers.delete(id);
    this.wasDown.delete(id);
  }

  private updateKnobLock(p: PointerSample): void {
    const cur = this.knobLocks.get(p.id) ?? null;
    let best: HTMLElement | null = null, bestD = Infinity, curD = Infinity;
    for (const [el, spec] of this.targets) {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;   // hidden
      const d = rectDistance(r, p.x, p.y);
      if (!isKnob(spec)) {
        // Inside a button or slider: exact hit-testing wins over any knob's pull.
        if (d === 0 && spec.kind !== "seek") return this.setKnobLock(p.id, null);
        continue;
      }
      if (el === cur) curD = d;
      if (d < bestD) { best = el; bestD = d; }
    }
    const keep = cur && curD <= KNOB_LOCK.unlockPx && curD <= bestD + KNOB_LOCK.switchPx;
    this.setKnobLock(p.id, keep ? cur : best && bestD <= KNOB_LOCK.lockPx ? best : null);
  }

  private setKnobLock(id: string, el: HTMLElement | null): void {
    const prev = this.knobLocks.get(id) ?? null;
    if (prev === el) return;
    if (el) this.knobLocks.set(id, el); else this.knobLocks.delete(id);
    if (prev && ![...this.knobLocks.values()].includes(prev)) delete prev.dataset.locked;
    if (el) el.dataset.locked = "true";
  }

  private hit(p: PointerSample): [HTMLElement, TargetSpec] | null {
    const knob = p.source === "hand" ? this.knobLocks.get(p.id) : undefined;
    const knobSpec = knob && this.targets.get(knob);
    if (knob && knobSpec) return [knob, knobSpec];
    const { x, y } = p;
    let best: [HTMLElement, TargetSpec] | null = null;
    let bestArea = Infinity;
    for (const [el, spec] of this.targets) {
      if (spec.kind === "seek" && p.source === "hand") continue;
      const r = el.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom && r.width * r.height < bestArea) {
        best = [el, spec];
        bestArea = r.width * r.height; // smallest wins when targets nest
      }
    }
    return best;
  }

  private src(p: PointerSample): ControlSource {
    return p.source === "hand" ? "hand" : "mouse";
  }

  private press(p: PointerSample): void {
    const h = this.hit(p);
    if (!h) return;
    const [el, spec] = h;
    switch (spec.kind) {
      case "pad":
        this.dispatch(spec.command);
        el.dataset.pressed = "true";
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue: 0, twistFrom: null });
        return;
      case "toggle":
        this.store.set(spec.control, this.store.get(spec.control) > 0.5 ? 0 : 1, this.src(p));
        el.dataset.pressed = "true";
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue: 0, twistFrom: null });
        return;
      case "seek":
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue: 0, twistFrom: null });
        this.seekTo(el, spec.deck, p.x);
        return;
      case "continuous": {
        const now = performance.now();
        if (p.source !== "hand" && now - (this.lastPress.get(el) ?? -Infinity) < DOUBLE_PRESS_MS) {
          this.store.reset(spec.control, this.src(p));
          this.lastPress.delete(el);
        } else {
          this.lastPress.set(el, now);
        }
        this.store.setHeld(spec.control, true);
        el.dataset.grabbed = "true";
        const twistFrom = spec.twist && p.angle !== undefined ? p.angle : null;
        const value = this.store.get(spec.control);
        const startValue = spec.detent === undefined ? value : detentToRaw(value, spec.detent);
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue, twistFrom });
      }
    }
  }

  private drag(p: PointerSample): void {
    const g = this.grabs.get(p.id);
    if (!g) return;
    if (g.spec.kind === "seek") return this.seekTo(g.el, g.spec.deck, p.x);
    if (g.spec.kind !== "continuous") return;
    if (g.twistFrom !== null) {
      if (p.angle === undefined) return;
      const v = g.startValue + (p.angle - g.twistFrom) / TWIST_RANGE_RAD;
      // Past an end: move the reference so this angle maps exactly to the end.
      if (v > 1) g.twistFrom = p.angle - (1 - g.startValue) * TWIST_RANGE_RAD;
      else if (v < 0) g.twistFrom = p.angle + g.startValue * TWIST_RANGE_RAD;
      this.store.set(g.spec.control, this.withDetent(g.spec, v), this.src(p));
      return;
    }
    const r = g.el.getBoundingClientRect();
    const travel = g.spec.travelPx ?? (g.spec.axis === "x" ? r.width : Math.max(r.height, 160));
    const disp = g.spec.axis === "x" ? p.x - g.startX : g.startY - p.y;
    const v = g.startValue + disp / travel;
    if (v > 1 || v < 0) {
      const over = (v > 1 ? v - 1 : v) * travel;   // px past the end (signed)
      if (g.spec.axis === "x") g.startX += over; else g.startY -= over;
    }
    this.store.set(g.spec.control, this.withDetent(g.spec, v), this.src(p));
  }

  private withDetent(spec: TargetSpec, raw: number): number {
    return spec.kind === "continuous" && spec.detent !== undefined ? detentFromRaw(raw, spec.detent) : raw;
  }

  private release(p: PointerSample): void {
    const g = this.grabs.get(p.id);
    if (!g) return;
    this.grabs.delete(p.id);
    delete g.el.dataset.grabbed;
    delete g.el.dataset.pressed;
    if (g.spec.kind === "continuous") this.store.setHeld(g.spec.control, false);
  }

  private seekTo(el: HTMLElement, deck: DeckId, x: number): void {
    const r = el.getBoundingClientRect();
    this.dispatch({ type: "seekFraction", deck, fraction: Math.min(1, Math.max(0, (x - r.left) / r.width)) });
  }
}

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
//  - double-press a continuous control within 350 ms to reset it to its default.
import type { Command } from "../control/commands";
import type { ControlId, ControlSource, ControlStore, DeckId } from "../control/controls";

export type PointerSource = "mouse" | "touch" | "hand";

export interface PointerSample {
  id: string;            // stable per pointer, e.g. "mouse", "hand-left"
  x: number;             // viewport px
  y: number;
  down: boolean;         // mouse button / touch contact / pinch
  source: PointerSource;
}

export type TargetSpec =
  | { kind: "continuous"; control: ControlId; axis: "x" | "y"; travelPx?: number }
  | { kind: "toggle"; control: ControlId }
  | { kind: "pad"; command: Command }
  | { kind: "seek"; deck: DeckId };

interface Grab {
  el: HTMLElement;
  spec: TargetSpec;
  startX: number;
  startY: number;
  startValue: number;
}

const DOUBLE_PRESS_MS = 350;

export class GestureController {
  private targets = new Map<HTMLElement, TargetSpec>();
  private grabs = new Map<string, Grab>();
  private wasDown = new Map<string, boolean>();
  private lastPress = new Map<HTMLElement, number>();
  /** Latest sample per pointer, for drawing hand cursors. */
  readonly pointers = new Map<string, PointerSample>();

  constructor(private store: ControlStore, private dispatch: (c: Command) => void) {}

  register(el: HTMLElement, spec: TargetSpec): () => void {
    this.targets.set(el, spec);
    return () => this.targets.delete(el);
  }

  /** Feed one pointer sample. Adapters call this on every move/press/release. */
  pointer(p: PointerSample): void {
    this.pointers.set(p.id, p);
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
    this.pointers.delete(id);
    this.wasDown.delete(id);
  }

  private hit(x: number, y: number): [HTMLElement, TargetSpec] | null {
    let best: [HTMLElement, TargetSpec] | null = null;
    let bestArea = Infinity;
    for (const [el, spec] of this.targets) {
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
    const h = this.hit(p.x, p.y);
    if (!h) return;
    const [el, spec] = h;
    switch (spec.kind) {
      case "pad":
        this.dispatch(spec.command);
        el.dataset.pressed = "true";
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue: 0 });
        return;
      case "toggle":
        this.store.set(spec.control, this.store.get(spec.control) > 0.5 ? 0 : 1, this.src(p));
        el.dataset.pressed = "true";
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue: 0 });
        return;
      case "seek":
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue: 0 });
        this.seekTo(el, spec.deck, p.x);
        return;
      case "continuous": {
        const now = performance.now();
        if (now - (this.lastPress.get(el) ?? -Infinity) < DOUBLE_PRESS_MS) {
          this.store.reset(spec.control, this.src(p));
          this.lastPress.delete(el);
        } else {
          this.lastPress.set(el, now);
        }
        this.store.setHeld(spec.control, true);
        el.dataset.grabbed = "true";
        this.grabs.set(p.id, { el, spec, startX: p.x, startY: p.y, startValue: this.store.get(spec.control) });
      }
    }
  }

  private drag(p: PointerSample): void {
    const g = this.grabs.get(p.id);
    if (!g) return;
    if (g.spec.kind === "seek") return this.seekTo(g.el, g.spec.deck, p.x);
    if (g.spec.kind !== "continuous") return;
    const r = g.el.getBoundingClientRect();
    const travel = g.spec.travelPx ?? (g.spec.axis === "x" ? r.width : Math.max(r.height, 160));
    const disp = g.spec.axis === "x" ? p.x - g.startX : g.startY - p.y;
    this.store.set(g.spec.control, g.startValue + disp / travel, this.src(p));
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
    this.dispatch({ type: "seekFraction", deck, fraction: (x - r.left) / r.width });
  }
}

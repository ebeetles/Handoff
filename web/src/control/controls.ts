// The control layer: every continuous/toggle control on the board is a number in 0..1
// with an id. Inputs (mouse, keyboard, hands, co-DJ automation) WRITE here; the audio
// engine and UI READ from here. Nothing else talks to the engine's params directly.
// That's what makes hand tracking (Chunk 5) and automation (Chunk 6) drop-in later.
import type { StemName } from "../contracts/track";

export type DeckId = "A" | "B";
export const DECKS: readonly DeckId[] = ["A", "B"];

export type DeckControl = "eqHigh" | "eqMid" | "eqLow" | "filter" | "volume" | "tempo" | "echo" | `stem.${StemName}`;
export type GlobalControl = "xfader" | "master" | "quantize";
export type ControlId = `${DeckId}.${DeckControl}` | GlobalControl;

/** Who wrote a value. Automation must yield to a human holding the control. */
export type ControlSource = "mouse" | "keyboard" | "hand" | "auto" | "engine";

interface ControlDef { default: number; kind: "continuous" | "toggle"; label: string }

const deckDefs: Record<DeckControl, ControlDef> = {
  eqHigh: { default: 0.5, kind: "continuous", label: "High" },
  eqMid: { default: 0.5, kind: "continuous", label: "Mid" },
  eqLow: { default: 0.5, kind: "continuous", label: "Low" },
  filter: { default: 0.5, kind: "continuous", label: "Filter" },
  volume: { default: 0.8, kind: "continuous", label: "Volume" },
  tempo: { default: 0.5, kind: "continuous", label: "Tempo" },
  echo: { default: 0, kind: "continuous", label: "Echo" },   // send to a 3/4-beat delay; tails ring out after a cut
  "stem.drums": { default: 1, kind: "toggle", label: "Drums" },
  "stem.bass": { default: 1, kind: "toggle", label: "Bass" },
  "stem.vocals": { default: 1, kind: "toggle", label: "Vocals" },
  "stem.other": { default: 1, kind: "toggle", label: "Melody" },
};

export const CONTROL_DEFS: Record<ControlId, ControlDef> = {
  xfader: { default: 0.5, kind: "continuous", label: "Crossfader" },
  master: { default: 0.8, kind: "continuous", label: "Master" },
  quantize: { default: 1, kind: "toggle", label: "Snap to beat" },
  ...(Object.fromEntries(
    DECKS.flatMap((d) => Object.entries(deckDefs).map(([k, v]) => [`${d}.${k}`, v])),
  ) as Record<`${DeckId}.${DeckControl}`, ControlDef>),
};

export const deckControl = (deck: DeckId, c: DeckControl): ControlId => `${deck}.${c}`;

type Listener = (value: number, source: ControlSource) => void;

export class ControlStore {
  private values = new Map<ControlId, number>();
  private listeners = new Map<ControlId, Set<Listener>>();
  private anyListeners = new Set<(id: ControlId, value: number, source: ControlSource) => void>();
  /** Controls currently grabbed by a human; automation writes to these are ignored. */
  private held = new Set<ControlId>();

  constructor() {
    for (const [id, def] of Object.entries(CONTROL_DEFS)) this.values.set(id as ControlId, def.default);
  }

  get(id: ControlId): number {
    return this.values.get(id)!;
  }

  set(id: ControlId, value: number, source: ControlSource): void {
    if (source === "auto" && this.held.has(id)) return;
    const v = Math.min(1, Math.max(0, value));
    if (this.values.get(id) === v) return;
    this.values.set(id, v);
    this.listeners.get(id)?.forEach((fn) => fn(v, source));
    this.anyListeners.forEach((fn) => fn(id, v, source));
  }

  reset(id: ControlId, source: ControlSource): void {
    this.set(id, CONTROL_DEFS[id].default, source);
  }

  isHeld(id: ControlId): boolean {
    return this.held.has(id);
  }

  setHeld(id: ControlId, held: boolean): void {
    if (held) this.held.add(id); else this.held.delete(id);
  }

  subscribe(id: ControlId, fn: Listener): () => void {
    let set = this.listeners.get(id);
    if (!set) this.listeners.set(id, (set = new Set()));
    set.add(fn);
    return () => set!.delete(fn);
  }

  subscribeAll(fn: (id: ControlId, value: number, source: ControlSource) => void): () => void {
    this.anyListeners.add(fn);
    return () => this.anyListeners.delete(fn);
  }
}

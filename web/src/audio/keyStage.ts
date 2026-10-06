// Key lock and key shift for one deck: a real-time pitch shifter at the end of the deck's chain.
//
// The transport is untouched: sources still change tempo through playbackRate (sync, rides,
// loops, the brake all work as before), which also moves pitch by 12*log2(rate) semitones. With
// key lock on, this stage shifts the deck back by that much, plus any key shift the DJ (or the
// co-DJ) asked for. The brake still dives, because it ramps playbackRate without changing the
// deck's nominal rate.
//
// The shifter (Signalsmith Stretch, MIT, WASM in an AudioWorklet, live-input mode) delays its
// output by exactly KEY_LATENCY_S (measured in Chromium: 80 ms block -> 80 ms, clicks included).
// So that both decks stay in time with each other whatever they're doing, every deck passes
// through the same delay while key lock is on: through the shifter when it has a shift to apply,
// otherwise through a plain DelayNode of the same length (bit-transparent: a pitch shifter at
// 0 semitones still softens transients). Paths cross over in CROSS_S, so nothing clicks.
// Pitch changes are scheduled for the content they belong to: a rate change at t is heard at
// t + latency, so its compensation is scheduled at output time t + latency.
// Loaded at run time from the published file, untouched: the library builds its AudioWorklet from
// its own functions' source text, and both Vite's dev pre-bundling and the production minifier
// rewrote that code so the worklet never started (key lock showed "unavailable" in a build).
import stretchUrl from "signalsmith-stretch?url";

type StretchFactory = (ctx: BaseAudioContext, options?: AudioWorkletNodeOptions) => Promise<AudioNode>;

export const KEY_BLOCK_MS = 80;
export const KEY_LATENCY_S = KEY_BLOCK_MS / 1000;
const CROSS_S = 0.02;
const ZERO = 0.005;   // semitones below which the deck takes the transparent path

/** The Stretch node's extra methods (README: schedule, configure, latency, start). */
interface StretchNode extends AudioNode {
  schedule(change: { output?: number; active?: boolean; semitones?: number }): Promise<unknown>;
  configure(cfg: { blockMs?: number }): Promise<unknown>;
  latency(): Promise<number>;
  start(when?: number): Promise<unknown>;
}

/** Semitones a deck must be shifted by: its key shift, less what its tempo did to its pitch. */
export function keyCompensation(rate: number, keyShift: number, lock: boolean): number {
  return lock ? keyShift - 12 * Math.log2(rate) : 0;
}

export class KeyStage {
  readonly input: GainNode;
  readonly output: GainNode;
  private readonly dry: DelayNode;
  private readonly dryGain: GainNode;
  private readonly wetGain: GainNode;
  private shifter: StretchNode | null = null;
  private lock = true;
  private semis = 0;
  /** Resolves once the shifter is running (it loads asynchronously); undefined errors are kept in `error`. */
  readonly ready: Promise<void>;
  error: string | null = null;

  constructor(private readonly ctx: BaseAudioContext) {
    this.input = new GainNode(ctx);
    this.output = new GainNode(ctx);
    this.dry = new DelayNode(ctx, { maxDelayTime: 1, delayTime: KEY_LATENCY_S });
    this.dryGain = new GainNode(ctx, { gain: 1 });
    this.wetGain = new GainNode(ctx, { gain: 0 });
    this.input.connect(this.dry).connect(this.dryGain).connect(this.output);
    this.wetGain.connect(this.output);
    this.ready = this.init();
  }

  private async init(): Promise<void> {
    try {
      // A worklet that never answers would leave key lock silently off (it did once, when Vite
      // pre-bundled the library): give up after a while and say so.
      const { default: Stretch } = (await import(/* @vite-ignore */ stretchUrl)) as { default: StretchFactory };
      const node = await Promise.race([
        Stretch(this.ctx) as Promise<unknown> as Promise<StretchNode>,
        new Promise<never>((_, reject) => setTimeout(() => reject(new Error("the pitch shifter didn't start")), 10000)),
      ]);
      await node.configure({ blockMs: KEY_BLOCK_MS });
      await node.start();
      this.input.connect(node).connect(this.wetGain);
      this.shifter = node;
      this.apply(this.ctx.currentTime);
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);   // key lock unavailable: plain delay
    }
  }

  /** Latency every deck shares while key lock is on (0 when off). */
  get latency(): number {
    return this.lock ? KEY_LATENCY_S : 0;
  }

  get semitones(): number {
    return this.semis;
  }

  get shifting(): boolean {
    return this.wetGain.gain.value > 0.5;
  }

  /** Key lock on/off for this deck. Turning it off removes the delay, so the engine switches all
   *  decks together, and fades the output around the switch. */
  setLock(on: boolean, at = this.ctx.currentTime): void {
    if (on === this.lock) return;
    this.lock = on;
    const g = this.output.gain;
    g.cancelScheduledValues(at);
    g.setValueAtTime(g.value, at);
    g.linearRampToValueAtTime(0, at + CROSS_S);
    this.dry.delayTime.setValueAtTime(on ? KEY_LATENCY_S : 0, at + CROSS_S);
    g.linearRampToValueAtTime(1, at + 2 * CROSS_S);
    if (!on) this.setSemitones(0, at);
  }

  /** Shift the content that plays at `at` (heard at at + latency) by `semis` semitones. */
  setSemitones(semis: number, at = this.ctx.currentTime): void {
    this.semis = this.lock ? semis : 0;
    this.apply(at);
  }

  private apply(at: number): void {
    const t = Math.max(this.ctx.currentTime, at) + this.latency;
    const wet = this.shifter !== null && Math.abs(this.semis) >= ZERO;
    if (this.shifter) void this.shifter.schedule({ output: t, semitones: this.semis });
    for (const [g, on] of [[this.wetGain.gain, wet], [this.dryGain.gain, !wet]] as const) {
      g.cancelScheduledValues(t);
      g.setValueAtTime(g.value, t);
      g.linearRampToValueAtTime(on ? 1 : 0, t + CROSS_S);
    }
  }
}

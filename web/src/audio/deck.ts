// One deck = one track's audio graph + transport.
//
//   sources (mix, or 4 stems) -> per-source gain -> declick -> EQ low/mid/high
//     -> low-pass -> high-pass -> volume -> [meter] -> crossfade gain -> engine master
//
// Rules (see AGENTS.md):
//  - All timing uses AudioContext.currentTime. Never setTimeout for audio events.
//  - Every rate/loop/position change re-anchors first (transport.ts).
//  - Discontinuities (seek, pause) fade the declick gain over ~6 ms to avoid clicks.
import type { StemName, TrackAnalysis, Waveform } from "../contracts/track";
import { STEM_NAMES } from "../contracts/track";
import type { DeckId } from "../control/controls";
import { BeatGrid } from "./grid";
import { eqDb, filterParams, volumeGain } from "./mapping";
import { ctxTimeAt, positionAt, reanchor, stoppedAt, type Anchor, type Loop } from "./transport";

export type SourceKey = "mix" | StemName;

export interface LoadedTrack {
  analysis: TrackAnalysis;
  waveform: Waveform;
  grid: BeatGrid;
  buffers: Map<SourceKey, AudioBuffer>;
  usingStems: boolean;
}

const DECLICK_S = 0.006;
const SMOOTH_TC = 0.012; // time constant for knob smoothing (setTargetAtTime)

export class Deck {
  track: LoadedTrack | null = null;
  anchor: Anchor = stoppedAt(0);
  /** During a roll: where the playhead would be without it (follows rate changes). */
  slip: Anchor | null = null;
  cuePos = 0;
  synced = false;
  loading: string | null = null;
  error: string | null = null;

  private sources = new Map<SourceKey, AudioBufferSourceNode>();
  private sourceGains = new Map<SourceKey, GainNode>();
  private readonly sum: GainNode;
  private readonly declick: GainNode;
  private readonly eqLow: BiquadFilterNode;
  private readonly eqMid: BiquadFilterNode;
  private readonly eqHigh: BiquadFilterNode;
  private readonly lowpass: BiquadFilterNode;
  private readonly highpass: BiquadFilterNode;
  private readonly volume: GainNode;
  private readonly meter: AnalyserNode;
  private readonly meterBuf = new Float32Array(1024);
  private meterHold = 0;
  readonly output: GainNode; // crossfade gain; engine connects this to master

  constructor(private readonly ctx: AudioContext, readonly id: DeckId) {
    const c = ctx;
    this.sum = c.createGain();
    this.declick = c.createGain();
    this.eqLow = new BiquadFilterNode(c, { type: "lowshelf", frequency: 200 });
    this.eqMid = new BiquadFilterNode(c, { type: "peaking", frequency: 1000, Q: 0.7 });
    this.eqHigh = new BiquadFilterNode(c, { type: "highshelf", frequency: 3200 });
    this.lowpass = new BiquadFilterNode(c, { type: "lowpass", frequency: 22000, Q: 0.707 });
    this.highpass = new BiquadFilterNode(c, { type: "highpass", frequency: 10, Q: 0.707 });
    this.volume = c.createGain();
    this.meter = new AnalyserNode(c, { fftSize: 1024 });
    this.output = c.createGain();
    this.sum.connect(this.declick).connect(this.eqLow).connect(this.eqMid).connect(this.eqHigh)
      .connect(this.lowpass).connect(this.highpass).connect(this.volume).connect(this.output);
    this.volume.connect(this.meter);
  }

  get now(): number {
    return this.ctx.currentTime;
  }

  position(now = this.now): number {
    return positionAt(this.anchor, now);
  }

  get playing(): boolean {
    return this.anchor.playing;
  }

  /** True while a quantized launch is scheduled but hasn't started. */
  get pending(): boolean {
    return this.anchor.playing && this.anchor.ctxTime > this.now;
  }

  // ------------------------------------------------------------ loading

  setTrack(track: LoadedTrack): void {
    this.hardStop();
    this.sourceGains.forEach((g) => g.disconnect());
    this.sourceGains.clear();
    this.track = track;
    for (const key of track.buffers.keys()) {
      const g = this.ctx.createGain();
      g.connect(this.sum);
      this.sourceGains.set(key, g);
    }
    const a = track.analysis;
    this.cuePos = a.beats[a.first_downbeat_index]!;
    this.anchor = stoppedAt(this.cuePos, this.anchor.rate);
    this.synced = false;
    this.error = null;
  }

  unload(): void {
    this.hardStop();
    this.slip = null;
    this.track = null; // drop buffer references so GC can reclaim (stems are large)
  }

  // ------------------------------------------------------------ transport

  private makeSources(when: number, offset: number): void {
    const t = this.track!;
    const { rate, loop } = this.anchor;
    for (const [key, buffer] of t.buffers) {
      const src = new AudioBufferSourceNode(this.ctx, { buffer, playbackRate: rate });
      if (loop) Object.assign(src, { loop: true, loopStart: loop.start, loopEnd: loop.end });
      src.connect(this.sourceGains.get(key)!);
      src.start(when, Math.max(0, offset));
      this.sources.set(key, src);
    }
  }

  private stopSources(when: number): void {
    this.sources.forEach((s) => {
      try { s.stop(when); } catch { /* already stopped */ }
    });
    this.sources.clear();
  }

  private hardStop(): void {
    this.stopSources(this.now);
    this.anchor = stoppedAt(this.position(), this.anchor.rate);
  }

  /** Start playing at AudioContext time `when` (>= now) from the current position. */
  play(when = this.now): void {
    if (!this.track || this.anchor.playing) return;
    const from = this.anchor.pos;
    if (from >= this.track.analysis.duration_s - 0.05) return;
    const g = this.declick.gain;
    g.cancelScheduledValues(this.now);
    g.setValueAtTime(0, when);
    g.linearRampToValueAtTime(1, when + DECLICK_S);
    this.anchor = { ...this.anchor, playing: true, ctxTime: when, pos: from };
    this.makeSources(when, from);
  }

  pause(): void {
    if (!this.anchor.playing) return;
    this.slip = null;
    const now = this.now;
    const g = this.declick.gain;
    g.cancelScheduledValues(now);
    g.setValueAtTime(g.value, now);
    g.linearRampToValueAtTime(0, now + DECLICK_S);
    const pos = this.position(now);
    this.stopSources(now + DECLICK_S);
    this.anchor = { ...this.anchor, playing: false, pos };
  }

  /** Be at `pos` as of AudioContext time `at` (default: now). Pass the `at` you computed
   *  `pos` from: currentTime can step a render quantum (2.9 ms) between two reads in one task,
   *  which left jumps and roll exits ~3 ms out of phase under load (browser smoke test). */
  seek(pos: number, at = this.now): void {
    if (!this.track) return;
    pos = Math.min(Math.max(0, pos), this.track.analysis.duration_s - 0.05);
    const loop = this.anchor.loop && pos >= this.anchor.loop.start && pos < this.anchor.loop.end ? this.anchor.loop : null;
    if (!this.anchor.playing) {
      this.anchor = { ...this.anchor, pos, loop };
      return;
    }
    const now = this.now;
    const t1 = now + DECLICK_S;
    // "seek(pos)" means "be at pos NOW" (or at `at`). The new sources only start after the
    // fade-out, at t1, so start them (t1 - at) * rate further in. Without this, every seek
    // (and so every sync) landed 6 ms late; measured in the browser test before this fix.
    const startPos = pos + (t1 - at) * this.anchor.rate;
    const g = this.declick.gain;
    g.cancelScheduledValues(now);
    g.setValueAtTime(g.value, now);
    g.linearRampToValueAtTime(0, t1);
    g.linearRampToValueAtTime(1, t1 + DECLICK_S);
    this.stopSources(t1);
    this.anchor = { ...this.anchor, playing: true, ctxTime: t1, pos: startPos, loop };
    this.makeSources(t1, startPos);
  }

  setRate(rate: number): void {
    this.anchor = reanchor(this.anchor, this.now, { rate });
    if (this.slip) this.slip = reanchor(this.slip, this.now, { rate });
    const at = Math.max(this.now, this.anchor.ctxTime);
    this.sources.forEach((s) => s.playbackRate.setValueAtTime(rate, at));
  }

  setLoop(loop: Loop | null): void {
    this.anchor = reanchor(this.anchor, this.now, { loop });
    this.sources.forEach((s) => {
      if (loop) {
        // Order matters: set bounds before enabling, so the source never sees a stale range.
        s.loopStart = loop.start;
        s.loopEnd = loop.end;
        s.loop = true;
      } else {
        s.loop = false;
      }
    });
  }

  /** AudioContext time of the next beat (for quantized actions); `now` if not playing. */
  nextBeatCtxTime(): number {
    const now = this.now;
    if (!this.track || !this.anchor.playing) return now;
    const pos = this.position(now);
    const nb = this.track.grid.nextTime(pos, "beat");
    if (this.anchor.loop && nb >= this.anchor.loop.end) return now; // would wrap; don't guess
    return Math.max(now, ctxTimeAt(this.anchor, nb));
  }

  /** Call regularly (UI frame loop): stops the transport when the track runs out. */
  tick(): void {
    if (this.track && this.anchor.playing && !this.anchor.loop && this.position() >= this.track.analysis.duration_s) {
      this.stopSources(this.now);
      this.anchor = stoppedAt(this.track.analysis.duration_s, this.anchor.rate);
    }
  }

  // ------------------------------------------------------------ mixer params

  setEq(band: "low" | "mid" | "high", v: number): void {
    const node = band === "low" ? this.eqLow : band === "mid" ? this.eqMid : this.eqHigh;
    node.gain.setTargetAtTime(eqDb(v), this.now, SMOOTH_TC);
  }

  setFilter(v: number): void {
    const p = filterParams(v);
    this.lowpass.frequency.setTargetAtTime(p.lowpassHz, this.now, SMOOTH_TC);
    this.highpass.frequency.setTargetAtTime(p.highpassHz, this.now, SMOOTH_TC);
    this.lowpass.Q.setTargetAtTime(p.q, this.now, SMOOTH_TC);
    this.highpass.Q.setTargetAtTime(p.q, this.now, SMOOTH_TC);
  }

  setVolume(v: number): void {
    this.volume.gain.setTargetAtTime(volumeGain(v), this.now, SMOOTH_TC);
  }

  setCrossfadeGain(g: number): void {
    this.output.gain.setTargetAtTime(g, this.now, SMOOTH_TC);
  }

  setStem(stem: StemName, on: boolean, when: number): void {
    const g = this.sourceGains.get(stem);
    if (!g) return; // track has no stems
    g.gain.cancelScheduledValues(when);
    g.gain.setTargetAtTime(on ? 1 : 0, when, 0.004);
  }

  get hasStems(): boolean {
    return !!this.track?.usingStems;
  }

  /** Post-fader level, 0..1 (dBFS mapped from -48..0), with peak-hold decay so the
   *  meter doesn't flicker to zero between kicks. Call at a steady rate (UI loop). */
  level(): number {
    this.meter.getFloatTimeDomainData(this.meterBuf);
    let sum = 0;
    for (let i = 0; i < this.meterBuf.length; i++) sum += this.meterBuf[i]! ** 2;
    const db = 10 * Math.log10(sum / this.meterBuf.length + 1e-12);
    const now = Math.min(1, Math.max(0, (db + 48) / 48));
    this.meterHold = Math.max(now, this.meterHold * 0.88);
    return this.meterHold;
  }

  static stemKeys(stems: boolean): SourceKey[] {
    return stems ? [...STEM_NAMES] : ["mix"];
  }
}

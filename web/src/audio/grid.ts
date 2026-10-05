// Beat-grid math. Pure functions over the analysis' beat list; no Web Audio here.
// Track time t (seconds into the file) <-> fractional beat index b <-> fractional bar.
// Between beats we interpolate linearly; outside the list we extrapolate with the
// first/last beat period, so positions before beat 0 get negative beat indices.

export class BeatGrid {
  readonly n: number;
  private readonly p0: number;
  private readonly pLast: number;

  constructor(readonly beats: readonly number[], readonly firstDownbeat: number, readonly beatsPerBar = 4) {
    if (beats.length < 2) throw new Error("BeatGrid needs at least 2 beats");
    this.n = beats.length;
    this.p0 = beats[1]! - beats[0]!;
    this.pLast = beats[this.n - 1]! - beats[this.n - 2]!;
  }

  /** Largest i with beats[i] <= t, or -1 if t is before the first beat. */
  private indexAtOrBefore(t: number): number {
    const b = this.beats;
    if (t < b[0]!) return -1;
    let lo = 0, hi = this.n - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (b[mid]! <= t) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  beatAt(t: number): number {
    const b = this.beats;
    const i = this.indexAtOrBefore(t);
    if (i < 0) return (t - b[0]!) / this.p0;
    if (i >= this.n - 1) return this.n - 1 + (t - b[this.n - 1]!) / this.pLast;
    return i + (t - b[i]!) / (b[i + 1]! - b[i]!);
  }

  timeAtBeat(beat: number): number {
    const b = this.beats;
    if (beat < 0) return b[0]! + beat * this.p0;
    if (beat >= this.n - 1) return b[this.n - 1]! + (beat - (this.n - 1)) * this.pLast;
    const i = Math.floor(beat);
    return b[i]! + (beat - i) * (b[i + 1]! - b[i]!);
  }

  /** Fractional bar index; bar 0 starts at the first downbeat. */
  barAt(t: number): number {
    return (this.beatAt(t) - this.firstDownbeat) / this.beatsPerBar;
  }

  timeAtBar(bar: number): number {
    return this.timeAtBeat(this.firstDownbeat + bar * this.beatsPerBar);
  }

  /** Local beat period (seconds) at track time t. */
  periodAt(t: number): number {
    const i = this.indexAtOrBefore(t);
    if (i < 0) return this.p0;
    if (i >= this.n - 1) return this.pLast;
    return this.beats[i + 1]! - this.beats[i]!;
  }

  bpmAt(t: number): number {
    return 60 / this.periodAt(t);
  }

  nearestBeatTime(t: number): number {
    return this.timeAtBeat(Math.round(this.beatAt(t)));
  }

  /** Time of the beat at or before t (or the bar start if unit === "bar"). */
  floorTime(t: number, unit: "beat" | "bar"): number {
    if (unit === "beat") return this.timeAtBeat(Math.floor(this.beatAt(t) + 1e-6));
    return this.timeAtBar(Math.floor(this.barAt(t) + 1e-6));
  }

  /** Time of the next beat (or bar) strictly after t. */
  nextTime(t: number, unit: "beat" | "bar"): number {
    if (unit === "beat") return this.timeAtBeat(Math.floor(this.beatAt(t) + 1e-6) + 1);
    return this.timeAtBar(Math.floor(this.barAt(t) + 1e-6) + 1);
  }
}

/** Where a loop of `beats` starting now should sit. With snap on, it starts on its own grid:
 *  loops of a bar or more on the bar line, 1-3 beats on the beat, and rolls (under a beat)
 *  on the current 1/2, 1/4, 1/8 ... of a beat, so the playhead is already inside the loop. */
export function loopRange(g: BeatGrid, pos: number, beats: number, quantize: boolean): { start: number; end: number } {
  let start = pos;
  if (quantize) {
    if (beats >= 4) start = g.floorTime(pos, "bar");
    else if (beats >= 1) start = g.floorTime(pos, "beat");
    else start = g.timeAtBeat(Math.floor(g.beatAt(pos) / beats + 1e-6) * beats);
  }
  return { start, end: g.timeAtBeat(g.beatAt(start) + beats) };
}

// Playhead math for one deck. Web Audio sources can't be paused or queried for their
// position, so we keep an "anchor": at AudioContext time ctxTime the playhead was at
// track position pos, moving at rate (track seconds per real second). Then
//
//     position(now) = pos + (now - ctxTime) * rate
//
// Every change of rate, loop, or position RE-ANCHORS first (pos := position(now),
// ctxTime := now). Skipping the re-anchor on a rate change is the classic bug: the
// whole elapsed time gets re-scaled by the new rate and the playhead jumps.

export interface Loop { start: number; end: number; beats: number }

export interface Anchor {
  readonly playing: boolean;
  readonly ctxTime: number;
  readonly pos: number;
  readonly rate: number;
  readonly loop: Loop | null;
}

export const stoppedAt = (pos: number, rate = 1): Anchor => ({ playing: false, ctxTime: 0, pos, rate, loop: null });

export function positionAt(a: Anchor, now: number): number {
  // now < ctxTime happens during a quantized launch: sources are scheduled to start in the future.
  if (!a.playing || now <= a.ctxTime) return a.pos;
  const raw = a.pos + (now - a.ctxTime) * a.rate;
  const L = a.loop;
  // Mirrors AudioBufferSourceNode: once the playhead reaches loopEnd it wraps to loopStart.
  if (L && a.pos < L.end && raw >= L.end) {
    const len = L.end - L.start;
    return L.start + ((raw - L.start) % len);
  }
  return raw;
}

/** AudioContext time at which the playhead reaches `target` (assumes no loop wrap in between). */
export function ctxTimeAt(a: Anchor, target: number): number {
  return a.ctxTime + (target - a.pos) / a.rate;
}

export function reanchor(a: Anchor, now: number, changes: Partial<Omit<Anchor, "ctxTime" | "pos">> & { pos?: number } = {}): Anchor {
  // max(): if a launch is still pending, keep its scheduled start time.
  return { ...a, pos: positionAt(a, now), ctxTime: Math.max(now, a.ctxTime), ...changes };
}

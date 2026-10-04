// Tempo + phase sync math (pure).
//
// Effective BPM of a deck = trackBpm * rate. To sync deck M to deck O we pick a rate so
//   bpm_M * rate_M = m * bpm_O * rate_O,   m in {1, 1/2, 2}
// (m != 1 = half/double-time mixing, e.g. 87 BPM hip-hop under 174 BPM drum & bass).
// Phase: one bar of M spans 1/m bars of O, so M's bar phase should equal frac(barPos_O * m).

export const TEMPO_RANGE = 0.08; // tempo fader covers rate 1 +/- 8%

export interface SyncChoice { multiplier: 0.5 | 1 | 2; rate: number }

export function chooseSync(myBpm: number, otherEffectiveBpm: number, range = TEMPO_RANGE): SyncChoice | null {
  let best: SyncChoice | null = null;
  for (const m of [1, 0.5, 2] as const) {
    const rate = (otherEffectiveBpm * m) / myBpm;
    if (Math.abs(rate - 1) <= range + 1e-9 && (!best || Math.abs(rate - 1) < Math.abs(best.rate - 1) - 1e-9)) {
      best = { multiplier: m, rate };
    }
  }
  return best;
}

export const frac = (x: number) => x - Math.floor(x);

/** Wrap x into [-0.5, 0.5). */
export const wrapHalf = (x: number) => frac(x + 0.5) - 0.5;

/** How many of MY bars to move so my bar phase matches the other deck's. In [-0.5, 0.5). */
export function barPhaseDelta(myBarPos: number, otherBarPos: number, multiplier: number): number {
  return wrapHalf(frac(otherBarPos * multiplier) - frac(myBarPos));
}

/**
 * Quantized launch: the other deck's (unscaled) bar position at which I should start so
 * that my current bar phase lines up. Returns the first such point at least
 * `minAheadBars` (in the other deck's bars) after otherBarNow.
 */
export function alignedLaunchBar(otherBarNow: number, multiplier: number, myPhase: number, minAheadBars: number): number {
  const sNow = (otherBarNow + minAheadBars) * multiplier;
  const n = Math.ceil(sNow - myPhase - 1e-9);
  return (n + myPhase) / multiplier;
}

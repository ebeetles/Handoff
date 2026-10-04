// Control value (0..1) -> audio parameter curves. One place, so every input source
// (mouse, hands, co-DJ automation) gets identical behavior.
import { TEMPO_RANGE } from "./sync";

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** EQ: 0.5 = flat. Below: down to -40 dB (a "kill"). Above: up to +6 dB. */
export function eqDb(v: number): number {
  v = clamp01(v);
  if (v >= 0.5) return 6 * (v - 0.5) / 0.5;
  return -40 * Math.pow((0.5 - v) / 0.5, 1.3);
}

/** One knob, two filters. Center (+/- 0.02) = off. Left = low-pass sweeping 20 kHz -> 60 Hz,
 *  right = high-pass sweeping 20 Hz -> 8 kHz, exponentially (pitch perception is logarithmic). */
export function filterParams(v: number): { lowpassHz: number; highpassHz: number; q: number } {
  v = clamp01(v);
  const dead = 0.02;
  if (v < 0.5 - dead) {
    const d = (0.5 - dead - v) / (0.5 - dead);
    return { lowpassHz: 20000 * Math.pow(60 / 20000, d), highpassHz: 10, q: 0.707 + 0.9 * d };
  }
  if (v > 0.5 + dead) {
    const d = (v - 0.5 - dead) / (0.5 - dead);
    return { lowpassHz: 22000, highpassHz: 20 * Math.pow(8000 / 20, d), q: 0.707 + 0.9 * d };
  }
  return { lowpassHz: 22000, highpassHz: 10, q: 0.707 };
}

/** Channel fader: squared law approximates a perceptual (audio-taper) fader. */
export const volumeGain = (v: number) => clamp01(v) ** 2;

export const masterGain = (v: number) => 1.25 * clamp01(v) ** 2;

/** Equal-power crossfader: gA^2 + gB^2 = 1 everywhere, so a blend doesn't dip in loudness. */
export function xfadeGains(x: number): { a: number; b: number } {
  const th = clamp01(x) * Math.PI / 2;
  return { a: Math.cos(th), b: Math.sin(th) };
}

export const tempoRate = (v: number) => 1 + (clamp01(v) - 0.5) * 2 * TEMPO_RANGE;
export const rateToTempo = (rate: number) => clamp01((rate - 1) / (2 * TEMPO_RANGE) + 0.5);

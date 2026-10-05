// One Euro filter (Casiez, Roussel, Vogel, CHI 2012): an adaptive low-pass for noisy
// pointer input. Cutoff rises with speed, so a still hand is smoothed heavily (no jitter)
// and a fast one lightly (little lag):
//   dx_hat = lowpass(dx/dt, alpha(dCutoff))
//   cutoff = minCutoff + beta * |dx_hat|
//   x_hat  = lowpass(x, alpha(cutoff)),   alpha(fc) = 1 / (1 + 1 / (2*pi*fc*dt))
// Units matter for beta: we filter viewport px, so speed is px/s.

export interface OneEuroParams { minCutoff: number; beta: number; dCutoff: number }

export const DEFAULT_ONE_EURO: OneEuroParams = { minCutoff: 1.0, beta: 0.01, dCutoff: 1.0 };

const alpha = (cutoffHz: number, dt: number) => 1 / (1 + 1 / (2 * Math.PI * cutoffHz * dt));

export class OneEuroFilter {
  private x: number | null = null;
  private dx = 0;
  private tMs = 0;

  constructor(private readonly p: OneEuroParams = DEFAULT_ONE_EURO) {}

  /** Filter a sample taken at time tMs (milliseconds, increasing). */
  filter(value: number, tMs: number): number {
    if (this.x === null) {
      this.x = value;
      this.tMs = tMs;
      return value;
    }
    const dt = Math.max((tMs - this.tMs) / 1000, 1e-3);
    this.tMs = tMs;
    const aD = alpha(this.p.dCutoff, dt);
    this.dx = aD * ((value - this.x) / dt) + (1 - aD) * this.dx;
    const a = alpha(this.p.minCutoff + this.p.beta * Math.abs(this.dx), dt);
    this.x = a * value + (1 - a) * this.x;
    return this.x;
  }

  reset(): void {
    this.x = null;
    this.dx = 0;
  }
}

// The composed transitions for this library (library/transitions.json), if the planner has run.
import { assertTransitions, type Candidate, type Transitions } from "../contracts/transitions";

export class TransitionLibrary {
  private data: Transitions | null = null;
  private listeners = new Set<() => void>();
  error: string | null = null;

  constructor(private readonly base: string) {}

  async load(): Promise<void> {
    try {
      const r = await fetch(`${this.base}/transitions.json`);
      // Vite's dev server answers a missing file with index.html: treat that as "not planned yet".
      if (!r.ok || (r.headers.get("content-type") ?? "").includes("text/html")) return;
      const t: unknown = await r.json();
      assertTransitions(t);
      this.data = t;
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    }
    this.listeners.forEach((fn) => fn());
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Incorporate an on-demand response while preserving other exits and track pairs. */
  merge(data: Transitions): void {
    assertTransitions(data);
    const pairs = new Map((this.data?.pairs ?? []).map((p) => [`${p.out_track}:${p.in_track}`, p]));
    for (const p of data.pairs) {
      const key = `${p.out_track}:${p.in_track}`;
      const cs = new Map([...(pairs.get(key)?.candidates ?? []), ...p.candidates].map((c) => [c.id, c]));
      const candidates = [...cs.values()];
      const valid = candidates.map((c, i) => ({ c, i })).filter(({ c }) => c.recipe && c.critic?.valid);
      valid.sort((a, b) => rank(a.c, b.c));
      pairs.set(key, { ...p, candidates, best: valid[0]?.i ?? null });
    }
    this.data = { ...data, pairs: [...pairs.values()] };
    this.listeners.forEach((fn) => fn());
  }

  composedFor(outTrack: string, inTrack: string): Candidate[] {
    return this.data ? playable(this.data, outTrack, inTrack) : [];
  }
}

/** Playable candidates (compiled and valid) for out -> in: Claude's compositions first, then the
 *  rules baseline, each best score first. By ear, Claude's beat a rules drum bridge the critic
 *  scored 6.6 points higher (Stars Collide -> The Longest Road); the critic isn't calibrated yet. */
export function playable(t: Transitions, outTrack: string, inTrack: string): Candidate[] {
  const p = t.pairs.find((x) => x.out_track === outTrack && x.in_track === inTrack);
  return (p?.candidates ?? []).filter((c) => c.recipe && c.critic?.valid).sort(rank);
}

function rank(a: Candidate, b: Candidate): number {
  return Number(b.source === "llm") - Number(a.source === "llm") || b.critic!.score - a.critic!.score;
}

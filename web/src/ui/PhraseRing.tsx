// The beginner's anchor: where am I in the current phrase, and how long until the next
// one? Transitions almost always start on a phrase boundary, so this counts down to it.
import type { DeckSnapshot } from "../audio/engine";

export function PhraseRing({ s }: { s: DeckSnapshot }) {
  const n = s.phraseBars || 16;
  const size = 150, c = size / 2, r = 64, gap = 0.05;
  const segs = Array.from({ length: n }, (_, i) => {
    const a0 = -Math.PI / 2 + (i / n) * Math.PI * 2 + gap / 2;
    const a1 = -Math.PI / 2 + ((i + 1) / n) * Math.PI * 2 - gap / 2;
    const p = (a: number) => `${c + r * Math.cos(a)} ${c + r * Math.sin(a)}`;
    const state = !s.loaded ? "empty" : i < s.barInPhrase ? "done" : i === s.barInPhrase ? "now" : "todo";
    return (
      <path key={i} d={`M ${p(a0)} A ${r} ${r} 0 0 1 ${p(a1)}`} fill="none" strokeLinecap="butt"
        strokeWidth={state === "now" ? 14 : 10}
        stroke={state === "now" ? "var(--deck)" : state === "done" ? "color-mix(in srgb, var(--deck) 45%, transparent)" : "var(--rule)"} />
    );
  });
  const beats = Array.from({ length: 4 }, (_, i) => {
    const a = -Math.PI / 2 + (i / 4) * Math.PI * 2;
    const on = s.loaded && s.playing && !s.pending && i === s.beatInBar;
    return <circle key={i} cx={c + 44 * Math.cos(a)} cy={c + 44 * Math.sin(a)} r={on ? 5 : 3} fill={on ? "var(--ink)" : "var(--faint)"} />;
  });
  const left = Math.max(0, Math.ceil(s.barsToNextPhrase - 1e-6));
  return (
    <div className="phrase" aria-label={s.loaded ? `${left} bars to the next phrase` : "No track loaded"}>
      <svg viewBox={`0 0 ${size} ${size}`}>{segs}{beats}</svg>
      <div className="phrase-center">
        <div className="phrase-count">{s.loaded ? left : "–"}</div>
        <div className="phrase-caption">{s.loaded ? (left === 1 ? "bar to next phrase" : "bars to next phrase") : "no track"}</div>
      </div>
    </div>
  );
}

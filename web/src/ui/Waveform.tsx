// Scrolling 3-band waveform with the beat grid. Both decks use the same REAL-time window,
// so when two decks are synced their beat lines scroll at the same speed and line up.
import { useEffect, useRef } from "react";
import type { DeckId } from "../control/controls";
import { useServices } from "./context";
import { cssVar, fitCanvas } from "./canvas";

const WINDOW_REAL_S = 10;

export function Waveform({ deck }: { deck: DeckId }) {
  const { engine } = useServices();
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const d = engine.decks[deck];
      const { w, h, dpr } = fitCanvas(canvas);
      ctx.clearRect(0, 0, w, h);
      const t = d.track;
      if (!t) return;
      const deckColor = cssVar(canvas, "--deck");
      const ink = cssVar(canvas, "--ink");
      const hot = cssVar(canvas, "--hot");
      const pos = d.position();
      const span = WINDOW_REAL_S * d.anchor.rate; // track seconds visible
      const x2t = (x: number) => pos + (x / w - 0.5) * span;
      const t2x = (tt: number) => ((tt - pos) / span + 0.5) * w;
      const wf = t.waveform, pps = wf.points_per_second, mid = h / 2;

      // loop region
      const loop = d.anchor.loop;
      if (loop) {
        ctx.fillStyle = hot + "33";
        ctx.fillRect(t2x(loop.start), 0, t2x(loop.end) - t2x(loop.start), h);
      }

      // bands: draw low (deck color), mid (lighter), high (ink), mirrored around the center
      const step = Math.max(1, Math.round(dpr));
      const bands: [number[], string, number][] = [[wf.low, deckColor, 0.95], [wf.mid, ink, 0.45], [wf.high, ink, 0.8]];
      for (const [arr, color, alpha] of bands) {
        ctx.globalAlpha = alpha;
        ctx.fillStyle = color;
        for (let x = 0; x < w; x += step) {
          const i0 = Math.floor(x2t(x) * pps), i1 = Math.max(i0 + 1, Math.floor(x2t(x + step) * pps));
          if (i1 <= 0 || i0 >= arr.length) continue;
          let m = 0;
          for (let i = Math.max(0, i0); i < Math.min(arr.length, i1); i++) m = Math.max(m, arr[i]!);
          const scale = arr === wf.high ? 0.35 : arr === wf.mid ? 0.7 : 0.95;
          const hh = (m / 255) * mid * scale;
          ctx.fillRect(x, mid - hh, step, hh * 2);
        }
      }
      ctx.globalAlpha = 1;

      // beat grid: beats faint, bars brighter, phrase starts bold with a number
      const g = t.grid, a = t.analysis;
      const phraseStarts = new Map(a.phrases.map((p) => [p.start_bar, p.index]));
      const b0 = Math.ceil(g.beatAt(x2t(0))), b1 = Math.floor(g.beatAt(x2t(w)));
      ctx.font = `${600} ${12 * dpr}px "Barlow Condensed", sans-serif`;
      for (let b = b0; b <= b1; b++) {
        const x = Math.round(t2x(g.timeAtBeat(b)));
        const rel = b - a.first_downbeat_index;
        const isBar = rel % 4 === 0;
        const bar = rel / 4;
        const phrase = isBar ? phraseStarts.get(bar) : undefined;
        if (phrase !== undefined) {
          ctx.fillStyle = deckColor;
          ctx.fillRect(x - dpr, 0, 2 * dpr, h);
          ctx.fillText(`Phrase ${phrase + 1}`, x + 6 * dpr, h - 6 * dpr);
        } else {
          ctx.fillStyle = isBar ? ink + "99" : ink + "33";
          ctx.fillRect(x, isBar ? 0 : h * 0.3, Math.max(1, dpr * (isBar ? 1.5 : 1)), isBar ? h : h * 0.4);
        }
      }

      // cue marker + playhead
      const cx = t2x(d.cuePos);
      ctx.fillStyle = hot;
      ctx.beginPath(); ctx.moveTo(cx - 6 * dpr, 0); ctx.lineTo(cx + 6 * dpr, 0); ctx.lineTo(cx, 9 * dpr); ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.fillRect(w / 2 - dpr, 0, 2 * dpr, h);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [engine, deck]);

  const loaded = !!engine.decks[deck].track;
  return (
    <div className={`wave deck-${deck.toLowerCase()}`}>
      <canvas ref={ref} />
      <span className="wave-label">{deck}</span>
      {!loaded && <div className="wave-empty">Load a track to deck {deck} from the library</div>}
    </div>
  );
}

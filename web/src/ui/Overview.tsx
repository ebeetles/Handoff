// Whole-track overview: waveform, section energy strip, phrase ticks, playhead. Grab to seek.
import { useEffect, useRef } from "react";
import type { DeckId } from "../control/controls";
import { useHitTarget, useServices } from "./context";
import { cssVar, fitCanvas } from "./canvas";

export function Overview({ deck }: { deck: DeckId }) {
  const { engine } = useServices();
  const ref = useRef<HTMLCanvasElement>(null);
  const hit = useHitTarget<HTMLDivElement>({ kind: "seek", deck });

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    let raf = 0;
    let cache: { id: string; w: number; h: number; img: HTMLCanvasElement } | null = null;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const d = engine.decks[deck];
      const { w, h, dpr } = fitCanvas(canvas);
      ctx.clearRect(0, 0, w, h);
      const t = d.track;
      if (!t) { cache = null; return; }
      const a = t.analysis, dur = a.duration_s;
      if (!cache || cache.id !== a.id || cache.w !== w || cache.h !== h) {
        // Static layer, drawn once per track/size.
        const img = document.createElement("canvas");
        img.width = w; img.height = h;
        const c = img.getContext("2d")!;
        const deckColor = cssVar(canvas, "--deck"), ink = cssVar(canvas, "--ink"), rule = cssVar(canvas, "--rule");
        const wf = t.waveform, strip = 6 * dpr, wh = h - strip, mid = wh / 2;
        const per = wf.low.length / w;
        for (let x = 0; x < w; x++) {
          let lo = 0, hi = 0;
          for (let i = Math.floor(x * per); i < Math.floor((x + 1) * per); i++) { lo = Math.max(lo, wf.low[i] ?? 0); hi = Math.max(hi, wf.high[i] ?? 0); }
          c.fillStyle = deckColor; c.globalAlpha = 0.85;
          const hl = (lo / 255) * mid * 0.95; c.fillRect(x, mid - hl, 1, hl * 2);
          c.fillStyle = ink; c.globalAlpha = 0.5;
          const hh = (hi / 255) * mid * 0.5; c.fillRect(x, mid - hh, 1, hh * 2);
        }
        c.globalAlpha = 1;
        for (const s of a.sections) {
          const x0 = (s.start_s / dur) * w;
          const endBar = a.bars[s.end_bar];
          const x1 = endBar ? (endBar.start_s / dur) * w : w;
          c.fillStyle = s.energy_level === "high" ? deckColor : s.energy_level === "mid" ? ink + "88" : rule;
          c.fillRect(x0 + dpr, h - strip + dpr, x1 - x0 - 2 * dpr, strip - dpr);
        }
        c.fillStyle = ink + "aa";
        for (const p of a.phrases) c.fillRect(Math.round((p.start_s / dur) * w), 0, dpr, wh);
        cache = { id: a.id, w, h, img };
      }
      ctx.drawImage(cache.img, 0, 0);
      const hot = cssVar(canvas, "--hot");
      if (d.anchor.loop) { ctx.fillStyle = hot + "55"; ctx.fillRect((d.anchor.loop.start / dur) * w, 0, Math.max(2, ((d.anchor.loop.end - d.anchor.loop.start) / dur) * w), h); }
      ctx.fillStyle = hot; ctx.fillRect((d.cuePos / dur) * w - dpr, 0, 2 * dpr, 8 * dpr);
      ctx.fillStyle = "#fff"; ctx.fillRect((d.position() / dur) * w - dpr, 0, 2 * dpr, h);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [engine, deck]);

  return <div className="overview" ref={hit} aria-label={`Deck ${deck} track overview. Grab to seek.`}><canvas ref={ref} /></div>;
}

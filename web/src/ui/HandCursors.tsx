// Draws a cursor for every non-mouse pointer (hands, Chunk 5). Empty until a hand adapter exists.
import { useEffect, useState } from "react";
import { useServices } from "./context";

export function HandCursors() {
  const { gestures } = useServices();
  const [pts, setPts] = useState<{ id: string; x: number; y: number; down: boolean }[]>([]);
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      const hands = [...gestures.pointers.values()].filter((p) => p.source === "hand");
      setPts((prev) => (prev.length === 0 && hands.length === 0 ? prev : hands.map(({ id, x, y, down }) => ({ id, x, y, down }))));
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [gestures]);
  return <>{pts.map((p) => <div key={p.id} className={`hand-cursor ${p.down ? "down" : ""}`} style={{ left: p.x, top: p.y }} />)}</>;
}

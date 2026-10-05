// Draws a cursor for every hand pointer: a pinch grabs whatever is under it. Near a knob the
// cursor sits on the knob (GestureController.cursor): that's what a pinch will grab and turn.
import { useEffect, useState } from "react";
import { useServices } from "./context";

export function HandCursors() {
  const { gestures } = useServices();
  const [pts, setPts] = useState<{ id: string; x: number; y: number; down: boolean; locked: boolean }[]>([]);
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      const hands = [...gestures.pointers.values()].filter((p) => p.source === "hand");
      setPts((prev) => (prev.length === 0 && hands.length === 0 ? prev : hands.map(({ id, x, y, down }) => {
        const c = gestures.cursor(id);
        return { id, down, x: c?.x ?? x, y: c?.y ?? y, locked: c?.locked ?? false };
      })));
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [gestures]);
  return <>{pts.map((p) => <div key={p.id} className={`hand-cursor${p.down ? " down" : ""}${p.locked ? " locked" : ""}`} style={{ left: p.x, top: p.y }} />)}</>;
}

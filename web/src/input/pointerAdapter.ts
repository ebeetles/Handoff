// Mouse/touch/pen -> GestureController. The hand-tracking adapter (Chunk 5) will be a
// sibling of this file that emits the same PointerSample objects from MediaPipe landmarks.
import type { GestureController, PointerSource } from "./gesture";

/** `claims(target)`: whether a press here belongs to the board (default: anywhere in root).
 *  Presses elsewhere (ordinary buttons, the transition picker) are left to the browser, and
 *  the board ignores those pointers until they're released. */
export function attachPointerAdapter(root: HTMLElement, gestures: GestureController, onFirstGesture: () => void,
                                     claims: (target: EventTarget | null) => boolean = () => true): () => void {
  const ignored = new Set<number>();
  const sample = (e: PointerEvent, down: boolean) => {
    const source: PointerSource = e.pointerType === "touch" ? "touch" : "mouse";
    gestures.pointer({ id: `${source}-${e.pointerId}`, x: e.clientX, y: e.clientY, down, source });
  };
  const onDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    if (!claims(e.target)) { ignored.add(e.pointerId); return; }
    onFirstGesture();
    root.setPointerCapture(e.pointerId); // keep receiving moves while dragging off the control
    e.preventDefault();
    sample(e, true);
  };
  const onMove = (e: PointerEvent) => { if (!ignored.has(e.pointerId)) sample(e, e.buttons === 1 || e.pointerType === "touch" && e.pressure > 0); };
  const onUp = (e: PointerEvent) => { if (!ignored.delete(e.pointerId)) sample(e, false); };
  const onCancel = (e: PointerEvent) => { ignored.delete(e.pointerId); gestures.lost(`${e.pointerType === "touch" ? "touch" : "mouse"}-${e.pointerId}`); };
  root.addEventListener("pointerdown", onDown);
  root.addEventListener("pointermove", onMove);
  root.addEventListener("pointerup", onUp);
  root.addEventListener("pointercancel", onCancel);
  return () => {
    root.removeEventListener("pointerdown", onDown);
    root.removeEventListener("pointermove", onMove);
    root.removeEventListener("pointerup", onUp);
    root.removeEventListener("pointercancel", onCancel);
  };
}

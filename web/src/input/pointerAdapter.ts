// Mouse/touch/pen -> GestureController. The hand-tracking adapter (Chunk 5) will be a
// sibling of this file that emits the same PointerSample objects from MediaPipe landmarks.
import type { GestureController, PointerSource } from "./gesture";

export function attachPointerAdapter(root: HTMLElement, gestures: GestureController, onFirstGesture: () => void): () => void {
  const sample = (e: PointerEvent, down: boolean) => {
    const source: PointerSource = e.pointerType === "touch" ? "touch" : "mouse";
    gestures.pointer({ id: `${source}-${e.pointerId}`, x: e.clientX, y: e.clientY, down, source });
  };
  const onDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    onFirstGesture();
    root.setPointerCapture(e.pointerId); // keep receiving moves while dragging off the control
    e.preventDefault();
    sample(e, true);
  };
  const onMove = (e: PointerEvent) => sample(e, e.buttons === 1 || e.pointerType === "touch" && e.pressure > 0);
  const onUp = (e: PointerEvent) => sample(e, false);
  const onCancel = (e: PointerEvent) => gestures.lost(`${e.pointerType === "touch" ? "touch" : "mouse"}-${e.pointerId}`);
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

// Keyboard shortcuts: a fast way to test the engine without hands or mouse precision.
import type { Command } from "../control/commands";
import type { ControlStore } from "../control/controls";

export const SHORTCUTS: { keys: string; does: string }[] = [
  { keys: "Q / P", does: "Play or pause deck A / B" },
  { keys: "W / O", does: "Cue deck A / B" },
  { keys: "S / L", does: "Sync deck A / B to the other deck" },
  { keys: "1-4 / 7-0", does: "Loop 1, 2, 4, 8 beats on A / B" },
  { keys: "← / →", does: "Nudge the crossfader" },
  { keys: "E D / I K", does: "Key up / down on deck A / B"}
];

export function attachKeyboard(dispatch: (c: Command) => void, store: ControlStore, onFirstGesture: () => void): () => void {
  const loopKeys: Record<string, [ "A" | "B", number ]> = {
    "1": ["A", 1], "2": ["A", 2], "3": ["A", 4], "4": ["A", 8],
    "7": ["B", 1], "8": ["B", 2], "9": ["B", 4], "0": ["B", 8],
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.repeat || e.metaKey || e.ctrlKey || (e.target as HTMLElement)?.closest("input, textarea, select")) return;
    const k = e.key.toLowerCase();
    const cmd: Command | null =
      k === "q" ? { type: "togglePlay", deck: "A" } :
      k === "p" ? { type: "togglePlay", deck: "B" } :
      k === "w" ? { type: "cue", deck: "A" } :
      k === "o" ? { type: "cue", deck: "B" } :
      k === "s" ? { type: "sync", deck: "A" } :
      k === "l" ? { type: "sync", deck: "B" } :
      k === "e" ? { type: "keyShift", deck: "A", semitones: 1 } :
      k === "d" ? { type: "keyShift", deck: "A", semitones: -1 } :
      k === "i" ? { type: "keyShift", deck: "B", semitones: 1 } :
      k === "k" ? { type: "keyShift", deck: "B", semitones: -1 } :      
      loopKeys[k] ? { type: "loop", deck: loopKeys[k]![0], beats: loopKeys[k]![1] } : null;
    if (cmd) { onFirstGesture(); dispatch(cmd); e.preventDefault(); return; }
    if (k === "arrowleft" || k === "arrowright") {
      store.set("xfader", store.get("xfader") + (k === "arrowleft" ? -0.05 : 0.05), "keyboard");
      e.preventDefault();
    }
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}

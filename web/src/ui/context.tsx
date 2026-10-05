import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { AudioEngine, DeckSnapshot } from "../audio/engine";
import type { ControlId, ControlStore, DeckId } from "../control/controls";
import type { CommandBus } from "../control/commands";
import type { GestureController, TargetSpec } from "../input/gesture";
import type { HandAdapter } from "../input/handAdapter";

export interface Services {
  engine: AudioEngine;
  store: ControlStore;
  bus: CommandBus;
  gestures: GestureController;
  hands: HandAdapter;
}

export const ServicesContext = createContext<Services | null>(null);

export function useServices(): Services {
  const s = useContext(ServicesContext);
  if (!s) throw new Error("ServicesContext missing");
  return s;
}

/** Subscribe to one control value. Re-renders only this component when it changes. */
export function useControl(id: ControlId): number {
  const { store } = useServices();
  return useSyncExternalStore(
    useCallback((cb) => store.subscribe(id, cb), [store, id]),
    () => store.get(id),
  );
}

/** Register an element as a gesture target. Returns a ref callback. */
export function useHitTarget<T extends HTMLElement>(spec: TargetSpec) {
  const { gestures } = useServices();
  const specRef = useRef(spec);
  specRef.current = spec;
  const cleanup = useRef<(() => void) | null>(null);
  return useCallback((el: T | null) => {
    cleanup.current?.();
    cleanup.current = null;
    if (el) {
      // Register a live proxy so changing props (e.g. a pad's command) need no re-register.
      const live = new Proxy({} as TargetSpec, { get: (_t, k) => specRef.current[k as keyof TargetSpec] });
      cleanup.current = gestures.register(el, live);
    }
  }, [gestures]);
}

/** Engine snapshots for both decks, refreshed ~20x/s. Canvases read the engine directly at 60 fps. */
export function useSnapshots(): Record<DeckId, DeckSnapshot> & { notice: string | null } {
  const { engine } = useServices();
  const read = () => ({ A: engine.snapshot("A"), B: engine.snapshot("B"), notice: engine.notice() });
  const [snap, setSnap] = useState(read);
  useEffect(() => {
    let raf = 0, last = 0;
    const loop = (t: number) => {
      if (t - last > 50) { last = t; setSnap(read()); }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [engine]);
  return snap;
}

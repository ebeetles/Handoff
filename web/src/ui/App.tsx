import { useEffect, useRef, useState } from "react";
import { AudioEngine } from "../audio/engine";
import { CommandBus } from "../control/commands";
import { ControlStore } from "../control/controls";
import { GestureController } from "../input/gesture";
import { attachKeyboard } from "../input/keyboard";
import { attachPointerAdapter } from "../input/pointerAdapter";
import { Knob, ToggleChip } from "./Controls";
import { ServicesContext, useServices, useSnapshots, type Services } from "./context";
import { DeckPanel } from "./DeckPanel";
import { HandCursors } from "./HandCursors";
import { Library } from "./Library";
import { Mixer } from "./Mixer";
import { Waveform } from "./Waveform";

const LIBRARY_BASE = `${import.meta.env.BASE_URL}library`;

// Module-level singleton, NOT useMemo: StrictMode double-invokes memo callbacks in dev,
// which would open a second AudioContext (browsers cap how many can exist).
let services: Services | null = null;
function getServices(): Services {
  if (!services) {
    const store = new ControlStore();
    const bus = new CommandBus();
    const engine = new AudioEngine(store, bus, LIBRARY_BASE);
    const gestures = new GestureController(store, bus.dispatch);
    services = { store, bus, engine, gestures };
    // Dev-only debug handle for browser tests and poking at state from the console.
    if (import.meta.env.DEV) (window as unknown as { __handoff: Services }).__handoff = services;
  }
  return services;
}

export function App() {
  const services = getServices();
  return (
    <ServicesContext.Provider value={services}>
      <Booth />
    </ServicesContext.Provider>
  );
}

function Booth() {
  const { engine, gestures, store, bus } = useServices();
  const snap = useSnapshots();
  const [libOpen, setLibOpen] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const resume = () => engine.resume();
    const detachPointer = attachPointerAdapter(boardRef.current!, gestures, resume);
    const detachKeys = attachKeyboard(bus.dispatch, store, resume);
    return () => { detachPointer(); detachKeys(); };
  }, [engine, gestures, store, bus]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="wordmark">Handoff<span>DJ board</span></div>
        <div className="notice" role="status">{snap.notice}</div>
        <div className="topbar-right">
          <button className="text-button" onClick={() => { engine.resume(); setLibOpen(true); }}>Library</button>
        </div>
      </header>
      <div className="waves">
        <Waveform deck="A" />
        <Waveform deck="B" />
      </div>
      <div className="board" ref={boardRef}>
        <DeckPanel deck="A" s={snap.A} />
        <div style={{ display: "grid", gridTemplateRows: "auto 1fr", gap: 12, minHeight: 0 }}>
          <div className="panel" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 14px" }}>
            <ToggleChip id="quantize" label="Snap to beat" />
            <Knob id="master" label="Master" size={52} />
          </div>
          <Mixer a={snap.A} b={snap.B} />
        </div>
        <DeckPanel deck="B" s={snap.B} />
      </div>
      <HandCursors />
      {libOpen && <Library base={LIBRARY_BASE} onClose={() => setLibOpen(false)} />}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { AudioEngine } from "../audio/engine";
import { CommandBus } from "../control/commands";
import { ControlStore } from "../control/controls";
import { GestureController } from "../input/gesture";
import { HandAdapter } from "../input/handAdapter";
import { attachKeyboard } from "../input/keyboard";
import { attachPointerAdapter } from "../input/pointerAdapter";
import { CameraPanel, CameraToggle } from "./CameraPanel";
import { Knob, ToggleChip } from "./Controls";
import { ServicesContext, useServices, useSnapshots, type Services } from "./context";
import { DeckPanel } from "./DeckPanel";
import { HandCursors } from "./HandCursors";
import { Library } from "./Library";
import { Mixer } from "./Mixer";
import { Waveform } from "./Waveform";

const LIBRARY_BASE = `${import.meta.env.BASE_URL}library`;
const HAND_MODEL_URL = `${import.meta.env.BASE_URL}models/hand_landmarker.task`;

// Module-level singleton, NOT useMemo: StrictMode double-invokes memo callbacks in dev,
// which would open a second AudioContext (browsers cap how many can exist).
let services: Services | null = null;
function getServices(): Services {
  if (!services) {
    const store = new ControlStore();
    const bus = new CommandBus();
    const engine = new AudioEngine(store, bus, LIBRARY_BASE);
    const gestures = new GestureController(store, bus.dispatch);
    const hands = new HandAdapter(gestures, HAND_MODEL_URL);
    services = { store, bus, engine, gestures, hands };
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
          <ToggleChip id="quantize" label="Snap to beat" />
          <Knob id="master" label="Master" size={40} />
          <CameraToggle />
          <button className="text-button" onClick={() => { engine.resume(); setLibOpen(true); }}>Library</button>
        </div>
      </header>
      <div className="waves">
        <Waveform deck="A" />
        <Waveform deck="B" />
      </div>
      <div className="board" ref={boardRef}>
        <DeckPanel deck="A" s={snap.A} />
        <Mixer a={snap.A} b={snap.B} />
        <DeckPanel deck="B" s={snap.B} />
      </div>
      <HandCursors />
      <CameraPanel />
      {libOpen && <Library base={LIBRARY_BASE} onClose={() => setLibOpen(false)} />}
    </div>
  );
}

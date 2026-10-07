import { useEffect, useRef, useState, type CSSProperties } from "react";
import { AudioEngine } from "../audio/engine";
import { CommandBus } from "../control/commands";
import { ControlStore } from "../control/controls";
import { GestureController } from "../input/gesture";
import { HandAdapter } from "../input/handAdapter";
import { AutomationPlayer } from "../coDJ/automation";
import { TransitionLibrary } from "../coDJ/transitions";
import { attachKeyboard } from "../input/keyboard";
import { attachPointerAdapter } from "../input/pointerAdapter";
import { CameraPanel, CameraToggle } from "./CameraPanel";
import { Knob, ToggleChip } from "./Controls";
import { ServicesContext, useServices, useSnapshots, type Services } from "./context";
import { DeckPanel } from "./DeckPanel";
import { HandCursors } from "./HandCursors";
import { Library } from "./Library";
import { TransitionControls } from "./TransitionControls";
import { Mixer } from "./Mixer";
import { Waveform } from "./Waveform";
import { AccessGate } from "./AccessGate";
import { HOSTED, hostedLibrary, localLibrary } from "../hosting";

// Local: files under public/library. Hosted: signed links from the backend (hosting.ts).
const LIBRARY = HOSTED ? hostedLibrary() : localLibrary(`${import.meta.env.BASE_URL}library`);
const HAND_MODEL_URL = `${import.meta.env.BASE_URL}models/hand_landmarker.task`;

// Module-level singleton, NOT useMemo: StrictMode double-invokes memo callbacks in dev,
// which would open a second AudioContext (browsers cap how many can exist).
let services: Services | null = null;
function getServices(): Services {
  if (!services) {
    const store = new ControlStore();
    const bus = new CommandBus();
    const engine = new AudioEngine(store, bus, LIBRARY);
    const gestures = new GestureController(store, bus.dispatch);
    const hands = new HandAdapter(gestures, HAND_MODEL_URL);
    const coDJ = new AutomationPlayer(engine, store, bus.dispatch);
    // Hosted, the server keeps no compositions: keep them in this browser.
    const transitions = new TransitionLibrary(LIBRARY, HOSTED ? "handoff.compositions" : null);
    void transitions.load();
    services = { store, bus, engine, gestures, hands, coDJ, transitions };
    // Dev-only debug handle for browser tests and poking at state from the console.
    if (import.meta.env.DEV) (window as unknown as { __handoff: Services }).__handoff = services;
  }
  return services;
}

export function App() {
  return <AccessGate><Board /></AccessGate>;
}

function Board() {
  const services = getServices();   // created only once the access gate (hosted) lets us in
  return (
    <ServicesContext.Provider value={services}>
      <Booth />
    </ServicesContext.Provider>
  );
}

function Booth() {
  const [keyStatus, setKeyStatus] = useState<"starting" | "ready" | "unavailable">("starting");
  useEffect(() => { void getServices().engine.keyLockStatus().then(setKeyStatus); }, []);
  const { engine, gestures, store, bus } = useServices();
  const snap = useSnapshots();
  const [libOpen, setLibOpen] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<HTMLDivElement>(null);
  const fit = useFitToWindow();

  useEffect(() => {
    const resume = () => engine.resume();
    // The whole app listens, so the top bar's chips and Master knob work too; a press belongs
    // to the board only on the board itself or on a control (other buttons stay native).
    const detachPointer = attachPointerAdapter(appRef.current!, gestures, resume,
      (t) => (t instanceof Node && boardRef.current!.contains(t)) || gestures.isTarget(t));
    const detachKeys = attachKeyboard(bus.dispatch, store, resume);
    return () => { detachPointer(); detachKeys(); };
  }, [engine, gestures, store, bus]);

  return (
    <div className="app" ref={appRef}>
      <div className="stage" style={fit}>
      <header className="topbar">
        <div className="wordmark"><span className="logo" aria-hidden /> Handoff<span className="wordmark-sub">DJ board</span></div>
        <div className="topbar-center">
          <TransitionControls />
          <div className="notice" role="status">{snap.notice}</div>
        </div>
        <div className="topbar-right">
          <div className="topbar-group" role="group" aria-label="Settings">
            <ToggleChip id="quantize" label="Snap to beat" />
            <ToggleChip id="keyLock" label={keyStatus === "unavailable" ? "Key lock (unavailable)" : "Key lock"} status={keyStatus}
              title="Tempo changes keep each deck's key; the − / + by a deck's key shift it by semitones. Adds 80 ms of latency." />
          </div>
          <Knob id="master" label="Master" size={40} />
          <div className="topbar-group">
            <CameraToggle />
            <button className="text-button" onClick={() => { engine.resume(); setLibOpen(true); }}>Library</button>
          </div>
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
      </div>
      <HandCursors />
      <CameraPanel />
      {libOpen && <Library onClose={() => setLibOpen(false)} />}
    </div>
  );
}

/** Below this size the board is scaled down as a whole instead of re-flowed, so every control
 *  keeps its place (hands learn where things are). 1280 x 800 is the smallest size the layout
 *  fits at (compact mode below 860 px tall). Hit-testing uses on-screen rects, so a transform
 *  is invisible to the mouse and the hands. */
const FIT = { width: 1280, height: 800 };

function useFitToWindow(): CSSProperties | undefined {
  const measure = () => Math.min(1, window.innerWidth / FIT.width, window.innerHeight / FIT.height);
  const [scale, setScale] = useState(measure);
  useEffect(() => {
    const onResize = () => setScale(measure());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  if (scale >= 1) return undefined;
  return { width: `${100 / scale}vw`, height: `${100 / scale}vh`, transform: `scale(${scale})`, transformOrigin: "0 0" };
}

// Camera toggle (top bar) and the floating preview: mirrored webcam with landmarks, status,
// errors, and the numbers Chunk 5's definition of done asks us to measure. Status comes from
// the adapter's (rare) state changes; the stats readout polls at 4 Hz. Nothing here runs per
// video frame.
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { HandAdapter } from "../input/handAdapter";
import { PINCH_DOWN_RANGE } from "../input/handTracking";
import { useServices } from "./context";

const KNOB_TRAVEL_PX = 160;   // GestureController's minimum travel for vertical controls
const PINCH_KEY = "handoff.pinchDown";   // per-viewer convenience; the default applies if absent

function loadPinch(): number | null {
  try {
    const v = Number(localStorage.getItem(PINCH_KEY));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

function useCameraStatus(hands: HandAdapter) {
  return useSyncExternalStore((cb) => hands.subscribe(cb), () => hands.status());
}

export function CameraToggle() {
  const { hands, engine } = useServices();
  const st = useCameraStatus(hands);
  const on = st.state === "running" || st.state === "starting";
  return (
    <button
      className="text-button"
      aria-pressed={on}
      onClick={() => { engine.resume(); if (on) hands.stop(); else void hands.start(); }}
    >
      {st.state === "starting" ? "Camera…" : on ? "Camera on" : "Camera"}
    </button>
  );
}

export function CameraPanel() {
  const { hands } = useServices();
  const st = useCameraStatus(hands);
  const box = useRef<HTMLDivElement>(null);
  const [stats, setStats] = useState(() => hands.stats());
  const [recording, setRecording] = useState(false);
  const [pinchDown, setPinchDown] = useState(() => hands.pinchDown());

  useEffect(() => {
    const saved = loadPinch();
    if (saved !== null) { hands.setPinchDown(saved); setPinchDown(hands.pinchDown()); }
  }, [hands]);
  const changePinch = (v: number) => {
    hands.setPinchDown(v);
    setPinchDown(hands.pinchDown());
    try { localStorage.setItem(PINCH_KEY, String(hands.pinchDown())); } catch { /* storage blocked: keep for this session */ }
  };

  const record = async () => {
    setRecording(true);
    try {
      const rec = await hands.record(10_000);
      const url = URL.createObjectURL(new Blob([JSON.stringify(rec)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `hand-recording-${rec.createdAt.replace(/[:.]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setRecording(false);
    }
  };

  useEffect(() => hands.mountPreview(box.current!), [hands]);
  useEffect(() => {
    if (st.state !== "running") return;
    const id = setInterval(() => setStats(hands.stats()), 250);
    return () => clearInterval(id);
  }, [hands, st.state]);

  const visible = st.state !== "off";
  const { inference: inf, hands: h } = stats;
  const drift = Object.entries(h.holdDriftPxPerS)
    .map(([id, px]) => `${id.slice(5)} ${((100 * px) / KNOB_TRAVEL_PX).toFixed(2)}%/s`).join(" · ");
  const pinch = Object.entries(h.pinch).map(([id, r]) => `${id.slice(5)} ${r.toFixed(2)}`).join(" · ");

  return (
    <div className="hand-panel" hidden={!visible}>
      <div className="hand-preview" ref={box} />
      {st.state === "starting" && <div className="hand-status">Starting camera and hand model…</div>}
      {st.state === "error" && <div className="hand-status error" role="alert">{st.message}</div>}
      {st.state === "running" && (
        <dl className="hand-stats" data-testid="hand-stats">
          <dt>Model</dt><dd>{inf.meanMs.toFixed(1)} ms avg, {inf.p95Ms.toFixed(1)} p95 ({st.delegate}), {inf.fps} fps</dd>
          <dt>Hands</dt><dd>{h.handsVisible}{pinch && ` · pinch ${pinch} (grab < ${pinchDown.toFixed(2)})`}</dd>
          <dt>Hold drift</dt><dd>{drift || "pinch a knob and hold still"}</dd>
          <dt>Dropped</dt><dd>{h.droppedGrabs} grabs, {h.pinchFlickers} flickers</dd>
        </dl>
      )}
      {st.state === "running" && (
        <label className="hand-pinch">
          <span>Pinch</span>
          <small>strict</small>
          <input type="range" min={PINCH_DOWN_RANGE.min} max={PINCH_DOWN_RANGE.max} step={0.01} value={pinchDown}
                 onChange={(e) => changePinch(Number(e.target.value))} aria-label="Pinch sensitivity" />
          <small>easy</small>
        </label>
      )}
      {st.state === "running" && (
        <button className="text-button hand-record" onClick={() => void record()} disabled={recording}>
          {recording ? "Recording 10 s…" : "Record 10 s of landmarks"}
        </button>
      )}
    </div>
  );
}

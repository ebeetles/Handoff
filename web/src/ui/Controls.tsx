// Board primitives. None of these handle input themselves: they register a hit target
// with the GestureController (useHitTarget) and render from the ControlStore.
import type { ReactNode } from "react";
import type { Command } from "../control/commands";
import type { ControlId } from "../control/controls";
import { useControl, useHitTarget } from "./context";

const TAU = Math.PI * 2;
const SWEEP = 0.8 * TAU; // knob travel: 288 degrees
const START = Math.PI / 2 + (TAU - SWEEP) / 2; // gap at the bottom

function arc(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const p = (a: number) => `${cx + r * Math.cos(a)} ${cy + r * Math.sin(a)}`;
  const large = Math.abs(a1 - a0) > Math.PI ? 1 : 0;
  const sweep = a1 > a0 ? 1 : 0;
  return `M ${p(a0)} A ${r} ${r} 0 ${large} ${sweep} ${p(a1)}`;
}

export function Knob({ id, label, size = 66, bipolar = false }: { id: ControlId; label: string; size?: number; bipolar?: boolean }) {
  const v = useControl(id);
  // Mouse: drag vertically. Hand: pinch and twist.
  const ref = useHitTarget<HTMLDivElement>({ kind: "continuous", control: id, axis: "y", twist: true });
  const c = size / 2, r = size / 2 - 7;
  const aV = START + v * SWEEP;
  const aFrom = bipolar ? START + 0.5 * SWEEP : START;
  const atRest = bipolar && Math.abs(v - 0.5) < 0.005;
  return (
    <div className="knob" ref={ref} role="slider" aria-label={label} aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <path d={arc(c, c, r, START, START + SWEEP)} stroke="var(--rule)" strokeWidth={6} fill="none" strokeLinecap="round" />
        {!atRest && <path d={arc(c, c, r, Math.min(aFrom, aV), Math.max(aFrom, aV))} stroke="var(--deck, var(--ink))" strokeWidth={6} fill="none" strokeLinecap="round" />}
        <circle cx={c} cy={c} r={r - 9} fill="var(--panel-2)" />
        <line x1={c + (r - 20) * Math.cos(aV)} y1={c + (r - 20) * Math.sin(aV)} x2={c + (r - 10) * Math.cos(aV)} y2={c + (r - 10) * Math.sin(aV)}
          stroke="var(--ink)" strokeWidth={3} strokeLinecap="round" />
      </svg>
      <span className="knob-label">{label}</span>
    </div>
  );
}

export function Fader({ id, label, readout }: { id: ControlId; label: string; readout?: (v: number) => string }) {
  const v = useControl(id);
  const ref = useHitTarget<HTMLDivElement>({ kind: "continuous", control: id, axis: "y" });
  return (
    <div className="fader" ref={ref} role="slider" aria-label={label} aria-valuenow={Math.round(v * 100)} style={{ height: "100%" }}>
      <div className="fader-track">
        <div className="fader-fill" style={{ top: `calc(10px + (100% - 20px) * ${1 - v})`, bottom: 10 }} />
        <div className="fader-thumb" style={{ top: `calc(11px + (100% - 22px) * ${1 - v})` }} />
      </div>
      <span className="fader-label">{readout ? readout(v) : label}</span>
    </div>
  );
}

export function Crossfader() {
  const v = useControl("xfader");
  const ref = useHitTarget<HTMLDivElement>({ kind: "continuous", control: "xfader", axis: "x" });
  return (
    <div className="xfader" ref={ref} role="slider" aria-label="Crossfader" aria-valuenow={Math.round(v * 100)}>
      <div className="xfader-track">
        <div className="xfader-thumb" style={{ left: `calc(20px + (100% - 40px) * ${v})` }} />
      </div>
      <div className="xfader-label"><span style={{ color: "var(--a)" }}>A</span><span>Crossfader</span><span style={{ color: "var(--b)" }}>B</span></div>
    </div>
  );
}

export function Pad({ command, children, active, pending, disabled, small, wide }: {
  command: Command; children: ReactNode; active?: boolean; pending?: boolean; disabled?: boolean; small?: boolean; wide?: boolean;
}) {
  const ref = useHitTarget<HTMLDivElement>({ kind: "pad", command });
  const cls = ["pad", small && "small", wide && "wide", active && "active", pending && "pending", disabled && "disabled"].filter(Boolean).join(" ");
  return <div className={cls} ref={ref} role="button" aria-pressed={active}>{children}</div>;
}

export function StemTile({ id, label, disabled }: { id: ControlId; label: string; disabled?: boolean }) {
  const v = useControl(id);
  const ref = useHitTarget<HTMLDivElement>({ kind: "toggle", control: id });
  const on = v > 0.5;
  return (
    <div className={`pad stem ${disabled ? "disabled" : on ? "on" : "off"}`} ref={ref} role="switch" aria-checked={on} aria-label={label}>
      {label}
    </div>
  );
}

export function ToggleChip({ id, label }: { id: ControlId; label: string }) {
  const v = useControl(id);
  const ref = useHitTarget<HTMLDivElement>({ kind: "toggle", control: id });
  return <div className={`chip ${v > 0.5 ? "on" : ""}`} ref={ref} role="switch" aria-checked={v > 0.5}><span className="dot" />{label}</div>;
}

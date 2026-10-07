// Knobs click into place at their default, like a real mixer: a small dead zone around the
// default while dragging or twisting, without a jump anywhere and with the ends still reachable.
import { describe, expect, it } from "vitest";
import { ControlStore } from "../control/controls";
import { DETENT, detentFromRaw, detentToRaw, GestureController } from "./gesture";

function fakeEl(left: number, top: number, w: number, h: number): HTMLElement {
  const rect = { left, top, width: w, height: h, right: left + w, bottom: top + h, x: left, y: top };
  return { getBoundingClientRect: () => rect, dataset: {} } as unknown as HTMLElement;
}

describe("knob detent", () => {
  it("maps continuously, sticks at the default, reaches both ends", () => {
    for (const d of [0.5, 0.8]) {
      expect(detentFromRaw(0, d)).toBe(0);
      expect(detentFromRaw(1, d)).toBe(1);
      expect(detentFromRaw(d, d)).toBe(d);
      expect(detentFromRaw(d + DETENT * 0.9, d)).toBe(d);
      expect(detentFromRaw(d - DETENT * 0.9, d)).toBe(d);
      for (let r = 0; r <= 1; r += 0.001) {   // no jumps
        expect(Math.abs(detentFromRaw(r + 0.001, d) - detentFromRaw(r, d))).toBeLessThan(0.003);
      }
      for (const v of [0, 0.2, d, 0.9, 1]) expect(detentFromRaw(detentToRaw(v, d), d)).toBeCloseTo(v, 9);
    }
  });

  it("a dragged knob passes through center with a click, and leaves it after a few pixels", () => {
    const store = new ControlStore();
    const g = new GestureController(store, () => {});
    g.register(fakeEl(0, 0, 60, 60), { kind: "continuous", control: "A.eqMid", axis: "y", detent: 0.5 });
    store.set("A.eqMid", 0.4, "mouse");
    g.pointer({ id: "m", x: 30, y: 30, down: true, source: "mouse" });
    const at = (dy: number) => { g.pointer({ id: "m", x: 30, y: 30 - dy, down: true, source: "mouse" }); return store.get("A.eqMid"); };
    const values = Array.from({ length: 60 }, (_, i) => at(i));
    const stuck = values.filter((v) => v === 0.5).length;
    expect(stuck).toBeGreaterThanOrEqual(10);            // ~12 px of travel sit exactly on the default
    expect(values.at(-1)!).toBeGreaterThan(0.6);         // and it carries on past it
    g.pointer({ id: "m", x: 30, y: 30 - 59, down: false, source: "mouse" });
  });

  it("twisting with a hand clicks into the default too", () => {
    const store = new ControlStore();
    const g = new GestureController(store, () => {});
    g.register(fakeEl(0, 0, 60, 60), { kind: "continuous", control: "A.filter", axis: "y", twist: true, detent: 0.5 });
    store.set("A.filter", 0.47, "hand");
    g.pointer({ id: "h", x: 30, y: 30, down: true, source: "hand", angle: 0 });
    g.pointer({ id: "h", x: 30, y: 30, down: true, source: "hand", angle: 0.1 });    // ~6 degrees
    expect(store.get("A.filter")).toBe(0.5);
  });

  it("without a detent nothing changes", () => {
    const store = new ControlStore();
    const g = new GestureController(store, () => {});
    g.register(fakeEl(0, 0, 44, 160), { kind: "continuous", control: "A.volume", axis: "y" });
    store.set("A.volume", 0.5, "mouse");
    g.pointer({ id: "m", x: 20, y: 80, down: true, source: "mouse" });
    g.pointer({ id: "m", x: 20, y: 79, down: true, source: "mouse" });
    expect(store.get("A.volume")).toBeCloseTo(0.5 + 1 / 160, 9);
  });
});

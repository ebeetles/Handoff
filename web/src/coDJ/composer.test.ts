import { localLibrary } from "../hosting";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../contracts/fixtures/sample_transitions.json";
import type { ComposeRequest } from "../contracts/composer";
import { assertTransitions } from "../contracts/transitions";
import { composeLive } from "./composer";
import { TransitionLibrary } from "./transitions";

const pair = fixture.pairs[0]!;
const request: ComposeRequest = { schema_version: 1, out_track: pair.out_track, in_track: pair.in_track,
  out_start_bar: 16, min_start_bar: 1, max_bars: 16, candidates: 4, brief: "A chopped percussion relay" };
afterEach(() => vi.unstubAllGlobals());

describe("live composer", () => {
  it("sends the requested pair and phrase and validates the reply", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const reply = await composeLive(request);
    expect(reply.pairs[0]!.out_track).toBe(request.out_track);
    expect(JSON.parse(fetch.mock.calls[0]![1].body as string)).toEqual(request);
  });
  it("reports missing server and missing credentials without calling playback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unavailable", { status: 502 })));
    await expect(composeLive(request)).rejects.toThrow(/Start the local composer server/);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Set ANTHROPIC_API_KEY in pipeline/.env" }),
      { status: 503, headers: { "content-type": "application/json" } })));
    await expect(composeLive(request)).rejects.toThrow(/Set ANTHROPIC_API_KEY/);
  });
  it("rejects responses for a different pair", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture), { headers: { "content-type": "application/json" } })));
    await expect(composeLive({ ...request, out_track: "different" })).rejects.toThrow(/different pair/);
  });
  it("merges new exits without duplicating candidates or losing prior plans", () => {
    assertTransitions(fixture);
    const lib = new TransitionLibrary(localLibrary("library"));
    lib.merge(fixture);
    const count = lib.composedFor(pair.out_track, pair.in_track).length;
    const second = structuredClone(fixture);
    second.pairs[0]!.candidates[0]!.id = "new_exit";
    lib.merge(second);
    lib.merge(second);
    expect(lib.composedFor(pair.out_track, pair.in_track)).toHaveLength(count + 1);
  });
  it("rejects malformed rankings and wrong recipe anchors", () => {
    const invalid = structuredClone(fixture);
    invalid.pairs[0]!.best = 999;
    expect(() => assertTransitions(invalid)).toThrow(/index/);
    invalid.pairs[0]!.best = 0;
    invalid.pairs[0]!.candidates[0]!.recipe!.anchor.out_track = "wrong";
    expect(() => assertTransitions(invalid)).toThrow(/anchor/);
  });
  it("hosted: compositions made here survive a reload (kept in this browser)", async () => {
    const mem = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v), removeItem: (k: string) => void mem.delete(k) });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not found", { status: 404 })));
    assertTransitions(fixture);
    const first = new TransitionLibrary(localLibrary("library"), "handoff.compositions");
    first.merge(fixture);
    const again = new TransitionLibrary(localLibrary("library"), "handoff.compositions");
    await again.load();
    expect(again.composedFor(pair.out_track, pair.in_track).length).toBeGreaterThan(0);
    mem.set("handoff.compositions", JSON.stringify([{ schema_version: 1, pairs: [] }]));   // from an older version
    const old = new TransitionLibrary(localLibrary("library"), "handoff.compositions");
    await old.load();
    expect(old.composedFor(pair.out_track, pair.in_track)).toEqual([]);
    vi.unstubAllGlobals();
  });
});

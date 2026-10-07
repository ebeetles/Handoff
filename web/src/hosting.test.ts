// The access gate must say WHY it can't get in. A CORS mismatch (the board's address isn't in the
// backend's ALLOWED_ORIGINS) looks like a network failure to fetch; it used to be reported as
// "The server didn't start" after 2.5 minutes of retrying.
import { afterEach, describe, expect, it, vi } from "vitest";
import { checkCode } from "./hosting";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("checkCode", () => {
  it("accepts a right code and refuses a wrong one", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ ok: true })));
    expect(await checkCode("x", () => {})).toBe("ok");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ detail: "no" }, 401)));
    expect(await checkCode("x", () => {})).toBe("wrong");
  });

  it("names a blocked origin at once instead of waiting for a server that is up", async () => {
    vi.stubGlobal("location", { origin: "https://handoff-web-abc1.onrender.com" });
    const fetch = vi.fn((url: string) => url.endsWith("/api/session")
      ? Promise.reject(new TypeError("Failed to fetch"))                     // what a CORS refusal looks like
      : Promise.resolve(json({ ok: true, hosted: true, origins: ["https://handoff-web.onrender.com"] })));
    vi.stubGlobal("fetch", fetch);
    await expect(checkCode("x", () => {})).rejects.toThrow(/ALLOWED_ORIGINS.*https:\/\/handoff-web-abc1\.onrender\.com/);
  });

  it("keeps waiting while the server is unreachable (asleep), then says which address it tried", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    const waits: number[] = [];
    const p = checkCode("x", (s) => waits.push(s));
    const done = expect(p).rejects.toThrow(/Couldn't reach the server at/);
    for (let i = 0; i < 70; i++) await vi.advanceTimersByTimeAsync(3000);
    await done;
    expect(waits.length).toBeGreaterThan(10);
  });
});

"""Browser smoke test: real Chromium, real Web Audio. Asserts the things unit tests can't:
audio actually flows, quantized launch waits for the bar, sync phase-locks and doesn't drift,
loops hold, jumps keep phase, and the page logs no errors.

Setup (once):  pip install playwright && python -m playwright install chromium
Run:           (in web/) npm run dev   then, in another shell:   python e2e/smoke.py
Needs a library with at least two beatmatchable tracks (the demo tracks work).
"""
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173/"
PROBE = """() => {
  const e = window.__handoff.engine, now = e.ctx.currentTime, f = x => x - Math.floor(x), o = {};
  for (const d of ['A','B']) { const k = e.decks[d], t = k.track, pos = k.position(now);
    o[d] = { pos, bar: t.grid.barAt(pos), rate: k.anchor.rate, bpm: t.grid.bpmAt(pos) * k.anchor.rate,
             playing: k.playing, pending: k.pending, loop: k.anchor.loop, level: k.level() }; }
  let d = f(o.A.bar) - f(o.B.bar); d = f(d + 0.5) - 0.5;
  o.phaseMs = d * 4 * 60 / o.A.bpm * 1000; return o; }"""

failures = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: failures.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1500, "height": 940})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("response", lambda r: r.status >= 400 and errs.append(f"{r.status} {r.url}"))
    pg.goto(URL); pg.wait_for_timeout(1000)
    for i, deck in [(0, "A"), (1, "B")]:
        pg.click("text=Library"); pg.wait_for_timeout(300)
        pg.locator(".lib-row").nth(i).locator(f"text=Load to {deck}").click()
        pg.wait_for_function(f"() => window.__handoff.engine.decks.{deck}.track !== null", timeout=60000)
    pg.mouse.click(700, 30)
    probe = lambda: pg.evaluate(PROBE)

    pg.keyboard.press("q"); pg.wait_for_timeout(2300)
    check(probe()["A"]["level"] > 0.05, "audio flows through deck A")
    pg.keyboard.press("p"); pg.wait_for_timeout(50)
    check(probe()["B"]["pending"], "deck B waits for deck A's next bar (quantized launch)")
    pg.wait_for_timeout(2500)
    check(probe()["B"]["playing"] and not probe()["B"]["pending"], "deck B started")
    pg.keyboard.press("l"); pg.wait_for_timeout(300)
    s = probe()
    check(abs(s["A"]["bpm"] - s["B"]["bpm"]) < 0.05, f"sync matched tempo ({s['A']['bpm']:.3f} vs {s['B']['bpm']:.3f})")
    check(abs(s["phaseMs"]) < 2, f"sync aligned bar phase ({s['phaseMs']:.2f} ms)")
    pg.wait_for_timeout(8000)
    s = probe()
    check(abs(s["phaseMs"]) < 2, f"no drift after 8 s ({s['phaseMs']:.2f} ms)")
    pg.keyboard.press("3"); pg.wait_for_timeout(5000)
    s = probe(); L = s["A"]["loop"]
    check(L is not None and L["start"] <= s["A"]["pos"] < L["end"], "4-beat loop holds the playhead")
    pg.keyboard.press("3"); pg.wait_for_timeout(200)
    check(probe()["A"]["loop"] is None, "pressing the same loop size exits the loop")
    pg.evaluate("() => window.__handoff.bus.dispatch({type:'jump', deck:'A', beats: 16})"); pg.wait_for_timeout(500)
    check(abs(probe()["phaseMs"]) < 2, "a 16-beat jump keeps the decks in phase")
    pg.evaluate("() => window.__handoff.store.set('B.stem.drums', 0, 'mouse')"); pg.wait_for_timeout(500)
    check(not errs, f"no page errors or failed requests {errs or ''}")
    b.close()

sys.exit(1 if failures else 0)

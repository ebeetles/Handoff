"""Browser smoke test: real Chromium, real Web Audio. Asserts the things unit tests can't:
audio actually flows, quantized launch waits for the bar, sync phase-locks and doesn't drift,
loops hold, jumps keep phase, the hand model loads and runs on a (fake) camera, and the page
logs no errors.

Setup (once):  pip install playwright && python -m playwright install chromium
Run:           (in web/) npm run dev   then, in another shell:   python e2e/smoke.py
Needs the demo tracks (Amber Room, Teal Signal) in the library: see README.
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
    # --disable-audio-output: the real Web Audio graph renders into a fake sink. Tests then don't
    # depend on (or play through) the machine's speakers; with a busy/stuck output device the
    # AudioContext clock stalls and every timing check fails.
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-audio-output",
                                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
    pg = b.new_page(viewport={"width": 1500, "height": 940}, permissions=["camera"])
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("response", lambda r: r.status >= 400 and errs.append(f"{r.status} {r.url}"))
    pg.goto(URL); pg.wait_for_timeout(1000)
    for title, deck in [("Amber Room", "A"), ("Teal Signal", "B")]:   # the demo tracks (README)
        pg.click("text=Library"); pg.wait_for_timeout(300)
        pg.locator(".lib-row", has_text=title).locator(f"text=Load to {deck}").click()
        pg.wait_for_function(f"() => window.__handoff.engine.decks.{deck}.track !== null", timeout=60000)
    pg.mouse.click(30, 30)   # the wordmark: a spot with no control (resumes audio)
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

    # Rolls (loops under a beat) slip: letting go lands back in phase with the synced deck.
    for frac in (0.25, 0.0625):
        pg.evaluate(f"() => window.__handoff.bus.dispatch({{type:'loop', deck:'A', beats: {frac}}})"); pg.wait_for_timeout(1200)
        s = probe(); L = s["A"]["loop"]
        check(L is not None and L["beats"] == frac and L["start"] <= s["A"]["pos"] < L["end"],
              f"a 1/{round(1 / frac)} roll holds the playhead in a {1000 * (L['end'] - L['start']) if L else 0:.0f} ms loop")
        pg.evaluate(f"() => window.__handoff.bus.dispatch({{type:'loop', deck:'A', beats: {frac}}})"); pg.wait_for_timeout(400)
        s = probe()
        check(s["A"]["loop"] is None and abs(s["phaseMs"]) < 2, f"letting go of the roll lands back in phase ({s['phaseMs']:.2f} ms)")

    # Hands on the real layout: knobs lock on (and twist); buttons are exact.
    hand = lambda x, y, down, angle: pg.evaluate(
        f"() => window.__handoff.gestures.pointer({{id: 'hand-e2e', x: {x}, y: {y}, down: {str(down).lower()}, source: 'hand', angle: {angle}}})")
    knob = pg.locator(".mixer .knob").first
    kb = knob.bounding_box()
    hx, hy = kb["x"] - 25, kb["y"] + kb["height"] / 2
    v0 = int(knob.get_attribute("aria-valuenow"))
    hand(hx, hy, False, 0); pg.wait_for_timeout(100)
    check(knob.get_attribute("data-locked") == "true" and pg.locator(".hand-cursor.locked").count() == 1,
          "a hand 25 px from a knob locks onto it (outline + cursor on the knob)")
    hand(hx, hy, True, 0); hand(hx - 40, hy + 30, True, 0.5); pg.wait_for_timeout(100)
    v1 = int(knob.get_attribute("aria-valuenow"))
    check(abs((v1 - v0) - 0.5 / (150 * 3.14159265 / 180) * 100) <= 1.5, f"twisting the pinched hand turns the knob ({v0} -> {v1})")
    hand(hx - 40, hy + 30, False, 0.5)
    pg.evaluate("() => window.__handoff.gestures.lost('hand-e2e')")
    pad = pg.locator(".deck-a .pad-row").nth(2).locator(".pad").last          # Loop 8
    pb = pad.bounding_box()
    loop_before = probe()["A"]["loop"]
    hand(pb["x"] + pb["width"] + 10, pb["y"] + pb["height"] / 2, False, 0)
    hand(pb["x"] + pb["width"] + 10, pb["y"] + pb["height"] / 2, True, 0)
    hand(pb["x"] + pb["width"] + 10, pb["y"] + pb["height"] / 2, False, 0); pg.wait_for_timeout(100)
    check(probe()["A"]["loop"] == loop_before, "a pinch 10 px beside a pad does nothing (buttons are exact)")
    pg.evaluate("() => window.__handoff.gestures.lost('hand-e2e')"); pg.wait_for_timeout(100)
    check(pg.locator(".hand-cursor").count() == 0 and knob.get_attribute("data-locked") is None, "losing the hand clears its cursor and lock")

    # Hands (Chunk 5). Chromium's fake camera shows a test pattern with no hand, so this checks
    # that the self-hosted wasm + model load and inference keeps up, not tracking quality.
    hands = "window.__handoff.hands"
    pg.get_by_role("button", name="Camera").click()
    pg.wait_for_function(f"() => ['running', 'error'].includes({hands}.status().state)", timeout=60000)
    st = pg.evaluate(f"() => {hands}.status()")
    check(st["state"] == "running", f"camera and hand model start ({st})")
    if st["state"] == "running":
        pg.wait_for_timeout(4000)
        inf = pg.evaluate(f"() => {hands}.stats().inference")
        # Headless Chromium runs the model on a software GPU, so its speed follows the machine's
        # load (measured 15 fps and 3 fps a minute apart, same code). This checks that inference
        # runs; real cost is measured in a headed browser (ROADMAP Chunk 5).
        check(inf["samples"] >= 10,
              f"hand inference runs: {inf['fps']} fps, {inf['meanMs']:.1f} ms avg, {inf['p95Ms']:.1f} ms p95 "
              f"({st['delegate']}, headless, no hand in frame)")
        check(pg.locator("[data-testid=hand-stats]").is_visible(), "hand stats readout is shown")
        slider = pg.get_by_label("Pinch sensitivity")
        slider.fill("0.3"); pg.wait_for_timeout(100)
        check(abs(pg.evaluate(f"() => {hands}.pinchDown()") - 0.3) < 1e-9, "the pinch sensitivity slider sets the grab threshold")
        pg.get_by_role("button", name="Camera on").click(); pg.wait_for_timeout(200)
        check(pg.evaluate(f"() => {hands}.status().state") == "off", "camera turns off")
    check(not errs, f"no page errors or failed requests {errs or ''}")
    b.close()

sys.exit(1 if failures else 0)

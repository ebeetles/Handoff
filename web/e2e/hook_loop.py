"""Moves v4 in a real browser: loop_hold. The HTTP reply is a deterministic fixture
(contracts/fixtures/sample_hook_loop_transition.json): B comes in synced, then A (stripped to its
vocal) loops its bars 20-21 three times while its bar clock runs on, and lands back in time.
Run with Vite: pipeline/.venv/bin/python web/e2e/hook_loop.py [url]
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DOC = json.loads((ROOT / "contracts/fixtures/sample_hook_loop_transition.json").read_text())
RECIPE = DOC["pairs"][0]["candidates"][0]["recipe"]
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173/"

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-audio-output"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/composer/compose", lambda route: route.fulfill(json=DOC))
    page.goto(URL)
    for title, deck in [("Amber Room", "A"), ("Teal Signal", "B")]:
        page.get_by_role("button", name="Library", exact=True).click()
        page.locator(".lib-row", has_text=title).get_by_text(f"Load to {deck}").click()
        page.wait_for_function(f"() => window.__handoff.engine.snapshot('{deck}').loaded")
    page.get_by_role("button", name="Compose with AI", exact=True).click()
    page.get_by_label("Composer exit phrase").select_option("16")
    page.get_by_label("Composer duration").select_option("16")
    page.get_by_role("button", name="Compose transition").click()
    page.wait_for_selector("dialog", state="detached")
    assert page.get_by_label("Transition recipe").input_value() == RECIPE["id"]
    page.evaluate("""() => {
      const h = window.__handoff, a = h.engine.decks.A;
      h.bus.dispatch({type:'togglePlay', deck:'A'});
      h.bus.dispatch({type:'seekFraction', deck:'A', fraction:a.track.grid.timeAtBar(13.6)/a.track.analysis.duration_s});
    }""")
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Try transition").click()
    samples = page.evaluate("""() => new Promise((done) => {
      const h = window.__handoff, e = h.engine, out = [];
      const tick = () => {
        const s = h.coDJ.status(), now = e.ctx.currentTime, A = e.decks.A, B = e.snapshot('B');
        const heard = A.track.grid.barAt(A.position(now));             // where A's audio actually is
        let phase = null;
        if (B.playing && !B.pending && A.playing) {
          const expect = 32 + (e.barAt('A', now) - 16);                 // A's clock vs B: bar 16 <-> 32
          phase = (e.barAt('B', now) - expect) * 4 * 60 / B.bpm * 1000;
        }
        out.push({bar: s.bar, state: s.state, heard, clock: e.barAt('A', now), phase});
        if (['done', 'stopped', 'error'].includes(s.state) || out.length > 3000) done(out);
        else requestAnimationFrame(tick);
      };
      tick();
    })""")
    assert samples[-1]["state"] == "done", samples[-1]
    held = [x for x in samples if 4.1 < x["bar"] < 9.9]
    lo, hi = min(x["heard"] for x in held), max(x["heard"] for x in held)
    cycles = sum(1 for p, n in zip(held, held[1:]) if n["heard"] < p["heard"] - 1)
    print(f"held: A's audio stayed in bars {lo:.2f}-{hi:.2f} and wrapped {cycles} times, while its clock ran "
          f"{held[0]['clock']:.2f} -> {held[-1]['clock']:.2f}", flush=True)
    assert 19.99 < lo and hi < 22.01 and cycles == 2 and held[-1]["clock"] > 25.8
    after = [x for x in samples if 10.1 < x["bar"] < 11.9]
    drift = max(abs(x["heard"] - x["clock"]) * 4 * 60 / 124 * 1000 for x in after)
    worst = max(abs(x["phase"]) for x in samples if x["phase"] is not None)
    print(f"after the loop: A's audio is {drift:.2f} ms from its clock; B stays locked (worst {worst:.2f} ms)", flush=True)
    assert drift < 1.0 and worst < 1.0
    assert not errors, errors
    print("PASS loop_hold repeats A's hook in place, keeps the bar clock running, and lands back in time", flush=True)
    browser.close()

"""Moves v3 in a real browser: a tempo ride, an entry at B's own tempo, and a brake. The HTTP
reply is a deterministic fixture (contracts/fixtures/sample_ride_brake_transition.json): A
(124 BPM) strips to drums and rides up to B's 128 over 4 bars, B comes in on bar 4 at its own
tempo, in phase, and A brakes to a halt over the 2 beats before bar 8.
Run with Vite: pipeline/.venv/bin/python web/e2e/ride_brake.py [url]
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DOC = json.loads((ROOT / "contracts/fixtures/sample_ride_brake_transition.json").read_text())
RECIPE = DOC["pairs"][0]["candidates"][0]["recipe"]
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173/"
TARGET = next(l for l in RECIPE["lanes"] if l["control"] == "tempo")["points"][-1][1]

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
    page.get_by_label("Composer duration").select_option("8")
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

    # Sample every animation frame in the page: tempo, rates, phase, and A's live playback rate.
    samples = page.evaluate("""() => new Promise((done) => {
      const h = window.__handoff, e = h.engine, out = [];
      const tick = () => {
        const s = h.coDJ.status(), now = e.ctx.currentTime, A = e.snapshot('A'), B = e.snapshot('B');
        const src = [...e.decks.A.sources.values()][0];
        let phase = null;
        if (B.playing && !B.pending && A.playing) {
          const expect = 32 + (e.barAt('A', now) - 20);     // B's bar 32 lines up with A's bar 16 + 4
          phase = (e.barAt('B', now) - expect) * 4 * 60 / B.bpm * 1000;
        }
        out.push({bar: s.bar, state: s.state, tempo: h.store.get('A.tempo'), aRate: A.rate, bRate: B.rate,
                  bPlaying: B.playing && !B.pending, phase, live: src ? src.playbackRate.value : null});
        if (s.state === 'done' || s.state === 'stopped' || s.state === 'error' || out.length > 2000) done(out);
        else requestAnimationFrame(tick);
      };
      tick();
    })""")
    final = page.evaluate("() => ({s: window.__handoff.coDJ.status(), a: window.__handoff.engine.snapshot('A'), b: window.__handoff.engine.snapshot('B'), tempo: window.__handoff.store.get('A.tempo')})")
    assert final["s"]["state"] == "done", final["s"]

    held = [x["tempo"] for x in samples if -1 < x["bar"] < 0]
    mid = [x["tempo"] for x in samples if 1.9 < x["bar"] < 2.1]
    assert held and all(v == 0.5 for v in held), held[:5]
    assert mid and all(abs(v - (0.5 + TARGET) / 2) < 0.01 for v in mid), (mid, TARGET)
    entry = [x for x in samples if x["phase"] is not None]
    assert entry, "B never played alongside A"
    first = entry[0]
    print(f"ride: A tempo {first['tempo']:.4f} (target {TARGET}), A rate {first['aRate']:.4f} (128/124 = {128 / 124:.4f}), "
          f"B rate {first['bRate']:.4f}", flush=True)
    assert first["tempo"] == TARGET and abs(first["aRate"] - 128 / 124) < 1e-3 and abs(first["bRate"] - 1) < 2e-3
    worst = max(abs(x["phase"]) for x in entry if x["bar"] < 7.5)
    print(f"B in phase over {len(entry)} frames: worst {worst:.2f} ms", flush=True)
    assert worst < 1.0
    print("PASS tempo ride lands A on B's tempo; B comes in at its own tempo, in phase", flush=True)

    braking = [x["live"] for x in samples if 7.5 <= x["bar"] < 8 and x["live"] is not None]
    before = [x["live"] for x in samples if 5 <= x["bar"] < 7.4 and x["live"] is not None]
    r = first["aRate"]
    print(f"brake: A's playback rate before {min(before):.3f}-{max(before):.3f}, during {['%.2f' % v for v in braking]}", flush=True)
    assert before and all(abs(v - r) < 1e-3 for v in before)
    assert any(0.15 * r < v < 0.85 * r for v in braking) and min(braking) < 0.2 * r
    assert not final["a"]["playing"] and final["b"]["playing"] and final["tempo"] == 0.5
    assert not errors, errors
    print("PASS brake slows A to a halt before bar 8; A stopped and reset, B playing; no page errors", flush=True)
    browser.close()

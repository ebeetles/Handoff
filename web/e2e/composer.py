"""Composer UI and creative mid-track playback. The HTTP reply is a deterministic fixture;
the real provider call is tested separately (never charge money in an automatic browser test).
Run with Vite: pipeline/.venv/bin/python web/e2e/composer.py [url]
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DOC = json.loads((ROOT / "contracts/fixtures/sample_creative_transition.json").read_text())
PAIR = DOC["pairs"][0]
RECIPE = PAIR["candidates"][0]["recipe"]
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173/"

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-audio-output"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors, requests = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def reply(route):
        requests.append(route.request.post_data_json)
        route.fulfill(json=DOC)

    page.route("**/api/composer/compose", reply)
    page.goto(URL)
    for title, deck in [("Amber Room", "A"), ("Teal Signal", "B")]:
        page.get_by_role("button", name="Library", exact=True).click()
        page.locator(".lib-row", has_text=title).get_by_text(f"Load to {deck}").click()
        page.wait_for_function(f"() => window.__handoff.engine.snapshot('{deck}').loaded")
    page.get_by_role("button", name="Compose with AI", exact=True).click()
    page.get_by_label("Composer exit phrase").select_option("16")
    page.get_by_label("Composer duration").select_option("8")
    page.get_by_label("Creative direction").fill("Rhythmic percussion relay with a big reveal")
    page.screenshot(path="/tmp/handoff-composer.png")
    page.get_by_role("button", name="Compose four ideas").click()
    page.wait_for_selector("dialog", state="detached")
    assert len(requests) == 1 and requests[0]["out_start_bar"] == 16 and requests[0]["max_bars"] == 8
    assert requests[0]["out_track"] == PAIR["out_track"] and requests[0]["in_track"] == PAIR["in_track"]
    assert page.get_by_label("Transition recipe").input_value() == RECIPE["id"]
    assert not page.evaluate("() => window.__handoff.engine.snapshot('A').playing")
    print("PASS composer requests the chosen mid-track phrase and reviews without autoplay", flush=True)
    page.evaluate("""() => {
      const h = window.__handoff, a = h.engine.decks.A;
      h.bus.dispatch({type:'togglePlay', deck:'A'});
      h.bus.dispatch({type:'seekFraction', deck:'A', fraction:a.track.grid.timeAtBar(12.6)/a.track.analysis.duration_s});
    }""")
    page.get_by_role("button", name="Try transition").click()
    page.wait_for_function("() => window.__handoff.coDJ.status().state === 'done'", timeout=45000)
    state = page.evaluate("""() => {
      const h = window.__handoff, s = h.coDJ.status();
      return {s, steps:h.coDJ.stepLog, applied:h.engine.automationLog,
              a:h.engine.snapshot('A'), b:h.engine.snapshot('B'), volume:h.store.get('B.volume')};
    }""")
    assert state["s"]["startBar"] == 16 and state["s"]["recipe"]["anchor"]["in_from_bar"] == 32
    gates = [s for s in state["steps"] if s["control"] == "A.volume" and 4 <= s["bar"] <= 6]
    assert len(gates) == 16
    assert all(any(a["control"] == s["control"] and abs(a["at"] - s["at"]) < 1e-8 for a in state["applied"]) for s in gates)
    assert not state["a"]["playing"] and state["b"]["playing"] and state["volume"] == 0.8
    print("PASS mid-track relay plays from A bar 16 into B bar 32 with all 16 gate steps on the audio clock", flush=True)
    # Stale exits stay blocked; the composer must not silently move a plan to another phrase.
    page.evaluate("""() => {
      const h=window.__handoff;
      h.bus.dispatch({type:'pause',deck:'B'}); h.bus.dispatch({type:'togglePlay',deck:'A'});
    }""")
    page.wait_for_timeout(400)
    assert page.get_by_role("button", name="Try transition").is_disabled()
    assert not errors, errors
    print("PASS stale exit is blocked and no page errors", flush=True)
    browser.close()

"""LIVE end-to-end check of the AI composer. Costs one real Claude call (~$0.15 with Opus 5.5)
unless the reply is already cached. Opt-in: never part of the automatic checks.

  board UI -> Vite proxy -> backend -> Claude -> compile -> critic -> merge -> Try transition
  -> plays on the real audio engine (steps on the bar, B in on its composed entry bar).

Run with the dev server and the backend up (README), e.g.:
  pipeline/.venv/bin/python web/e2e/composer_live.py "With My Crew" "EvenFall" 32 --yes-spend
Args: outgoing title, incoming title (library row text), exit bar (a phrase start of the outgoing track).
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

URL = "http://localhost:5173/"
if "--yes-spend" not in sys.argv or len(sys.argv) < 5:
    sys.exit(__doc__)
OUT_T, IN_T, EXIT = sys.argv[1], sys.argv[2], int(sys.argv[3])

with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-audio-output"])
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL); pg.wait_for_timeout(800)
    for title, deck in [(OUT_T, "A"), (IN_T, "B")]:
        pg.get_by_role("button", name="Library", exact=True).click(); pg.wait_for_timeout(300)
        pg.locator(".lib-row", has_text=title).get_by_text(f"Load to {deck}").click()
        pg.wait_for_function(f"() => window.__handoff.engine.snapshot('{deck}').loaded", timeout=60000)
    pg.get_by_role("button", name="Compose with AI", exact=True).click()
    pg.get_by_label("Composer exit phrase").select_option(str(EXIT))
    pg.get_by_label("Composer duration").select_option("16")
    t0 = time.time()
    pg.get_by_role("button", name="Compose four ideas").click()
    pg.wait_for_selector("dialog", state="detached", timeout=300000)
    print(f"composed in {time.time() - t0:.0f} s; picked:", pg.get_by_label("Transition recipe").input_value())
    pair = pg.evaluate("""() => { const a = window.__handoff.engine.snapshot('A'), b = window.__handoff.engine.snapshot('B');
        return window.__handoff.transitions.composedFor(a.trackId, b.trackId).filter(c => c.source === 'llm')
          .map(c => ({idea: c.idea, score: c.critic.score, start: c.recipe.anchor.out_start_bar, from: c.recipe.anchor.in_from_bar,
                      bars: c.recipe.bars, moves: c.plan.moves.map(m => m.move), rationale: c.rationale, reasons: c.critic.reasons})); }""")
    print(json.dumps(pair, indent=1))
    # Play A from just before the exit, then run the picked (best) idea.
    pg.evaluate(f"""() => {{ const h = window.__handoff, a = h.engine.decks.A;
        h.bus.dispatch({{type: 'togglePlay', deck: 'A'}});
        h.bus.dispatch({{type: 'seekFraction', deck: 'A', fraction: a.track.grid.timeAtBar({EXIT} - 3.4) / a.track.analysis.duration_s}}); }}""")
    pg.wait_for_timeout(400)
    pg.get_by_role("button", name="Try transition").click()
    seen, worst, entry = set(), 0.0, None
    for _ in range(600):
        pg.wait_for_timeout(100)
        r = pg.evaluate("""(seen) => { const h = window.__handoff, e = h.engine, s = h.coDJ.status(), now = e.ctx.currentTime, out = [];
            if (s.out && e.decks[s.out].playing) h.coDJ.stepLog.forEach((st, i) => { if (!seen.includes(i) && st.at <= now)
              out.push({i, err: (st.at - e.barToCtx(s.out, s.startBar + st.bar)) * 1000,
                        applied: e.automationLog.some(a => a.control === st.control && Math.abs(a.at - st.at) < 1e-9)}); });
            let entry = null;
            if (s.state === 'running' && s.in && e.decks[s.in].playing && !e.decks[s.in].pending) {
              const enter = s.recipe.events.find(x => x.deck === 'in' && x.command === 'play').at_bar;
              entry = (e.barAt(s.in, now) - (s.recipe.anchor.in_from_bar + e.barAt(s.out, now) - s.startBar - enter)) * 4 * 60 / e.snapshot(s.in).bpm * 1000; }
            return {state: s.state, msg: s.message, steps: out, entry}; }""", list(seen))
        for st in r["steps"]:
            seen.add(st["i"]); worst = max(worst, abs(st["err"]))
            assert st["applied"], st
            assert abs(st["err"]) < 5, st
        if r["entry"] is not None and entry is None:
            entry = r["entry"]
        if r["state"] not in ("armed", "running"):
            break
    fin = pg.evaluate("() => ({a: window.__handoff.engine.snapshot('A'), b: window.__handoff.engine.snapshot('B'), xf: window.__handoff.store.get('xfader')})")
    print(f"state {r['state']} ({r['msg']}); {len(seen)} steps, worst {worst:.2f} ms; B entry {entry:.2f} ms; "
          f"B playing {fin['b']['playing']} level {fin['b']['level']:.2f}; A playing {fin['a']['playing']}; xfader {fin['xf']}; errors {errs}")
    assert r["state"] == "done" and entry is not None and abs(entry) < 5 and fin["b"]["playing"] and not fin["a"]["playing"] and not errs
    print("PASS live composition played end to end")
    b.close()

"""Browser test for Chunk 6: every transition recipe, played by the automation player on the
demo tracks in real Chromium with real Web Audio. Slow (~5 min): each recipe really plays.

Asserts, per run:
  - it finishes, the incoming deck plays and the outgoing deck has stopped (and is silent);
  - every exact step lands within 5 ms of its bar on the outgoing deck's clock, checked
    live as it sounds (and the engine really scheduled it at that time);
  - while both decks play, their bars line up (phase < 2 ms) for recipes that sync;
  - the end state matches the recipe file: incoming lanes at their last value, outgoing
    lanes back to neutral, crossfader on the incoming side;
  - a human grab mid-recipe takes that lane over (takeover run);
  - no page errors.

Run (in web/): npm run dev, then:   python e2e/recipes.py [url]
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173/"
ROOT = Path(__file__).resolve().parents[2]
RECIPES = {p.stem: json.loads(p.read_text()) for p in (ROOT / "contracts" / "recipes").glob("*.json")}
# The critic's best composed transition for the demo pair (Chunk 7), if the planner has run.
_t = ROOT / "web" / "public" / "library" / "transitions.json"
_pair = next((p for p in json.loads(_t.read_text())["pairs"] if _t.exists() and p["out_track"].startswith("demo-amber")
              and p["in_track"].startswith("demo-teal") and p["best"] is not None), None) if _t.exists() else None
if _pair:
    RECIPES["composed"] = _pair["candidates"][_pair["best"]]["recipe"]
DEFAULTS = {"eqLow": 0.5, "eqMid": 0.5, "eqHigh": 0.5, "filter": 0.5, "volume": 0.8, "echo": 0,
            "stem.drums": 1, "stem.bass": 1, "stem.vocals": 1, "stem.other": 1}
RUNS = [("bass_swap", "A", False), ("filter_handoff", "B", False), ("echo_out", "A", False),
        ("loop_roll", "A", False), ("drum_bridge", "A", False), ("bass_swap", "A", True)] + ([("composed", "A", False)] if _pair else [])

# Checks every step that has sounded (and isn't checked yet) against the out deck's bar time NOW,
# while it is still playing: |scheduled time - bar time| and whether the engine logged it.
CHECK_STEPS = """(seen) => {
  const h = window.__handoff, e = h.engine, s = h.coDJ.status(), now = e.ctx.currentTime, out = [];
  if (!s.out || !e.decks[s.out].playing) return out;
  h.coDJ.stepLog.forEach((st, i) => {
    if (seen.includes(i) || st.at > now) return;
    const bar = e.barToCtx(s.out, s.startBar + st.bar);
    const applied = e.automationLog.some((a) => a.control === st.control && Math.abs(a.at - st.at) < 1e-9);
    out.push({ i, control: st.control, bar: st.bar, errMs: (st.at - bar) * 1000, applied });
  });
  return out; }"""

PHASE = """() => { const e = window.__handoff.engine, s = window.__handoff.coDJ.status(), now = e.ctx.currentTime;
  if (s.state !== 'running' || !e.decks.A.playing || !e.decks.B.playing || e.decks.A.pending || e.decks.B.pending) return null;
  const f = (x) => x - Math.floor(x); let d = f(e.barAt(s.out, now)) - f(e.barAt(s.in, now)); d = f(d + 0.5) - 0.5;
  return d * 4 * 60 / e.snapshot(s.out).bpm * 1000; }"""

# Where B is vs where the anchor says it should be: B's bar - (in_from_bar + bars since it came in), in ms.
ENTRY = """(fromBar) => { const e = window.__handoff.engine, s = window.__handoff.coDJ.status(), now = e.ctx.currentTime;
  if (s.state !== 'running' || !e.decks[s.in].playing || e.decks[s.in].pending) return null;
  const enter = s.recipe.events.find((x) => x.deck === 'in' && x.command === 'play').at_bar;
  const want = fromBar + (e.barAt(s.out, now) - s.startBar - enter);
  return (e.barAt(s.in, now) - want) * 4 * 60 / e.snapshot(s.in).bpm * 1000; }"""

failures = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond: failures.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-audio-output"])   # see smoke.py
    for rid, out, takeover in RUNS:
        r, inn = RECIPES[rid], ("B" if out == "A" else "A")
        label = f"{r['name']} {out}->{inn}" + (" (takeover)" if takeover else "")
        print(f"--- {label}", flush=True)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("response", lambda resp: resp.status >= 400 and errs.append(f"{resp.status} {resp.url}"))
        pg.goto(URL); pg.wait_for_timeout(800)
        for title, deck in [("Amber Room", "A"), ("Teal Signal", "B")]:
            pg.click("text=Library"); pg.wait_for_timeout(300)
            pg.locator(".lib-row", has_text=title).locator(f"text=Load to {deck}").click()
            pg.wait_for_function(f"() => window.__handoff.engine.decks.{deck}.track !== null", timeout=60000)
        pg.mouse.click(30, 30)
        pg.evaluate(f"""() => {{ const h = window.__handoff;
            h.store.set('xfader', '{out}' === 'A' ? 0 : 1, 'mouse');
            h.bus.dispatch({{type: 'togglePlay', deck: '{out}'}}); }}""")
        pg.wait_for_timeout(400)
        # Jump to ~3 bars before the transition (the next phrase, or a composed one's exit bar).
        to_bar = r["anchor"]["out_start_bar"] - 3.4 if "anchor" in r else 12.6
        pg.evaluate(f"""() => {{ const d = window.__handoff.engine.decks.{out}, g = d.track.grid;
            window.__handoff.bus.dispatch({{type: 'seekFraction', deck: '{out}', fraction: g.timeAtBar({to_bar}) / d.track.analysis.duration_s}}); }}""")
        pg.wait_for_timeout(300)
        pg.select_option("select[aria-label='Transition recipe']", r["id"])
        pg.get_by_role("button", name="Try transition").click()
        st = pg.evaluate("() => window.__handoff.coDJ.status()")
        check(st["state"] in ("armed", "running"), f"armed ({st['state']}: {st['message']})")

        seen, results, took, phases, entry = [], [], False, [], None
        for _ in range(int((r["bars"] + 8) * 2.2 / 0.1)):
            pg.wait_for_timeout(100)
            for res in pg.evaluate(CHECK_STEPS, seen):
                seen.append(res["i"]); results.append(res)
            st = pg.evaluate("() => window.__handoff.coDJ.status()")
            if "anchor" in r and entry is None:
                entry = pg.evaluate(ENTRY, r["anchor"]["in_from_bar"])
            ph = pg.evaluate(PHASE)
            if ph is not None: phases.append(ph)
            if takeover and not took and st["state"] == "running" and st["bar"] >= 2:
                pg.evaluate(f"""() => {{ const s = window.__handoff.store;
                    s.setHeld('{inn}.eqLow', true); s.set('{inn}.eqLow', 0.3, 'mouse'); s.setHeld('{inn}.eqLow', false); }}""")
                took = True
            if st["state"] not in ("armed", "running"): break
        check(st["state"] == "done", f"finished ({st['state']}: {st['message']})")

        n_steps = sum(len([1 for i, pt in enumerate(l["points"]) if i and pt[0] == l["points"][i - 1][0]])
                      for l in r["lanes"] if not (takeover and l["deck"] == "in" and l["control"] == "eqLow"))
        worst = max((abs(x["errMs"]) for x in results), default=0.0)
        check(len(results) == n_steps and all(x["applied"] for x in results) and worst < 5,
              f"{len(results)}/{n_steps} steps applied on their bar, worst {worst:.2f} ms")

        if "anchor" in r:
            check(entry is not None and abs(entry) < 5, f"B came in from its composed entry bar {r['anchor']['in_from_bar']} ({entry if entry is None else round(entry, 2)} ms)")
        if r["requires"]["beatmatchable"]:
            worst_ph = max((abs(x) for x in phases), default=float("nan"))
            check(len(phases) > 3 and worst_ph < 2, f"bars line up while both decks play (worst {worst_ph:.2f} ms over {len(phases)} samples)")
        pg.wait_for_timeout(600)
        snap = pg.evaluate(f"""() => {{ const e = window.__handoff.engine;
            return {{ out: e.snapshot('{out}'), in: e.snapshot('{inn}'), store: Object.fromEntries(
              [...'{out}{inn}'].flatMap(d => {json.dumps(list(DEFAULTS))}.map(c => [d + '.' + c, window.__handoff.store.get(d + '.' + c)]))
              .concat([['xfader', window.__handoff.store.get('xfader')]])) }}; }}""")
        check(snap["in"]["playing"] and not snap["out"]["playing"] and snap["out"]["loop"] is None,
              f"{inn} plays, {out} stopped (levels {snap['in']['level']:.2f} / {snap['out']['level']:.2f})")
        check(snap["in"]["level"] > 0.2, f"{inn} is audible")
        bad = []
        for l in r["lanes"]:
            if l["control"] == "xfader":
                want, key = (1 if inn == "B" else 0), "xfader"
            elif l["deck"] == "in":
                want, key = l["points"][-1][1], f"{inn}.{l['control']}"
                if takeover and l["control"] == "eqLow": want = 0.3
            else:
                want, key = DEFAULTS[l["control"]], f"{out}.{l['control']}"
            if abs(snap["store"][key] - want) > 1e-6: bad.append(f"{key}={snap['store'][key]} (want {want})")
        check(not bad, "end state matches the recipe" + (f": {bad}" if bad else ""))
        if takeover:
            check(f"{inn}.eqLow" in st["takenOver"] and not any(x["control"] == f"{inn}.eqLow" for x in results),
                  f"the grabbed lane stayed with the human (taken over: {st['takenOver']})")
        check(not errs, f"no page errors {errs or ''}")
        pg.close()
    b.close()

print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)

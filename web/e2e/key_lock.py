"""Key lock and key shift, measured in the audio itself (real Chromium, real engine).

Deck A loops one bar of the demo track's bass section, and an AnalyserNode on the deck's output
finds the bass's pitch. Without key lock, +8% tempo raises it 8%; with key lock it stays put; a
+2 semitone key shift raises it 12.2% at any tempo. Also checks that both decks keep the same
latency (so sync holds) and the brake still dives with key lock on.
Run with Vite: pipeline/.venv/bin/python web/e2e/key_lock.py [url]
"""
import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173/"

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-audio-output"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    for title, deck in [("Amber Room", "A"), ("Teal Signal", "B")]:
        page.get_by_role("button", name="Library", exact=True).click()
        page.locator(".lib-row", has_text=title).get_by_text(f"Load to {deck}").click()
        page.wait_for_function(f"() => window.__handoff.engine.snapshot('{deck}').loaded")
    page.wait_for_function("() => window.__handoff.engine.decks.A.key.shifting !== undefined")
    page.evaluate("() => Promise.all([window.__handoff.engine.decks.A.key.ready, window.__handoff.engine.decks.B.key.ready])")
    assert page.evaluate("() => window.__handoff.engine.decks.A.key.error") is None
    # Loop bar 17 of Amber Room (bass + chords; the progression's first bar, an A root).
    page.evaluate("""() => {
      const h = window.__handoff, a = h.engine.decks.A;
      window.__an = new AnalyserNode(h.engine.ctx, { fftSize: 32768, smoothingTimeConstant: 0 });
      a.out.connect(window.__an);
      h.bus.dispatch({type: 'togglePlay', deck: 'A'});
      h.bus.dispatch({type: 'seekFraction', deck: 'A', fraction: a.track.grid.timeAtBar(16.05) / a.track.analysis.duration_s});
    }""")
    page.wait_for_timeout(300)
    page.evaluate("() => window.__handoff.bus.dispatch({type: 'loop', deck: 'A', beats: 4})")

    def pitch(label: str) -> float:
        page.wait_for_timeout(1500)                       # let ramps, crossovers and latency settle
        hz = page.evaluate("""async () => {
          const an = window.__an, sr = an.context.sampleRate, d = new Float32Array(an.frequencyBinCount), acc = new Float32Array(d.length);
          for (let k = 0; k < 12; k++) { an.getFloatFrequencyData(d); for (let i = 0; i < d.length; i++) acc[i] += 10 ** (d[i] / 20); await new Promise(r => setTimeout(r, 90)); }
          const hzAt = (i) => i * sr / an.fftSize;
          let best = 0;
          for (let i = 1; i < acc.length - 1; i++) { const f = hzAt(i); if (f < 60 || f > 300) continue; if (acc[i] > acc[best]) best = i; }
          const [l, c, r] = [acc[best - 1], acc[best], acc[best + 1]];
          return hzAt(best + 0.5 * (l - r) / (l - 2 * c + r));    // parabolic peak
        }""")
        print(f"  {label}: bass peak {hz:.2f} Hz", flush=True)
        return hz

    def set_controls(tempo: float, lock: int, key: float) -> None:
        page.evaluate(f"""() => {{ const s = window.__handoff.store;
          s.set('keyLock', {lock}, 'mouse'); s.set('A.tempo', {tempo}, 'mouse'); s.set('A.key', {key}, 'mouse'); }}""")

    set_controls(0.5, 1, 0.5)
    base = pitch("tempo 0%, key lock on")
    set_controls(1.0, 0, 0.5)                             # tempo fader full up: +8%
    rate = page.evaluate("() => window.__handoff.engine.snapshot('A').rate")
    vinyl = pitch(f"tempo +{(rate - 1) * 100:.0f}%, key lock off")
    set_controls(1.0, 1, 0.5)
    locked = pitch(f"tempo +{(rate - 1) * 100:.0f}%, key lock on")
    set_controls(1.0, 1, 0.5 + 2 / 12)
    shifted = pitch(f"tempo +{(rate - 1) * 100:.0f}%, key lock on, key +2")
    snap = page.evaluate("() => window.__handoff.engine.snapshot('A')")
    print(f"ratios: lock off {vinyl / base:.4f} (expect {rate:.4f}); lock on {locked / base:.4f} (expect 1); "
          f"+2 st {shifted / base:.4f} (expect {2 ** (2 / 12):.4f}); deck shows {snap['camelot']} -> {snap['heardCamelot']}", flush=True)
    assert abs(vinyl / base - rate) < 0.01
    assert abs(locked / base - 1) < 0.01
    assert abs(shifted / base - 2 ** (2 / 12)) < 0.012
    assert snap["heardCamelot"] != snap["camelot"] and snap["pitchSemitones"] == 2
    print("PASS key lock holds the key at +8% tempo, and key shift moves it by whole semitones", flush=True)

    lat = page.evaluate("() => ({A: window.__handoff.engine.decks.A.key.latency, B: window.__handoff.engine.decks.B.key.latency})")
    assert lat["A"] == lat["B"] > 0
    page.evaluate("() => window.__handoff.store.set('keyLock', 0, 'mouse')")
    page.wait_for_timeout(200)
    lat_off = page.evaluate("() => ({A: window.__handoff.engine.decks.A.key.latency, B: window.__handoff.engine.decks.B.key.latency})")
    assert lat_off["A"] == lat_off["B"] == 0
    print(f"PASS both decks share the key stage's latency ({lat['A'] * 1000:.0f} ms with key lock, 0 without), so sync is unaffected", flush=True)
    assert not errors, errors
    print("PASS no page errors", flush=True)
    browser.close()

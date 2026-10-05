# Handoff

A DJ board you'll play with your hands, with an AI co-DJ that helps beginners make good transitions. This is session 1: the preprocessing pipeline and a working two-deck board (mouse and keyboard for now). See `ROADMAP.md` for the plan and `AGENTS.md` for how to work on it.

## Run it

You need Python 3.11+, Node 20+, and ffmpeg (`brew install ffmpeg` on macOS).

```bash
# 1. Make a library. Start with the two synthetic demo tracks (they include stems):
cd pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python make_demo_tracks.py --out tracks
python preprocess.py --in tracks --out ../web/public/library

# 2. Run the board:
cd ../web
npm install
npm run dev          # open http://localhost:5173, then Library -> load a track on each deck
```

**Your own music.** Put files in `pipeline/tracks/` and rerun `preprocess.py`. Add `--stems` to split drums, bass, vocals, and melody with Demucs (`pip install demucs` first). It's slow on CPU, so expect a few minutes per track, but it only runs once per track. If a track's phrase lines look one beat off, add `{"Your File.mp3": {"downbeat_shift": 1}}` to `pipeline/overrides.json` and rerun with `--force`.

**Real tracks from Audius.** `fetch_audius.py` downloads tracks whose artists have enabled downloads, saves them to `pipeline/tracks/` as `Artist - Title.<ext>`, and records title, artist, license, and credit (artist + Audius URL) in `pipeline/overrides.json`. It only adds keys that are missing, so your own fixes are never overwritten. Tracks that aren't downloadable, or are gated behind a follow/tip/purchase, are skipped and listed.

```bash
cd pipeline
python fetch_audius.py https://audius.co/Zute/nonstop PkglRZ1               # track URLs or IDs
python fetch_audius.py --search "tech house" --genre "Tech House" --limit 5  # or a search
python fetch_audius.py --search techno --limit 3 --dry-run                   # preview, write nothing
python preprocess.py --in tracks --out ../web/public/library
```

An API key is optional (it raises rate limits). Get one at [api.audius.co/plans](https://api.audius.co/plans) and export it as `AUDIUS_API_KEY`; never commit it. "Downloadable" only means the artist allows downloads. Most tracks are "All rights reserved", so check each license before deploying anything publicly.

## Try this

1. Load the demo tracks on A and B, then play A (`Q`).
2. With "Snap to beat" on, press play on B (`P`). It waits and starts on A's next bar.
3. Press Sync on B (`L`). Tempo matches and the kicks line up on the waveforms.
4. Watch the phrase rings. Start a crossfade when A's ring is about to complete.
5. Mute B's drums, then bring them back right on the phrase line.

Keyboard: `Q`/`P` play, `W`/`O` cue, `S`/`L` sync, `1`–`4` and `7`–`0` loops, arrow keys for the crossfader. Double-click any knob or fader to reset it.

**Hands.** Press **Camera** (top right) and allow camera access. A ring follows each hand; it tracks your knuckles and ignores twisting, so it stays still while you pinch and while you turn a knob. Put the ring on a control and pinch (touch your thumb tip to your index tip) to grab it. Buttons and sliders need the ring on them; knobs pull the ring in when it's close and hold it on the knob while you turn:
- **Knobs:** twist your hand like turning a real knob. Clockwise turns it up; about 75° of hand rotation goes from center to either end. Let go and re-grab to turn further.
- **Faders and the crossfader:** move up/down or left/right.
- **Pads and toggles:** fire on the pinch. (Double-click resets a knob or fader with the mouse; hands don't reset, so you can re-grab quickly to keep turning.)

Open your fingers to let go. The mouse keeps working alongside (it drags knobs vertically). Seeking on the track overview is mouse-only, so a stray pinch can't jump the track; with hands, use Cue and the jump pads.

**Loops and rolls.** Loop pads (1, 2, 4, 8 beats) hold a section until you press the same pad again. Roll pads (1/2 to 1/16 of a beat) stutter; when you let go, the track carries on where it would have been, so a synced deck stays in time. The preview panel shows the model's speed, your live pinch value next to the grab threshold, and a hold-drift readout. If pinches are missed or a relaxed hand presses things, move the **Pinch** slider (strict ↔ easy); it's remembered in this browser. **Record 10 s of landmarks** saves a JSON of your raw hand data, which is what we tune pinch and twist against.

The hand model (7.8 MB) is downloaded once into `web/public/models/` the first time you run `npm run dev` or `npm run build` (or run `npm run fetch-hand-model`). Everything is served locally; nothing is fetched from a CDN at runtime.

## Tests

```bash
cd pipeline && python -m pytest -q
cd web && npm run typecheck && npm test
python web/e2e/smoke.py      # with `npm run dev` running; needs: pip install playwright && python -m playwright install chromium
```

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

## Try this

1. Load the demo tracks on A and B, then play A (`Q`).
2. With "Snap to beat" on, press play on B (`P`). It waits and starts on A's next bar.
3. Press Sync on B (`L`). Tempo matches and the kicks line up on the waveforms.
4. Watch the phrase rings. Start a crossfade when A's ring is about to complete.
5. Mute B's drums, then bring them back right on the phrase line.

Keyboard: `Q`/`P` play, `W`/`O` cue, `S`/`L` sync, `1`–`4` and `7`–`0` loops, arrow keys for the crossfader. Double-click any knob or fader to reset it.

## Tests

```bash
cd pipeline && python -m pytest -q
cd web && npm run typecheck && npm test
python web/e2e/smoke.py      # with `npm run dev` running; needs: pip install playwright && python -m playwright install chromium
```

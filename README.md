# Handoff

Link: https://handoff-web.onrender.com/
Access Code: handoff-fall-2026

## What it does

Handoff is a fully functional DJ board that can be controlled with your hands through a webcam. It also features an AI co-DJ that is able to compose complex transitions given any two tracks, so that even someone who can't DJ can mix well. Behind the scenes, handoff is also able to preprocess any track, run stem separation through demucs, detect drops and hooks, etc. such that there is a complete set of tools for the AI DJ to utilize for the transitions.

## How to use it

Provide the access code listed above to get to the DJ board. While in the board, the first thing to do is load tracks through the library button on the top right. Pick a track A and track B, and they should load into the board. Then you can just play around with it, the buttons and knobs are labelled and should be pretty self explanatory (Just playing around with them will instantly show you what each of them does, way easier than explaining). The sliders on each far side is for the tempos of each track, and the crossfader slider in the middle is to just adjust how much volume each side gets, sliding it to one side will make that side louder and the other side quieter, thus it starts in the middle. 

You can obviously click on things and such just with your mouse, but for a better experience, you can toggle on the camera also located on the top right to play the board with your hands. To do this, simply use your index finger as the cursor, and pinch with your thumb to click/hold. You can use both hands at the same time on both sides. to twist a knob, just pinch and twist in the air like you would a real knob. Same thing with the sliders. To use the AI DJ, click compose with AI on the top, and select where, the max transition length, and a creative direction, then click compose. After a minute or two, the AI will generate a transition, which can be selected in the drop down to the left. Finally, just press try transition while your track is playing, and sit back and enjoy.

## Features I'm most proud of

I am the most proud of the AI composer, because getting it to generate good transitions that are not the most basic preset ones but still having it sound really good was difficult. The ultimate design of the composer is to ask Claude to write a verbal transition plan in DJ move vocabulary, given all the data of the two current tracks. Then a compiler turns that verbal plan into automation that actually makes sense to the DJ board and it will execute it on its own. Finally a critic following a rubric based on music theory simulates and scores the mix. Claude will come up with 3 drafts, and the best scoring one will be revised. It wasn't very good at first, but the more data I was able to provide it the better it got and actually understood the tracks without physically being able to hear it. This included being able to detect drops, builds, and hooks.

## Running it locally

Requirements: Python 3.11+ (tested on 3.13), Node 20+ (tested on 22), ffmpeg. Optional: Demucs, for stems.
Board: `cd web && npm install && npm run dev`, then open http://localhost:5173.
AI Composer (optional):
1. `pip install -r backend/requirements.txt`
2. put `ANTHROPIC_API_KEY=...` in `pipeline/.env`
3. from the repo root: `pipeline/.venv/bin/python -m uvicorn backend.app:app --port 8000`

## Secrets

Here are the following secrets:
Anthropic API key
Cloudflare R2 credentials
Access code (not really a secret, this is just so less people can use it for now. I did put it in the README for easy access for grading but I will just take it down after)

These secrets are in pipeline/.env which is gitignored and will not be committed

## How I used AI

Tools I used:
Claude chat (Opus 5.5) helped me plan out the project, and provided a roadmap and spec document
Claude Code (Opus 5.5) wrote most of the code, tests, and docs.
OpenAI Codex (Astra 6) wrote some of the code for the AI composer, but Claude finished it

## AI-generated documentation

## Citations and credits

Notes on what to cover (check each before submitting):
- **AI tools:**
  - Claude Code with Claude Opus 5.5 (Anthropic): code, tests, docs.
  - OpenAI Codex: initial live composer.
  - Claude API, `claude-opus-5-5`: runtime composer.
- **Libraries:**
  - librosa (McFee et al., *librosa: Audio and Music Signal Analysis in Python*, SciPy 2015), NumPy;
  - Demucs / htdemucs (Rouard, Massa, Défossez, *Hybrid Transformers for Music Source Separation*, ICASSP 2023);
  - MediaPipe Hand Landmarker (Google);
  - Signalsmith Stretch (Geraint Luff, MIT licence), for key lock;
  - React, Vite, FastAPI, boto3, Playwright, vitest, pytest, moto.
- **Methods adapted:**
  - Krumhansl–Kessler key profiles, for key detection;
  - Foote's checkerboard novelty (Foote, *Automatic Audio Segmentation Using a Measure of Audio Novelty*, ICME 2000), for sections;
  - the 1€ filter (Casiez, Roussel, Vogel, CHI 2012), for hand smoothing;
  - the Kabsch / least-squares rotation, for palm twist;
  - the Camelot wheel (Mixed In Key), for harmonic mixing.
- **Music:**
  - tracks from Audius artists who enabled downloads; the in-app library lists each one's artist, licence and source link;
  - two demo tracks synthesized by `pipeline/make_demo_tracks.py`;
  - the three CC BY-NC-SA tracks and the CC BY track need credit.
- **Services:** Audius API, Cloudflare R2, Render.

---

### Run it

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

**Your own music.** Put files in `pipeline/tracks/` and rerun `preprocess.py`. Add `--stems` to split drums, bass, vocals, and melody with Demucs (`pip install demucs` first; add `--only "Artist"` to do one track). It takes about 10 s per track on an Apple-silicon Mac, and later runs keep the stems it made. If a track's phrase lines look one beat off, add `{"Your File.mp3": {"downbeat_shift": 1}}` to `pipeline/overrides.json` and rerun with `--force`.

**Real tracks from Audius.** `fetch_audius.py` downloads tracks whose artists have enabled downloads, saves them to `pipeline/tracks/` as `Artist - Title.<ext>`, and records title, artist, license, and credit (artist + Audius URL) in `pipeline/overrides.json`. It only adds keys that are missing, so your own fixes are never overwritten. Tracks that aren't downloadable, or are gated behind a follow/tip/purchase, are skipped and listed.

```bash
cd pipeline
python fetch_audius.py https://audius.co/Zute/nonstop PkglRZ1               # track URLs or IDs
python fetch_audius.py --search "tech house" --genre "Tech House" --limit 5  # or a search
python fetch_audius.py --search techno --limit 3 --dry-run                   # preview, write nothing
python preprocess.py --in tracks --out ../web/public/library
```

An API key is optional (it raises rate limits). Get one at [api.audius.co/plans](https://api.audius.co/plans) and export it as `AUDIUS_API_KEY`; never commit it. "Downloadable" only means the artist allows downloads. Most tracks are "All rights reserved", so check each license before deploying anything publicly.

### Try this

1. Load the demo tracks on A and B, then play A (`Q`).
2. With "Snap to beat" on, press play on B (`P`). It waits and starts on A's next bar.
3. Press Sync on B (`L`). Tempo matches and the kicks line up on the waveforms.
4. Watch the phrase rings. Start a crossfade when A's ring is about to complete.
5. Mute B's drums, then bring them back right on the phrase line.
6. Compose a transition live: put `ANTHROPIC_API_KEY=...` in `pipeline/.env`, then start the local composer server next to `npm run dev`:
   `pipeline/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000` (from the repo root; `pip install -r backend/requirements.txt` first). Load two tracks, press **Compose with AI**, choose where on the playing track to start (any phrase, mid-track included), a length, and a creative direction. Claude drafts three ideas, the board's compiler and critic keep the strongest, and Claude refines that one with the critic's findings (up to two revisions). One transition comes back, pre-selected, in about 1–2 minutes (about $0.12–0.30). Press **Try transition** to play it.
7. Compose transitions for your library offline (Chunk 7): `cd pipeline && python plan_transitions.py` runs the free rules composer on every pair. To have Claude compose, put `ANTHROPIC_API_KEY=...` in `pipeline/.env` (gitignored) and run e.g. `python plan_transitions.py --composer both --max-pairs 3`; it prints what each run cost, and replies are cached so reruns are free. The picker then lists "Composed for this pair" first, best score first.
8. Let the board do a transition: with A playing and B loaded (and paused), pick a recipe at the top (Bass swap, Filter handoff, Echo out, Loop roll, Drum bridge) and press **Try transition**. It starts on A's next phrase and plays itself on the board's own controls. Grab any control it's moving and that control is yours for the rest of the transition. If a recipe can't run (keys clash, tempos too far apart, no stems), it says why.

Keyboard: `Q`/`P` play, `W`/`O` cue, `S`/`L` sync, `1`–`4` and `7`–`0` loops, arrow keys for the crossfader. Double-click any knob or fader to reset it.

**Hands.** Press **Camera** (top right) and allow camera access. A ring follows each hand; it tracks your knuckles and ignores twisting, so it stays still while you pinch and while you turn a knob. Put the ring on a control and pinch (touch your thumb tip to your index tip) to grab it. Buttons and sliders need the ring on them; knobs pull the ring in when it's close and hold it on the knob while you turn:
- **Knobs:** twist your hand like turning a real knob. Clockwise turns it up; about 75° of hand rotation goes from center to either end. Let go and re-grab to turn further.
- **Faders and the crossfader:** move up/down or left/right.
- **Pads and toggles:** fire on the pinch. (Double-click resets a knob or fader with the mouse; hands don't reset, so you can re-grab quickly to keep turning.)

Open your fingers to let go. The mouse keeps working alongside (it drags knobs vertically). Seeking on the track overview is mouse-only, so a stray pinch can't jump the track; with hands, use Cue and the jump pads.

**Loops and rolls.** Loop pads (1, 2, 4, 8 beats) hold a section until you press the same pad again. Roll pads (1/2 to 1/16 of a beat) stutter; when you let go, the track carries on where it would have been, so a synced deck stays in time. The preview panel shows the model's speed, your live pinch value next to the grab threshold, and a hold-drift readout. If pinches are missed or a relaxed hand presses things, move the **Pinch** slider (strict ↔ easy); it's remembered in this browser. **Record 10 s of landmarks** saves a JSON of your raw hand data, which is what we tune pinch and twist against.

The hand model (7.8 MB) is downloaded once into `web/public/models/` the first time you run `npm run dev` or `npm run build` (or run `npm run fetch-hand-model`). Everything is served locally; nothing is fetched from a CDN at runtime.

### Tests

```bash
cd pipeline && python -m pytest -q
cd web && npm run typecheck && npm test
python web/e2e/smoke.py      # with `npm run dev` running; needs: pip install playwright && python -m playwright install chromium
```

### Putting it online

See [docs/DEPLOY.md](docs/DEPLOY.md): a private, access-code deployment on Render (free) with the music in a private Cloudflare R2 bucket.
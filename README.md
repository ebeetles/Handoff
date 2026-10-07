# Handoff

> **DRAFT SCAFFOLD: replace everything in this top part with your own writing.**
> Each section below lists what the assignment asks for and notes on what to cover, with facts
> from the project to draw on. They are prompts, not text to keep. Delete every note (and this
> box) once you've written the section in your own words. The AI-generated documentation at the
> bottom can stay, under its label.

## What it does

Notes on what to cover:
- **One-paragraph pitch:** a DJ board you play with your hands (webcam), plus an AI co-DJ that composes transitions so a beginner can mix well.
- **Who it's for, and the problem:** DJing has a steep learning curve (beatmatching, phrasing, keys, EQ). Your own angle on why you built it.
- **The main parts, one line each:**
  - the board: two decks, mixer, stems;
  - hand control;
  - the AI composer;
  - the offline analysis that makes the rest possible.
- **Optional:** what a "transition" is, for a reader who has never DJ'd.
- **The deployed link,** and that the access code is sent separately to course staff.

## How to use it

Notes on what to cover (a short walkthrough a grader can follow):
- **Getting in:** open the link in Chrome on a laptop or desktop and enter the access code. The first load can take about a minute while the free server wakes.
- **Load and play:** open **Library** and load a track on each deck. Play A, then sync B, and use the crossfader.
- **The board's tools:** the parts (stems) pads, loops and rolls, EQ and filter knobs. Knobs click into their default.
- **Key lock and key shift:** the chip in the top bar, and −/+ by each deck's key.
- **Hands:** **Camera** (top right), pinch to grab, twist to turn knobs, open your hand to let go. The mouse works alongside.
- **Transitions:**
  - the A → B direction button;
  - pick a transition and press **Try transition**;
  - **Compose with AI**: choose a phrase, a length and a creative direction; it takes 1–3 minutes; review it, then try it.
- **Keyboard shortcuts** (the list is in the library panel).

## Features I'm most proud of

Notes on what to cover: pick 2–4 and say *why*, in your own voice: what was hard, what surprised you, what you learned. Candidates, with facts:
- **Hand control:**
  - pinch and twist on real knobs;
  - the cursor stays still while you pinch and twist (it follows the knuckles and corrects for rotation);
  - lock-on for knobs only.
  - It took many rounds of your own testing (pinch too strict or too forgiving, lock-on on or off, twist glitches).
- **The AI composer:**
  - **How it works:** Claude writes a plan in a vocabulary of DJ moves; a compiler turns it into automation; a critic simulates the mix and scores it.
  - **Getting one good idea:** three drafts, the best one revised with the critic's findings.
  - **Variety:** concept cards and a memory of recent work, after you found it repetitive.
  - **Structure:** it uses detected drops, builds and hooks, can start mid-track, and has tempo rides, brakes and hook loops.
- **Key lock and key shift:** tracks in clashing keys can blend. It's measured in the audio: pitch held exactly at +8% tempo, and a +2 semitone shift accurate to about 14 cents.
- **Timing precision:** everything is scheduled on the audio clock. Tests show automation steps land at 0.00 ms and synced decks stay within 1 ms.
- **The analysis pipeline:** beat grids, stem separation (Demucs), key, and section, drop and hook detection, checked against synthetic tracks with known answers.
- **Your listening shaped the AI:** for example, you preferred a composed transition over a higher-scored drum bridge, and liked same-key blends the critic had marked down. Both changed how the critic and the picker work.

## Running it locally

Notes on what to cover (keep the commands; explain them in your own words):
- **Requirements:** Python 3.11+ (tested on 3.13), Node 20+ (tested on 22), ffmpeg. Optional: Demucs, for stems.
- **Pipeline:**
  - `cd pipeline && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
  - Tracks: `python make_demo_tracks.py --out tracks` (two synthetic demo tracks with stems), or `fetch_audius.py` (see below), or your own files in `pipeline/tracks/`.
  - Then `python preprocess.py --in tracks --out ../web/public/library` (add `--stems` for Demucs).
- **Board:** `cd web && npm install && npm run dev`, then open http://localhost:5173.
- **AI composer (optional):**
  - `pip install -r backend/requirements.txt`
  - put `ANTHROPIC_API_KEY=...` in `pipeline/.env`
  - from the repo root: `pipeline/.venv/bin/python -m uvicorn backend.app:app --port 8000`
- **Tests:**
  - `cd pipeline && python -m pytest -q` (117 tests)
  - `cd web && npm run typecheck && npm test` (105 tests)
  - the Playwright browser scripts in `web/e2e/` (listed in `AGENTS.md`)
- **Deploying:** point to `docs/DEPLOY.md`.

## Secrets

Notes on what to cover:
- **Which secrets exist:**
  - the Anthropic API key;
  - Cloudflare R2 credentials: two tokens, read-write for your uploads and read-only for the server;
  - the access code;
  - optionally an Audius API key.
- **Where they live:**
  - locally, in `pipeline/.env`, which is gitignored and never committed;
  - deployed, in Render's environment variables only.
- **The key never reaches the browser:**
  - every Claude call goes through the backend;
  - built files were checked for secrets: none;
  - error messages are scrubbed so a key can't leak through them.
- **Access control when deployed:**
  - the code is checked on every request;
  - the music sits in a private bucket, reached only through links that expire after 6 hours;
  - only the board's own address may call the backend (CORS and origin check);
  - composing is limited to 10 an hour per visitor, to cap API spend.
- **What's deliberately not in git:** `.env` files, audio, the generated library.

## How I used AI

Notes on what to cover (this section is weighted heavily: be specific and honest):
- **Tools:**
  - **Claude Code** (Anthropic's coding agent, running Claude Opus 5.5) wrote the large majority of the code, tests and documentation, chunk by chunk, following `AGENTS.md` and `ROADMAP.md`.
  - **OpenAI Codex** wrote the first version of the live composer (backend endpoint and Compose panel). Claude Code checked it, finished it, and verified it live.
  - **Claude at runtime:** the composer itself calls Claude (`claude-opus-5-5`) through the Anthropic API. That's AI inside the product, not just in building it.
- **Your role** (describe in your own words):
  - **Direction:** the idea and feature choices, e.g. composing from building blocks instead of presets, key lock, more creativity, a single refined transition.
  - **Hands-on testing by hand and ear,** with feedback that changed the design:
    - pinch too strict, then too forgiving;
    - lock-on removed, then knobs only;
    - the cursor moving when pinching;
    - poor transitions between genres and keys;
    - a repetitive composer;
    - track quality;
    - bugs like duplicate menu entries, keys swapping back, B-into-A confusion, a cut-off countdown, dead top-bar controls, window resizing.
  - **Decisions:** licensing (removing tracks), how to deploy, reviewing diffs.
- **How the AI's work was checked:**
  - "done means tested" rules in `AGENTS.md`;
  - pytest (117), vitest (105) and 8 Playwright browser scripts;
  - synthetic ground-truth tracks;
  - live runs with costs reported;
  - the decision log in `ROADMAP.md`.
- **Where AI got it wrong or needed correcting** (pick honest examples):
  - the critic's tuning penalty contradicted your ears, so it was recalibrated;
  - a planner bug piled up duplicate "Echo out" entries;
  - key shifts swapped keys on the way back;
  - the deploy gate said "server didn't start" when the real issue was an address mismatch, and the Render name collision turned out to be the cause.
- **What you learned** about working with AI agents, and what you'd do differently.

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

## AI-generated documentation

*Everything below this line was written by AI (Claude Code), not by me. It's kept as reference
documentation for running and developing the project. Longer AI-written documents: `docs/DEPLOY.md`
(deployment), `ROADMAP.md` (architecture, status, decision log), `AGENTS.md` (working rules and tests).*

A two-deck DJ board you can play with your hands through a webcam, with an AI co-DJ that composes transitions between tracks. `ROADMAP.md` has the architecture and the decision log; `AGENTS.md` has the working rules and every test command.

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
# Handoff roadmap

Handoff (working title) is a DJ board you play with your hands, with an AI co-DJ that helps a complete beginner make good transitions. Hands control the board through a webcam. A fast decision model (Jev) chooses what to mix and when. An LLM explains each choice so the user learns while they play.

This file is the plan of record. Agents: read it together with `AGENTS.md` before touching code, and work on one chunk at a time.

## Architecture at a glance

```
OFFLINE (Python, run once per library)
  tracks/*.mp3|wav|flac  [+ optional <name>.stems/]
        │  pipeline/preprocess.py         (Chunk 1)
        ▼
  web/public/library/<track_id>/
        analysis.json   ◄── contract: contracts/track_analysis.schema.json
        waveform.json
        audio/mix.flac, audio/stem_*.flac
        │  pipeline/plan_transitions.py   (Chunk 7: Claude composes moves -> compiler -> critic)
        ▼
  library/transitions.json  ◄── contract: Transitions (Chunk 7)

BROWSER (React + TypeScript + Web Audio)
  mouse / keys / touch ─┐
  hands (MediaPipe) ────┼─► GestureController ─► ControlStore (0..1 values) ─┐
  co-DJ automation ─────┘                      CommandBus (discrete actions) ─┼─► AudioEngine ─► speakers
                                                                              │
  UI reads engine.snapshot() and the ControlStore; it never mutates the engine directly.

BACKEND (FastAPI, Chunk 8)
  holds API keys; proxies Jev (live re-rank) and the LLM (explanations); stores sessions + ratings
```

The seams between those boxes are typed contracts. That's what lets separate agent sessions build separate chunks without breaking each other.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Analysis | Python 3.12, librosa, numpy, ffmpeg | Mature MIR tooling; offline, so speed doesn't matter |
| Stems | Demucs (`htdemucs`), or pre-separated stems | Best open separator; slow on CPU, so run once and cache |
| Served audio | FLAC, 44.1 kHz | Lossy codecs add encoder delay (~25 ms) that browsers trim inconsistently, which shifts the beat grid |
| Frontend | React 18, TypeScript (strict), Vite | Agents are fluent in it; the audio engine is framework-free TS |
| Audio | Web Audio API (AudioBufferSourceNode, Biquad, DynamicsCompressor) | Sample-accurate scheduling, native loops, no dependencies |
| Hands | MediaPipe Tasks Vision `HandLandmarker` (in browser) | 21 landmarks per hand at video rate in JS. OpenCV isn't needed: we want landmarks, not raw image processing |
| Decisions | Jev (TypeSafe System One API) behind an adapter | Typed choices with probabilities. The same request shape works with Kev (local) and Clef/Clef-flash (Cloudflare), so it's swappable by base URL |
| Explanations | A fast Claude model (e.g. Haiku tier) via the backend | One or two sentences per transition; keys stay server-side |
| Backend | FastAPI + SQLite | Same language as the pipeline; small surface |
| Hosting | Static frontend (Cloudflare Pages/Vercel), audio on object storage (R2/S3), backend on Render | Audio is too large for git and the frontend bundle |
| Tests | pytest (pipeline, ground truth), vitest (timing math), Playwright smoke (real browser audio) | Each layer is tested where its bugs live |

## Contracts (the seams)

| # | Contract | Lives in | Written by → read by |
|---|---|---|---|
| C1 | TrackAnalysis v4 (sections labelled drop/build/chorus/…, cues, hooks, per-bar stem presence) | `contracts/track_analysis.schema.json`, mirrored in `web/src/contracts/track.ts`, fixture in `contracts/fixtures/` | pipeline → web, planner |
| C2 | Control ids + 0..1 semantics (adds `keyLock`, `${deck}.key`) | `web/src/control/controls.ts` | inputs, automation → engine, UI |
| C3 | Command union (add-only: `playAt`, `pause`, `loopOff`, `setControlAt`, `brakeAt`, `loop.slip`, `keyShift`) | `web/src/control/commands.ts` | inputs, co-DJ → engine |
| C4 | PointerSample | `web/src/input/gesture.ts` | input adapters (mouse, hands) → GestureController |
| C5 | Recipe JSON v4 (Chunk 6; v3 adds the outgoing `tempo` lane and `brake`, v4 the incoming `key` lane) | `contracts/recipe.schema.json`, recipes in `contracts/recipes/`, mirrored in `web/src/contracts/recipe.ts` | authored by hand → automation player, planner |
| C6 | Transitions v4 (Chunk 7) | `contracts/transitions.schema.json`, mirrored in `web/src/contracts/transitions.ts` | planner, composer API → web, co-DJ |
| C7 | HTTP API (Chunk 8) | `backend/` OpenAPI (auto from FastAPI); `POST /api/composer/compose` request: `contracts/composer_request.schema.json` v1, mirrored in `web/src/contracts/composer.ts` (response: C6) | backend → web |

To change a contract: bump its version, update writer and reader in the same change, regenerate fixtures, and run every test suite.

## Status

| Chunk | State |
|---|---|
| 0 Contracts and repo conventions | Done |
| 1 Analysis pipeline | v0.1 done; tested on synthetic tracks only |
| 2 Audio engine | v0.1 done |
| 3 Control layer | Done |
| 4 Board UI | v0.1 done |
| 5 Hand input | v0.1 built and tested on synthetic hands; real-camera definition-of-done checks pending |
| 6 Transition recipes + automation | v0.1 done: 5 recipes, automation player, echo; definition of done met in the browser test (by ear: pending) |
| 7 Composed transitions (AI composer + critic) | Done: offline planner + live composer, verified live; Jev re-ranking not started |
| 8 Backend | Started: local composer endpoint only (`backend/app.py`) |
| 9 Live co-DJ + guided mode | Not started |
| 10 LLM voice | Not started |
| 11 Evaluation, writeup, deploy | Not started |

Only mark a chunk done when its definition of done is met and its tests pass.

---

## Chunk 0: Contracts and conventions (done)

Repo layout, the TrackAnalysis schema, the shared fixture tested from both Python and TypeScript, and the agent rules in `AGENTS.md`.

## Chunk 1: Analysis pipeline (v0.1 done)

**Builds.** `pipeline/preprocess.py` turns a folder of tracks into `web/public/library/`. Per track it computes:

- A beat grid. It searches directly for the constant grid t_i = a + b·i that maximizes a kick-weighted onset envelope summed at the grid times. b is searched within ±4% of the tracker's tempo and a over a full beat; tracked beat indices are never used. It then measures the grid's offset from the kick in each 8-bar segment. If the worst offset (`max_drift_ms`) is at most 10 ms, the track is beatmatchable and the grid replaces the tracked beats over the whole track.
- The downbeat phase, from low-band onsets, harmonic change, and overall onset strength.
- Key, via Krumhansl-Kessler profiles on chroma above C2, mapped to Camelot.
- Per-bar band energy (low, mid, high) and loudness.
- Sections, via bar-level self-similarity and checkerboard novelty (energy, chroma, and with stems drums/bass/vocals presence).
- Structure (v4, `structure.py`, rule-based):
  - **Section labels:** intro, verse, build, drop, chorus, breakdown, main, outro, plus each section's vocal level and repetition group.
  - **Cues:** drops, builds, breakdowns, and vocals coming in or stopping.
  - **Hooks:** the most repeated vocal and instrumental 2/4-bar phrase, with every bar they start at.
  - **Per-bar stem presence** is stored in the analysis.
  - **On the board:** the overview marks drops, builds and vocal-hook repeats.
- 16-bar phrases and a 3-band display waveform.

Stems come from Demucs (`--stems`) or from `<name>.stems/` folders. Per-file fixes go in `overrides.json`.

**Verified.** pytest checks on synthetic tracks with known answers (the two demo tracks, plus beat-grid tracks with loud offbeat hats, injected half-beat tracker slips, and a drifting tempo):

- BPM within 0.1.
- Downbeat within 10 ms (measured about 4 ms).
- Grid within 5 ms of every kick despite offbeat hats and tracker slips; a ±25 ms drifting track is flagged not beatmatchable.
- Camelot number correct.
- Exact section boundaries on one track.
- Schema validity, phrase coverage, and index consistency.

**Bugs the ground truth caught** (keep these in mind when editing):

1. Median onset aggregation gave 2/3× and 1/2× tempo errors.
2. Kick fundamentals leaked into chroma and produced wrong keys; fixed with chroma above C2.
3. The beat grid sat 28 ms late from a 2048-sample window; a high-resolution phase pass brought it to about 4 ms.
4. Weighting features before z-scoring undid the weights, so a breakdown was missed.
5. (Real tracks.) A line fit through tracked beats breaks when the tracker slips half a beat: every later beat gets the wrong index (Frog Prince: residual 0.25, slope biased to 123.965 BPM). Full-band onsets also put the phase on loud offbeat hats; Zute's old grid had the right tempo but was half a beat off. Fixed by the direct kick-weighted grid search.
6. `onset_strength(S=...)` without `n_fft` centers as if n_fft were 2048, which made a hop-64 band envelope 35 ms late. Always pass `n_fft` with `S=`.
7. (Real tracks, 2026-10-04.) A kick whose low end swells peaks in the kick band up to ~50 ms after its attack. The kick-weighted drift check flipped between attack and swell by section, so 9 of 11 new tracks read 23–104 ms of "drift"; on one (TEKTULA) the grid itself sat 13.6 ms late. Fixed: the kick band picks *which* position is the beat, then a broadband search within ±⅛ beat (`refine_phase`) picks *where*, and drift is measured the same way. Guarded by `test_soft_kick_grid_sits_on_the_attack`.
8. Beats were stored rounded to 0.1 ms, which made a regular grid's local tempo wobble ±0.02 BPM. Sync takes its rate from the local interval, so synced decks drifted ~0.2 ms/s (smoke-test phase checks failed at ~2.8 ms after re-analysis). Beats are now stored to 1 µs. Guarded by `test_regular_grid_is_stored_evenly_spaced`; post-sync drift went from 0.33 to 0.02 ms over 8 s.

**1b: next upgrades (do with real tracks, before Chunk 7):**
- [ ] Run on 10–20 real tracks across the genres you'll actually mix. *(2026-10-04: 14 real tracks from Audius: house, tech house, techno, progressive house, electro, trance, drum & bass, dubstep, tropical house. 11 of 14 beatmatchable after fixes 7–8; not yet checked by ear. 2026-10-05: +14 for cross-genre testing: hip-hop, drum & bass, dubstep, trap, deep house, disco, funk, breakbeat, downtempo, ambient; 9 of 14 beatmatchable. The drifting ones are a swung hip-hop beat, two funk tracks, a downtempo instrumental and an ambient piece; check by ear whether the grid or the music drifts. The drum & bass track reads 90 BPM, i.e. half time. After listening, the user removed 3 as poor music (the ambient piece, that drum & bass track, the hip-hop beat) and asked for vocal tracks. +8 well-played vocal tracks (2.4k–26k plays): vocal house ×3, house, dance pop, drum & bass, future bass, melodic bass; vocals measured in 41–87% of bars. Library: 35 tracks.)* Calibrate `BEATMATCHABLE_MAX_DRIFT_MS` / `DRIFT_MIN_SALIENCE` and the section novelty threshold, and record the values and reasons in the decision log below.
- [ ] Downbeat accuracy on real music is the weakest point. Spot-check every track in the board: the phrase lines on the waveform should land on obvious changes. Fix with `downbeat_shift`, and consider a neural downbeat tracker (e.g. Beat This!) if more than ~20% need fixing.
- [x] Run Demucs on the real tracks (overnight or on Colab) and check that stem lengths match the mix. *(2026-10-05: all 14 real tracks, about 25 s each including analysis on an M3 Max. Lengths match, and the stems rebuild the mix (DC removed) to −26 to −41 dB. Both decks on stems take about 1 GB for the two longest tracks; they load in about 1 s each.)*
- [ ] Optional: CLAP vibe tags per phrase, as strings in a new `tags` field (schema v3).

**Pitfalls.** Always analyze the exact file that is served, never the original upload. Never serve lossy audio without measuring its decode offset.

## Chunk 2: Audio engine (v0.1 done)

**Builds.** `web/src/audio/`. `Deck` holds one track's graph:

sources (mix or 4 stems) → per-stem gain → declick → 3-band EQ → low-pass + high-pass → volume → meter → crossfade gain

`AudioEngine` owns the AudioContext, both decks, the master limiter, and all command handling:

- Quantized launch on the other deck's bar line.
- Sync, including half- and double-time.
- Loops, beat jumps, cue, and phase-preserving seek.

Pure math is in `grid.ts`, `transport.ts`, `sync.ts`, and `mapping.ts`.

**Verified.** 19 vitest checks on the math, and a Playwright smoke test in real Chromium (`web/e2e/smoke.py`):

- Audio flows through the graph.
- Quantized launch waits for the bar line.
- After sync, phase error is 0.00 ms and still under 1 ms after 8 s.
- Loops hold the playhead, and jumps keep the decks in phase.
- The page logs no errors.

A browser-found bug is already fixed: seeks landed 6 ms late because of the declick fade.

**Known limits, roughly in the order they'll matter:**
- Stems are decoded at full length. That's 85 MB per 4-minute stem (44,100 × 2 ch × 4 B × 240 s), so about 680 MB with both decks on stems. That's fine for a few tracks. The fix later is windowed stems: decode only around planned transitions.
- ~~Changing tempo also shifts pitch (no key lock).~~ Fixed 2026-10-06: key lock and key shift (`audio/keyStage.ts`), see the decision log.
- Sync is one-shot. Constant-tempo tracks stay locked (verified). Drifting tracks need a phase-lock loop: nudge the rate by k × phase error each frame.
- The EQ uses shelving filters rather than true isolators, there's no headphone cue, and there are no FX yet. Echo is added in Chunk 6.

## Chunk 3: Control layer (done)

**Builds.** `ControlStore` holds every knob, fader, and toggle as a 0..1 value with a source tag. `CommandBus` carries pads and buttons. `GestureController` handles hit-testing and the grab/drag/release model, and writes to the store. Input adapters only emit `PointerSample`s.

**Key rule.** The store ignores `auto` writes to any control a human is holding. That's how automation and hands coexist.

## Chunk 4: Board UI (v0.1 done)

**Builds.**
- Two deck panels with a phrase ring counting bars to the next phrase, transport, loop, jump, and stem pads, plus a tempo fader.
- A mixer: EQ, filter, volume, meters, and crossfader.
- Scrolling 3-band waveforms on a shared real-time window, so synced beat lines align.
- A seekable overview with a section energy strip, a library drawer, and keyboard shortcuts.
- Deck B mirrors deck A so each hand owns one side. Hit targets are oversized for imprecise hand input.

**Next:** visual polish only after Chunk 5. Hand use will change what needs to be bigger or moved.

## Chunk 5: Hand input

**Goal.** Play the board with two hands through the webcam, with no change to engine or UI code.

**Builds.** `web/src/input/handAdapter.ts`, a sibling of `pointerAdapter.ts`, plus a camera toggle and a small mirrored preview.
- Use `@mediapipe/tasks-vision` `HandLandmarker` in `VIDEO` mode with `numHands: 2`, and call `detectForVideo` once per animation frame, capped at about 30 fps.
- The pointer position is the midpoint of the thumb tip (landmark 4) and index tip (8). Mirror x for the selfie view. Map an inner "active region" (about the central 80% of the camera frame) to the full viewport, so you never have to reach the frame edge.
- Pinch ratio = |p4 − p8| / |p0 − p9| (pinch distance over hand size, so it doesn't depend on distance from the camera). Use hysteresis: down below about 0.25, up above about 0.35. Tune these.
- Smooth x and y with a One Euro filter (adaptive low-pass: heavy smoothing when still, light when moving fast). Start at minCutoff ≈ 1.0 and beta ≈ 0.01, then tune.
- Pointer ids: `hand-left` / `hand-right`. Note that MediaPipe handedness assumes a mirrored image; verify it. When a hand disappears, call `gestures.lost(id)`.

**Definition of done.**
- Two hands can play, sync, sweep the filter, and move the crossfader.
- Holding a pinched knob still drifts its value less than 1% per second (measure it).
- No dropped grabs during a 30-second continuous fader move.
- Inference cost is measured and stays under 10 ms per frame on your laptop.

**v0.1 (2026-10-04).** `handTracking.ts` (pure: geometry, pinch hysteresis, One Euro, stable ids, dropout grace, stats), `handAdapter.ts` (camera, HandLandmarker, 30 fps loop, preview), `oneEuro.ts`, and `ui/CameraPanel.tsx` (Camera toggle, mirrored preview with landmarks, live readout of inference ms, hold drift, dropped grabs).
- Verified with vitest on synthetic landmark streams through the real GestureController and ControlStore:
  - pinch grab/turn/release, and the hysteresis band;
  - a 100 ms dropout keeps the grab, and 200 ms releases it;
  - two hands on two controls with flickering handedness labels;
  - the mouse working alongside a hand;
  - hold-still drift < 1%/s under ~2 px landmark jitter (simulated).
- Verified in Chromium with a fake camera (smoke test): the model and wasm load from our own server in dev and in a production build, inference runs, the camera turns off, and there are no errors or third-party requests.
- Inference with no hand in frame: 9.6 ms mean, 12.7 ms p95 in headed Chromium on an M3 Max (GPU delegate, ANGLE Metal). Headless Chromium (SwiftShader) runs ~56 ms, which is not representative.
- **Still to check with a real camera** (the definition of done):
  - two-hand play (sync, filter sweep, crossfader);
  - real hold drift (target < 1%/s);
  - a 30 s fader move with 0 dropped grabs;
  - inference < 10 ms while hands are tracked;
  - that `hand-left` really is your left hand (it only affects naming; ids follow position);
  - tuning pinch thresholds and One Euro parameters.

**v0.2 (2026-10-04), after first real-hand use** ("pinching is inconsistent, I have to really clearly show my thumb and index"; "hard to use in general"):
- **Pinch:** measured from the thumb tip to the index's last segment (7→8) instead of tip to tip, with down < 0.30 / up > 0.45. Grace period 250 ms. MediaPipe presence and tracking confidence lowered to 0.3.
- **Lock-on:** hand pointers only, in GestureController. Lock within 56 px of a control, keep until 96 px, switch only to a target ≥ 24 px closer. Pinch grabs the locked target, the cursor snaps onto it, and `data-locked` outlines it. Seek strips only lock from inside.
- **Twist knobs:** knobs are `twist` targets. The hand's palm rotation (`PointerSample.angle`) turns them at 150° per full range, while faders still slide.
- **Recorder:** "Record 10 s of landmarks" downloads raw frames (JSON) to tune on real hands and to use as replay fixtures.
- **Verified:** vitest on synthetic hands (51 web tests), covering lock and switch hysteresis, hidden and unmounted targets, the seek-strip rule, twist turning and ignoring translation, counter-clockwise twist, mouse vertical drag on a twist knob, and twist hold-still drift < 1%/s. The browser smoke test checks lock-on highlight, the snapped cursor and twist on the real layout.
- **Still needs a real hand:** whether the new pinch metric and thresholds fix the misses, whether twist feels right (150° range, filter lag), and whether 56 px lock-on is the right reach.

**v0.3 (2026-10-04), feedback on v0.2:** twist "works pretty well". Lock-on was "too strict … all the buttons and knobs and sliders are so close together". Pinch was "too forgiving … if my fingers are just kind of relaxed it would register as a press".
- **Pinch:** back to thumb tip to index tip, now strict: down < 0.20 (fingertips basically touching), up > 0.32. The panel shows the live value next to the grab threshold.
- **Lock-on follows the hand:**
  - inside a control (≥ 4 px past its edge) locks it at once;
  - in gaps, the nearest control within 40 px wins, with 8 px hysteresis;
  - the lock lets go at 56 px.
  
  v0.2's 24 px switch margin and 96 px unlock kept the lock on the old control while the hand was over its neighbour.
- 52 web tests: packed-controls traversal, no flicker on a shared border, and a relaxed thumb by the index joint isn't a pinch. The smoke test passes.

**v0.4 (2026-10-04), feedback on v0.3:** "no lock on is still better", and "when I pinch the cursor moves because my fingers move naturally while pinching, which means I click on something that I wasn't trying to click on."
- **Lock-on removed.** Hands hit-test exactly like the mouse: a pinch grabs what's under the cursor.
- **The cursor anchor is the index/middle knuckle midpoint** (landmarks 5, 9), not the thumb/index tip midpoint. Pinching curls the index and lifts the thumb, which moved a fingertip cursor by ~15+ px at the click (and again at the release); the knuckles don't move. Twist knobs are unchanged (palm rotation; position is ignored while twisting).
- 48 web tests: a realistic pinch (index curling, thumb closing) leaves the cursor exactly in place while the old anchor moved > 15 px; a pinch beside a control grabs nothing. The smoke test checks twist on the real layout and that a pinch beside a knob does nothing.

**v0.5 (2026-10-04), feedback on v0.4:** the steady pinch cursor "works great", but "when I'm twisting a knob the cursor moves somewhere else, causing issues".
- **Rotation-corrected cursor.** A twist turns the hand about the pinch point and swings the knuckles around it. The cursor subtracts that swing.
  - At each pinch start: d = knuckles − fingertip midpoint (the pivot) is captured.
  - Each frame: offset −= R(θ−θref)·d − R(θprev−θref)·d. This telescopes, so noise can't accumulate.
  - The correction persists after release, so untwisting doesn't move the cursor either. It bleeds off (e^(−travel/200 px)) only while the open hand's corrected cursor travels more than 2 px/frame, so it never creeps on its own.
- **Release debounce (100 ms).** Twisting can briefly open the pinch. A 1–3 frame blip used to release and re-press: a twist knob hit the double-press reset and snapped to center, and a pad fired twice. A release now needs the pinch open ≥ 100 ms, and the hand sends nothing while one is pending, so an opening hand can't drag a fader.
- 53 web tests:
  - a 60° twist keeps the cursor within 1 px (the knuckles alone moved > 60 px);
  - untwisting after release moves it < 2 px;
  - an open hand held still 10 s with jitter creeps < 3 px;
  - 400 px of travel removes > 70% of the correction;
  - turning the hand on a fader leaves it unchanged;
  - a mid-twist flicker keeps the grab;
  - a blip on a pad fires once, and a deliberate double pinch still resets.

**v0.6 (2026-10-04), feedback on v0.5:** "on average no lock on is better, [but] lock on for knobs that twist was actually better because that's how we keep the cursor in place while turning. For buttons and sliders it's better to not have lock on." Also asked for a layout optimized for hand control, and faster loops.
- **Hybrid lock-on.** Only twist knobs pull a hand in: within 36 px, the nearest knob wins with 8 px hysteresis, and the lock lets go at 52 px. While locked or twisting, the cursor sits on the knob's center. Being inside any button or slider cancels a knob's pull. Seek strips are mouse-only for hands.
- **Rolls:** 1/2, 1/4, 1/8, 1/16. They start on their own grid (`loopRange`, unit-tested). Each deck keeps a `slip` anchor (where playback would be without the roll, re-anchored on rate changes), and leaving a roll seeks to it. A fractional loop otherwise exits up to a beat off.
- **Clock race fixed** (found by the new smoke checks). `currentTime` can step a render quantum between two reads in one task, so jumps and roll exits landed ~3 ms out of phase under load. `seek(pos, at)` now takes the clock read that `pos` came from. Sync, jump, roll exit and seek all read the clock once. 3 consecutive smoke runs pass 22/22, with roll exits at -0.6 ms.
- **Layout for hands** (measured at 1920×1080, 1440×900, 1280×800: no overlapping or clipped targets):
  - **Pads:** 14 px apart (was 8). Knobs are bigger (EQ 60, filter 70) and evenly spaced, at least 6 px apart (were touching).
  - **Tempo faders** sit on each deck's outer edge, off the path between the pads and the mixer.
  - **Transport row:** Play is double-width next to Sync, with Sync on the mixer side (deck B mirrors).
  - **Cue** (it stops a playing deck) moves away from Play, to the outer end of a bottom "Cue and jump" row.
  - **Row order** follows use in a transition: Transport, Parts, Loop, Roll, Cue and jump.
  - **Snap-to-beat and Master** move to the top bar (rare settings; the top edge is the hardest place to reach with a raised hand). That gives the mixer the full column.
  - **Display shrinks; hand targets don't:** waveforms 84 → 66 px, phrase ring 150 → 116 px, overview 36 px. Under 860 px tall a compact mode keeps pads ≥ 44 px.
- **Tests:** 62 web tests; the smoke test passes 22/22.

**v0.7 (2026-10-04), feedback on v0.6:** the pinch is "a little too strict again", and "the turning is behaving weirdly now with the lock".
- **Pinch sweet spot.** A relaxed thumb against the index's last joint reads ~0.24 tip-to-tip, the same as a sloppy real pinch, so no single threshold works (0.30 pressed on relaxed hands, 0.20 missed pinches).
  - **Press** needs tip ratio < 0.25 *and* the thumb at the fingertip, not back at the joint: tip − joint ratio < 0.1. Sloppy (0.23) and pad-to-pad pinches press; a relaxed thumb at the joint doesn't.
  - **Release** at > 0.35, joint ignored, so twists hold.
  - **Pinch slider** in the camera panel (0.18 to 0.32, saved per browser) for hands and cameras that differ.
- **Twist fixes** (each caused odd turning, made likelier by lock-on encouraging quick re-grabs):
  - **No double-pinch reset for hands.** Re-grabbing within 350 ms to keep turning snapped the knob to center. Mouse double-click still resets.
  - **The palm reference is re-taken at every pinch.** Against the first-seen shape, a change in hand tilt bent the twist (nonlinear gain).
  - **The angle filter snaps to the hand at each pinch.** After a fast unwind the knob otherwise drifted back a few degrees by itself.
  - **Overshoot re-bases.** Turning (or dragging a fader) past an end and back responds at once, with no dead zone.
- **Tests:** 66 web tests (sweet-spot cases, slider, ratchet without reset, end re-basing, linear twist after a tilt). The smoke test passes 23/23, including the slider.

**Pitfalls.**
- Don't put landmark processing in React state (re-renders every frame).
- Camera permission errors need a clear message in the UI.
- Keep the mouse working at the same time.

## Chunk 6: Transition recipes and automation

**Goal.** Pre-authored transitions that play automatically over the board's own controls, and that a human can take over at any moment.

**Contract C5: Recipe JSON** (`contracts/recipe.schema.json`). A recipe is lanes of breakpoints in bars relative to the transition start, which is a phrase boundary on the outgoing deck:

```json
{ "id": "bass_swap", "name": "Bass swap", "bars": 16,
  "requires": { "stems": true, "beatmatchable": true, "max_camelot_distance": 1 },
  "events": [ { "at_bar": 0, "deck": "in", "command": "play" } ],
  "lanes": [
    { "deck": "in",  "control": "stem.bass", "points": [[0, 0], [8, 0], [8, 1]] },
    { "deck": "out", "control": "stem.bass", "points": [[0, 1], [8, 1], [8, 0]] },
    { "deck": null,  "control": "xfader",    "points": [[0, 0], [8, 0.5], [16, 1]] } ] }
```

Two points at the same bar mean a step; otherwise values interpolate linearly. "in" and "out" are resolved to A or B at run time.

**Builds.**
- `web/src/coDJ/automation.ts`: a lookahead scheduler (the "two clocks" pattern). A timer every ~25 ms schedules anything due in the next ~100 ms. Bar positions convert to AudioContext time through the outgoing deck's grid and anchor. Lanes write `store.set(id, v, "auto")`.
- Add `engine.scheduleControl(id, value, ctxTime)` for exact-time steps. Otherwise the beat-quantized stem toggles add up to a beat of delay.
- Echo: a per-deck delay with feedback on an FX send, needed for echo-out.
- First recipes: bass swap, drum bridge, filter handoff, echo out, loop roll. The loop roll can use the roll pads' commands (`loop` with beats 1/2 to 1/16), which already slip back in phase on exit (Chunk 5 v0.6).
- A temporary "Try transition" button for testing.

**Takeover rule.** If a human grabs a control during a recipe, that lane belongs to the human until the recipe ends.

**Definition of done.**
- Each recipe plays correctly on the demo tracks.
- Automation steps land within 5 ms of their bar position (assert in the smoke test).
- Grabbing a lane mid-recipe stops automation for that lane.

**v0.1 (2026-10-05).**
- **Contract C5:** `contracts/recipe.schema.json` + `contracts/recipes/*.json`, validated on both sides (pytest `test_recipes.py`, vitest via `assertRecipe`). Lanes are `[bar, value]`; a repeated bar is a step; crossfader values run out→in, so recipes work in both directions.
- **Recipes:** bass swap (EQ, no stems needed), filter handoff, echo out (any key or tempo), loop roll (½→¼→⅛ rolls into a phrase drop), drum bridge (stems).
- **Engine (C2/C3 additions, add-only):**
  - control `${deck}.echo` (send to a ¾-beat delay with feedback, fed after the crossfader and returned to the master, so tails ring out after a cut);
  - commands `playAt`, `pause`, `loopOff`, and `setControlAt` (the planned `scheduleControl`, as a Command so automation still never calls the engine);
  - read-only `barAt` / `barToCtx` / `nextPhraseBar`, which follow a roll's slip clock.
- **`coDJ/automation.ts`:** a 25 ms timer schedules 100 ms ahead.
  - Steps and `playAt` go on the audio clock; ramps go through the store; pause/loop events fire on the first tick at or after their bar, since the engine snaps loops.
  - Lanes on the audible side glide in over the first bar.
  - A human write or hold takes a lane until the end.
  - The outgoing deck's lanes reset to neutral afterwards.
  - A recipe refuses with plain reasons (not playing, stems, drifting tempo, tempo gap, key clash).
- **Temporary UI:** a recipe picker, "Try transition" and a status line in the top bar.
- **Verified:**
  - 11 vitest checks on the player (fake clock): exact steps, glide, both directions, takeover by write and by hold, loop timing, refusals, outgoing deck stopping.
  - `e2e/recipes.py` (~5 min, real Chromium) plays every recipe on the demo tracks (one B→A, one with a mid-recipe grab):
    - every step lands on its bar (worst 0.00 ms on the deck clock; the engine applied each);
    - bars line up during overlaps (worst 0.05 ms);
    - the end state matches each recipe file;
    - the grabbed lane stays with the human;
    - no page errors.
  - Not yet judged by ear.

## Chunk 7: Composed transitions (reworked 2026-10-05)

**Goal.** The AI composes a new transition for each pair from building blocks, instead of picking a preset. It is given a vocabulary of moves plus the musical facts of both tracks, and writes several candidate plans ("drop A to drums on its breakdown, bring in B's vocal over it, echo-throw A's last phrase"). A critic scores them and the best one plays on the Chunk 6 player. Presets stay as the evaluation baseline. *Why the rework:* picking from five fixed recipes needs no AI.

**Pipeline** (`pipeline/plan_transitions.py` → `web/public/library/transitions.json`, contract C6), per ordered pair A → B:

1. **Facts** (`compose/facts.py`). Each track is summarised per phrase, never as raw arrays:
   - section label, energy level and trend, bass weight;
   - vocals (from the vocal stem when there is one, else unknown);
   - key, tempo, bars left.

   Pair facts: tempo ratio (`chooseSync` port), key distance, which decks have stems.
2. **Composer** (`compose/composer.py`): Claude, with structured outputs (strict JSON schema, a union of typed moves). It writes N different candidates. Each is an anchor (A's exit phrase bar, B's entry bar), moves in bars from the transition start, and a one-line rationale citing the facts.
   - On-disk cache keyed by the request hash: reruns are free and reproducible.
   - The API key comes from the environment (offline tooling only; never in the repo or the frontend).
   - Server-side refusal fallback is on.
   - A **rules composer** (templates over the same vocabulary) is the baseline and lets the pipeline run without a key.
3. **Moves vocabulary** (`compose/moves.py`):
   - `in_enter`: B starts at transition bar t, from B's bar m.
   - `crossfade`: staged to a level, smooth or cut.
   - `bass_swap`: on stems if both decks have them, else EQ.
   - `eq`: kill, dip, flat or boost a band, stepped or ramped. This is the "EQ carve".
   - `filter_sweep`: low-pass or high-pass, opening or closing.
   - `echo_throw`: echo build, optional cut, tail rings.
   - `stem_drop`: mute or unmute named stems; only on decks with stems.
   - `loop_roll`: ½→¼→⅛ rolls that slip back.
   - `out_stop`: the end.
4. **Compiler** (`compose/compiler.py`). Moves become a recipe (C5 v2, anchored), or a list of errors:
   - bars outside either track, or past B's end;
   - stem moves without stems;
   - two moves on one control at once;
   - a crossfader that never reaches B;
   - no `in_enter` / `out_stop`.
5. **Critic** (`compose/critic.py`), rules-based and deterministic. It simulates the mix bar by bar from the analysis data (per-band energy and loudness of each deck, through crossfader, volume, EQ, filter and stem gains) and scores, with reasons:
   - **clashes:** bass (both lows strong), vocals (both voiced, when known), key (tonal overlap at Camelot distance > 1);
   - **level:** dips and spikes in total loudness;
   - **structure:** phrase alignment, entry and exit sections, overlap length, plan complexity.

   Jev (TypeSafe System One) can later re-rank the critic's top-k behind the same interface (read its API docs first; needs a key).
6. **Output (C6).** Per pair: compatibility facts, every candidate (source `llm` / `rules`, plan, compiled recipe, critic score, breakdown and reasons) and the best index.

**Web.** The player runs anchored recipes: it waits for A's exit bar and starts B from its entry bar (`playAt.fromBar`). The board loads `transitions.json`, and "Try transition" offers the best composed transitions for the loaded pair before the presets.

**Progress (2026-10-05).** Everything but the live LLM run is built and tested.
- **Recipe contract C5 v2:** anchor, and per-deck stem requirements. The five presets are migrated.
- **`compose/`:** facts, moves, compiler, critic, rules composer, and a Claude composer (`claude-opus-5-5`, structured outputs, prompt-cached system prompt, disk cache, refusal fallback).
- **`plan_transitions.py`** writes C6, with the shared fixture checked from both sides.
- **Board:** `playAt.fromBar`, the anchored player, and a picker with "Composed for this pair".
- **Tests:** 28 pipeline tests for this chunk, 82 web tests, and the browser recipe test playing the demo pair's best composed transition (steps on the bar, B in from its entry bar at 0.0 ms, end state as compiled).
- **Rules composer over the whole library** (240 pairs): every candidate compiles.
  - Tempo-incompatible pairs (141) can only cut, so Echo out wins there.
  - Key-compatible pairs (28): Bass swap blend wins 19.
  - Key-clash pairs (71): cuts win 60.
- **Live composer** (2026-10-05; built by a Codex session, finished and verified here):
  - **The board asks for a transition on demand.** "Compose with AI" sends a pick of the outgoing track's phrase (any future phrase, mid-track included), a maximum length (8/16/32 bars) and a creative brief to `POST /api/composer/compose` (local FastAPI, `backend/app.py`, through Vite's proxy).
  - **The backend** composes with Claude, then compiles, critiques and merges into `transitions.json`. The board pre-selects the best idea for review; nothing autoplays.
  - **Vocabulary v2** adds `volume` (tease, breath before a drop) and `rhythmic_gate` (beat chops). The compiler requires B to end at neutral settings (full volume, flat EQ, filter open, all stems on). Exits and entries may be any phrase with at least 8 bars left.
  - **Guard rails:** the key stays server-side; requests are only accepted from the local board; one composition at a time; track ids are checked against the library; candidate ids are content-hashed, so repeated compositions merge.
  - **Live results:**
    - Demo pair: 4 of 4 ideas compiled and passed the critic (90.9–93.7).
    - zaza "With My Crew" → A P L 0 "EvenFall", exiting mid-track at bar 32: 58 s, about $0.15. 3 of 4 compiled; the rejected one closed two filters on B at once and never reopened them. Ideas: a percussion relay with a loop roll (91.3), gated call-and-response (88.2), stem tease/retract with an echo cut (83.8). The best played end to end in Chromium: 6 steps at 0.00 ms, B in on its bar 32 at 0.00 ms.
  - **Tests:** `pipeline/tests/test_live_composer.py` (HTTP boundary, constraints reach the model, no secrets in errors); `web/e2e/composer.py` (UI and mid-track gated relay, with a canned reply); `web/e2e/composer_live.py` (opt-in, costs one real call).
- **Across genres, tempos and keys** (2026-10-05). The composer was good on similar tracks in one key, poor across genres or keys.
  - **Diagnosis:**
    - Unsyncable pairs (tempo gap > 8%, or a drifting tempo) could never overlap at all, so Claude could only cut or echo out.
    - Keys were judged as written. But the board has no key lock: synced, B is transposed by 12·log2(BPM_A/BPM_B) semitones. 124 vs 128 BPM puts B 55 cents flat whatever its key.
  - **Facts:** per-bar presence of every stem against the mix (`stems.json`), and four-bar windows with `drums` (0 = beatless) and `tonal` (0 = drums only). Pair facts add `tempo.gap_pct`, `tempo.tempo_ride` and `key.when_locked` (the key B is heard in, and the detune in cents).
  - **Critic:**
    - **Keys:** clash only when pitched parts (bass, other, vocals) overlap, judged by the key as heard plus detune.
    - **Rhythm:** when the decks aren't locked, two rhythms together for half a bar or more makes a plan invalid (a train wreck). A beat under a beatless breakdown, or an a cappella over drums, is fine at any tempo.
    - **Ride:** sliding A's pitch under its melody costs a little.
  - **Moves v3:**
    - `tempo_ride`: A glides within its ±8% fader towards B's own tempo, and B syncs the rest as it enters. This reaches pairs up to ~16% apart, and B plays at its own tempo and pitch whenever the gap is ≤ 8%.
    - `brake`: a turntable stop on the audio clock.
    - B may now enter on the bar A stops (a hard cut).
  - **Prompt:** a playbook: drum bridge, breakdown bridge, a cappella handoff, tempo ride, hard switch on the one, half/double time.
  - **Contracts:** Recipe v3 (outgoing `tempo` lane, ramps only; `brake` event), Transitions v3, command `brakeAt` (add-only).
  - **Planner:**
    - It now merges candidates. Rerunning the rules planner used to replace each saved pair and drop its paid LLM candidates.
    - Pairs saved by an older version are recompiled from their plans, and `--recompile` rescores saved plans, both without calls.
  - **Library:** 30 tracks, 14 new: hip-hop, drum & bass (read at half time, 90 BPM), dubstep ×2, trap, deep house, disco, funk ×2, breakbeat ×2, downtempo ×2, ambient. 5 of the new ones drift, so they're unlockable.
    - **How the 870 pairs lock:** 212 directly, 170 more with a tempo ride, 408 involve a drifting track, 80 are too far apart.
    - **Of the 382 lockable pairs:** 42 "compatible" on paper clash as heard, and 22 the other way round.
  - **Live** (4 hard pairs, $0.65, 51–87 s each): 12 of 16 ideas compiled at first. The 4 rejects were brakes landing B on the stop bar, now allowed: 16/16 compile, 14/16 valid. The critic zeroed 2 train wrecks (unlocked beats overlapping for 1.1 and 5.4 bars). Best ideas:
    - **Protohype → CR0SS** (145 vs 128, 5A vs 3A), drums-only tempo ride into B's vocal build, 91.6. The ride transposes B to 5A, A's own key.
    - **Disco → swung hip-hop** (unlockable): A's drums-only outro under B's beatless intro, 92.0; and the "September" a cappella over the hip-hop drums, 91.5.
    - **San Pacho → Kool Karlo** (unlockable): B's vocal teased over A's groove, then an echo cut, 91.4.
    - **Dubstep → deep house:** still a cut (88). Claude's rides kept A's bass audible, and the critic charged the 1.4-semitone slide.
  - **Played in Chromium on the real tracks:**
    - The ride: A at 0.92, B at 1.042, worst phase 0.20 ms over 864 frames.
    - A brake: rate 1.00 → 0.00, then B on the bar.
    - The a cappella: B on A's bar within 1.2 ms, then free tempo by design.
    
    All steps were applied and there were no page errors.
  - **Not judged by ear yet.** In particular: whether 40 cents of detune is the right free allowance, and the rhythm weights.
- **One transition, refined** (2026-10-06; the user asked for one really good transition every time instead of four ideas).
  - **Loop** (`plan_pair(..., refine=2)`, used by the backend and the CLI):
    1. Claude drafts 3 ideas in one call, and the compiler and critic pick the strongest.
    2. While it has a fixable problem, Claude gets it back as a multi-turn revision: compile errors, or the critic's findings with transition bar numbers and its penalties. It returns one improved plan, up to 2 rounds.
    3. The best version across rounds is kept, so a revision that over-corrects can't replace a better draft.
  - **Stopping:** at score ≥ 95, or when no fixable penalty is left. Penalties the pair itself causes (B's stretch after a maximum tempo ride) are marked unavoidable and not sent as things to fix.
  - **Metadata:** composer metadata records each round's score and which round was kept.
  - **Live, 9 compositions:**
    - **Single draft first:** Fils de Luxe → Stars Collide went 85.6 → 84.8 → 89.8 in one run, and 53.5 → 57.2 → 71.1 in another. Draft quality varies too much for "good every time", hence the 3 drafts.
    - **Protohype → Morgan Page:** Claude chased the unavoidable 7% stretch by adding moves (82.6 → 66.6). Hence the unavoidable flag, the 10-move limit stated in the prompt, and "smallest targeted change".
    - **Final setup, 6 pairs:** 85.4–91.8; 2 needed revisions (86.5 → 88; 89.3 → 89.5 → 76.5, kept 89.5). About 50–100 s and $0.12–0.30 each.
    - **Played in Chromium:** 3 of them, steps on the audio clock, locked overlaps within 0.03 ms.
  - **Open question:** on 2 of the 6 pairs, the rules baseline's plain drum bridge outscores Claude's transition (e.g. 96.1 vs 89.5), mostly from the critic's credit for long clean blends and calm exits. The critic isn't calibrated by ear, so a higher score isn't proven better; worth a listening comparison. *Listened (2026-10-06): on Stars Collide → The Longest Road the user preferred Claude's transition (89.5) to the drum bridge (96.1).*
- **Less repetition, more ideas** (2026-10-06; the user: "it really likes doing very similar things over and over… almost a glorified rules").
  - **Measured on 71 saved ideas:** "roll" or "echo" in 35 titles; the same toolkits again and again (strip stems + echo cut ×8, stems + bass swap ×5, highpass + roll + brake ×5).
  - **Causes:** a technique menu in the prompt; the critic's taste applied twice (best-of-3, refinement); no memory between compositions; a small vocabulary.
  - **Changes** (`compose/concepts.py`):
    - **Concept cards:** each draft is built around a different concept, drawn at random from 16 archetypes. These are filtered to what the pair supports (stems, lock, vocals, beatless windows) and skip concepts used recently. Examples: hook loop mash-up, a cappella, vocal preview, false drop, call and response, breakdown swap, intro doorway, energy slam, turntable stop, tempo morph, filter duel, layer by layer, long EQ blend, echo wash, stutter edit.
    - **Memory:** the last 12 compositions (`pipeline/cache/composer_history.jsonl`) go to Claude as `recent_work`, not to be repeated.
    - **Novelty:** the draft to refine is chosen by score minus 8 × its toolkit similarity (Jaccard) to recent work.
    - **Prompt:** the menu became a palette of facts, plus "one signature moment" and "the strip/swap/echo template is a last resort". Revisions keep the concept.
    - **Recomposing gives a fresh idea.** The concept draw makes each request differ, so it isn't a cache hit.
  - **New move: loop** (moves v4). `style` is `roll`, or `hold_1`/`hold_2`/`hold_4`, which loops bars of A, e.g. its vocal hook, while B builds.
    - The engine's `loop` command takes `slip`, so a held loop keeps the bar clock running and lands back in time.
    - In Chromium (`e2e/hook_loop.py`): A's audio stayed in its bars 20–22 and wrapped twice while its clock ran on; after the loop it was 0.00 ms from its clock, and B stayed locked (0.01 ms).
  - **Live, 6 compositions in a row** ($1.76, 15 calls, ~2 min each): 6 different concepts and 6 different toolkits, scored 88.0–95.1. They were a breakdown swap, an echo wash, a vocal preview over a held loop of A, a turntable stop, a layer-by-layer stem relay with a tempo ride, and a long EQ blend.

**Definition of done.**
- The compiler and critic are unit-tested: every move compiles; each rejection reason fires; each critic penalty fires on a synthetic case.
- The composer is tested against recorded responses; a rerun with a warm cache makes zero network calls.
- `transitions.json` validates for every pair, and every candidate carries its plan, recipe, score and reasons.
- In the browser test, a composed transition plays end to end on the demo pair (steps on the bar, end state as compiled).
- Run live on the real library: report cost, the share of LLM plans that compile, and how LLM and rules candidates score under the critic.

## Chunk 8: Backend

**Builds.** `backend/` (FastAPI):
- `GET /api/library` and `GET /api/transitions/{a}/{b}`
- `POST /api/decide`: live Jev proxy, rate-limited
- `POST /api/explain`: LLM, streaming
- `POST /api/session/events`: transition log and ratings (SQLite)

API keys live only here, read from environment variables. Audio files are served from object storage or a CDN, not the backend.

**Definition of done.** OpenAPI docs render, the web app reads transitions through the API, and no key appears in the frontend bundle (grep the build).

## Chunk 9: Live co-DJ and guided mode

**Goal.** The co-DJ reads the live set and either performs the transition or coaches you through it.

**Builds.**
- When the outgoing deck is within ~8 bars of a candidate exit phrase, take the precomputed top-k for (current track → each queued track). Re-rank with one live Jev call using live state: how much the user's hands are moving, set length so far, and recent recipes (to avoid repeats). That's about one call per phrase (~30 s), so cost is negligible.
- **Confidence controls autonomy.** In autopilot, the co-DJ executes if the top option's probability is at least τ_auto; otherwise it suggests. Pick τ_auto from a quick calibration check on logged decisions, not by feel.
- **Modes:**
  - Autopilot: the co-DJ performs the recipe.
  - Guided: a cue card pins to the relevant control ("Bring in B's drums on the next bar"), the user performs it, and timing is graded within a beat.
  - Free: warnings only, for example two vocals about to overlap.

**Definition of done.** A 3-track set plays in autopilot without intervention, and a guided transition can be completed with hands alone.

## Chunk 10: LLM voice

After each transition, the backend sends the LLM a structured fact payload: recipe, keys, BPMs, legality reasons, Jev confidence. It returns one or two sentences in the co-DJ persona. The persona prompt lives server-side, and the model is instructed to use only facts from the payload. Show the text in the UI. TTS is optional and comes later.

**Definition of done.** Explanations never mention a fact absent from the payload (spot-check 20).

## Chunk 11: Evaluation, writeup, deploy

- **Evaluation.** Record transitions from a `MediaStreamDestination` on the master bus: about 15 clips each for Jev's pick, the rules-only baseline, and a random legal pick. Build a blind rating page (random order, 1–5) and store ratings in the backend. Report the mean per condition with bootstrap confidence intervals, and say plainly whether Jev beat the baselines.
- **Writeup.** Cover the AI usage log (what was delegated, what was verified, and how), the architecture, the bugs found and how the tests caught them, the evaluation results, and limitations.
- **Deploy.** Static frontend, audio on object storage, backend on Render. Check licensing for any non-demo audio before making it public. CC BY-ND does not allow derivatives, and stems and mixes are derivatives.

## Plan by session

| Session | Chunks | Cut if behind |
|---|---|---|
| 1 (done) | 0–4 | — |
| 2 | 1b (real tracks, Demucs, calibration) + 5 (hands) | Demucs (use provided stems or mix-only tracks) |
| 3 | 6 (recipes) | Echo-out and loop roll; ship bass swap, drum bridge, filter |
| 4 | 7 (planner + Jev) | Live-API scoring; use cached results only |
| 5 | 8 + 9 (backend, autopilot) | Guided mode |
| 6 | 10 + 11 | Voice before evaluation; the writeup is never cut |

Cut order if time runs out: voice, then guided mode, then live re-ranking (use offline ranks), then extra recipes. Hands and the Jev-planned transitions are the project; protect them.

## Decision log

- **2026-10-03** Served audio is FLAC, not MP3, to avoid encoder-delay grid shifts.
- **2026-10-03** Beat grid: least-squares fit, then a high-resolution phase refinement (hop 64 / n_fft 512). Residual bias is about 4 ms on the demo tracks.
- **2026-10-03** Key detection uses chroma above C2; relative major/minor confusion is accepted (mix-compatible).
- **2026-10-03** Section features are weighted after z-scoring, with energy ×3.
- **2026-10-03** Hand input goes through the same GestureController as the mouse; continuous controls are relative-drag.
- **2026-10-03** `seek()` compensates for the 6 ms declick delay. Found by the browser smoke test (6 ms post-sync offset); now 0.00 ms.
- **2026-10-03** Fonts are self-hosted via @fontsource; no third-party requests at runtime.
- **2026-10-03** `fetch_audius.py` (spec: api.audius.co/v1/swagger.yaml) fetches a track only if `is_downloadable` and `access.download` are both true. Live tests showed the search filter `only_downloadable=true` returning mostly non-downloadable tracks, and roughly half of `is_downloadable` tracks being download-gated. Search uses `has_downloads=true`, which does filter. Downloads are usually 320 kbps MP3; preprocess still serves and analyzes the FLAC made from them, so the analyze-what-you-serve rule holds. The API key goes in `x-api-key` and is stripped on the redirect to the content node.
- **2026-10-04** Beat grid: replaced the least-squares fit through tracked beats with a direct search maximizing S(a, b) = Σ env(a + b·i). The envelope is low-band (<150 Hz) onsets + 0.25 × full-band onsets, each scaled to mean 1, at hop 64 / n_fft 512. b is searched within ±4% of the tracker tempo, because librosa's tempo comes from integer lags at hop 512 and is quantized in ~2.5% steps (123.05 / 129.20 near 125 BPM). The search is coarse (fold modulo b), then fine (0.1 ms). The old ±50 ms phase refinement is subsumed by the fine stage. Why: Frog Prince's tracker slipped half a beat ~4 times, and loud offbeat hats pull a full-band phase search half a beat late (that happened to Zute's old grid).
- **2026-10-04** Beatmatchable is now `max_drift_ms ≤ 10` (`BEATMATCHABLE_MAX_DRIFT_MS`), replacing `grid_residual_ratio < 0.04`. This is TrackAnalysis schema v2 (`grid_residual_ratio` removed, `tempo.max_drift_ms` added) and pipeline 0.2.0.
  - How it's measured: per 32-beat segment, the offset (within ±¼ beat) that maximizes the same envelope.
  - Which segments count: only those with an on-beat kick, meaning an interior low-band peak within ±¼ beat whose max/mean salience is ≥ 1.6 (`DRIFT_MIN_SALIENCE`). Pad breakdowns (salience 1.17) and intros with only offbeat bass (Zute) are skipped.
  - Why 10 ms: steady tracks measure 0.1–5.1 ms across 5 tracks (segment noise at hop 2.9 ms), so 10 ms gives 2× margin. Two decks each within 10 ms keeps worst-case kick misalignment around 20 ms, roughly where flams become audible. A ±25 ms synthetic drift measures 47 ms.
  - Grid choice stays coupled to beatmatchable: regular iff beatmatchable, unless `force_grid`.
- **2026-10-04** Hand input (Chunk 5):
  - **Pinch ratio** uses MediaPipe's 3D metric `worldLandmarks`, not 2D image points, so a tilted palm doesn't shorten the hand length and fake a pinch. Thresholds stay at down < 0.25 / up > 0.35 until tuned on real hands.
  - **Pointer ids** follow position: each hand is matched to the nearest known hand (within 25% of the viewport width), and handedness is only used for a new hand. MediaPipe labels flicker and sometimes give both hands the same label, which would merge two hands into one pointer. Labels are swapped because MediaPipe assumes a mirrored image and we pass the raw camera frame (documented: "handedness is determined assuming the input image is mirrored").
  - **Dropouts:** a hand missing for ≤ 150 ms keeps its grab; after that, `gestures.lost()` releases it.
  - **One Euro** filters in viewport px (minCutoff 1.0, beta 0.01; beta is per px/s).
  - **Self-hosted assets:** the wasm comes from `node_modules` via Vite `?url`. The model (`hand_landmarker.task` float16 v1, md5 15318430…) is fetched once into `web/public/models/` (gitignored) by `npm run fetch-hand-model`, which also runs before dev and build. MediaPipe's JS loads only when the camera is first turned on.
  - **Warm-up:** the first inference compiles GPU shaders and blocks the main thread for ~5 s, so the adapter runs one inference on a blank frame while the UI says "Starting". Inference stats exclude the first 5 frames.
- **2026-10-04** Hand input v0.2:
  - **Contract C4 extended with two optional fields** (non-breaking; C4 has no version number): `PointerSample.angle` (radians, clockwise on screen, only changes used) and `TargetSpec.continuous.twist`. Writer: handTracking. Reader: GestureController.
  - **Pinch metric** is now thumb tip to the index distal segment (7→8). The tips separate in MediaPipe's estimate when the thumb meets the pad at an angle or is occluded. The relaxed-hand thumb rests near the index PIP/MCP, which is why the segment isn't extended past 7.
  - **Thresholds** 0.30 / 0.45 (wider band so twisting, which distorts finger landmarks, doesn't release). Grace 250 ms. MediaPipe minHandPresence and minTracking confidence 0.3 (detection stays 0.5). All are unvalidated on real hands; tune from recordings.
  - **Twist** angle is the 2D least-squares (Kabsch) rotation of the palm landmarks (0, 5, 9, 13, 17) relative to the hand's first-seen shape, in mirrored, aspect-corrected coords. It's absolute rather than accumulated frame to frame, so it can't random-walk, and it's One Euro filtered in degrees (minCutoff 1, beta 0.02).
  - **Twist range** is 150° of hand rotation per full knob range (about ±75° from center, inside a wrist's comfortable ±80°). The drawn knob sweeps 288°, so it turns about 1.9× the hand.
  - **Lock-on** is 56 / 96 / 24 px (lock / unlock / switch margin), hands only, so the mouse stays exact.
- **2026-10-04** Hand input v0.3, from user feedback:
  - **Pinch** is thumb tip to index tip again, down < 0.20 / up > 0.32. The segment metric made relaxed hands press, and the user prefers missing a sloppy pinch to a false press. The 0.12 hysteresis band keeps twists held (twist confirmed to work well).
  - **Lock-on rule:** inside a control (4 px edge margin against border jitter) wins immediately; otherwise nearest within 40 px, 8 px hysteresis, unlock at 56 px. The controls are packed tighter than the v0.2 margins.
- **2026-10-04** Hand input v0.4, from user feedback:
  - **Lock-on removed** (GestureController is back to exact hit-testing for every pointer). On this tightly packed board, even a hand-following lock felt worse than none.
  - **Cursor anchor:** index/middle knuckles (5, 9) instead of the thumb/index tips. Finger motion during a pinch moved the tip midpoint at the moment of the click; the knuckles stay still.
  - **Kept:** twist knobs, the strict tip-to-tip pinch (0.20 / 0.32), and the seek clamp to 0..1.
- **2026-10-04** Hand input v0.5:
  - **The cursor ignores rotation about the pinch point** (correction captured at each pinch start; it persists through release and bleeds off with open-hand travel: 200 px e-folding, 2 px/frame deadband).
  - **Releases need the pinch open ≥ 100 ms** (`releaseHoldMs`). Twist flickers otherwise triggered the double-press reset and double-fired pads. The cost is up to 100 ms of release latency, during which the hand sends no samples.
- **2026-10-04** Hand input v0.6 + layout, from user feedback:
  - **Knob-only lock-on** (36 / 52 / 8 px). Twisting needs the cursor pinned to the knob, while buttons and sliders felt better exact.
  - **Hands can't seek** on the overview. It's the most destructive accidental pinch; jump and cue cover it.
  - **Layout rules for hands:** targets ≥ 44 px tall and ≥ 14 px apart; risky controls (tempo, Cue, jumps) on outer edges and away from frequent ones; rows ordered by use in a transition; rare settings at the top edge.
- **2026-10-04** Rolls (loops under a beat) slip back on exit via a per-deck `slip` anchor, so a synced deck stays in phase. Loops of a beat or more keep classic behavior.
- **2026-10-04** `Deck.seek(pos, at)`: a seek target computed from a clock read must pass that read. `currentTime` can advance a render quantum within one task (seen as ~3 ms phase errors in the smoke test).
- **2026-10-04** Hand input v0.7:
  - **Pinch press** = tip ratio < 0.25 and (tip − joint) < 0.1; release > 0.35. The joint guard separates a relaxed thumb from a sloppy pinch, which tip distance alone can't.
  - **User-adjustable grab threshold** (0.18–0.32, localStorage) because hands and cameras differ.
  - **Twist:** no hand double-press reset; palm reference and angle filter re-taken at each pinch; ends re-base.
- **2026-10-04** Beat grid phase comes in two steps.
  - **Which position is the beat:** chosen by the kick-weighted search (robust to offbeat hats).
  - **Where exactly it is:** a full-band onset search within ±⅛ beat (`ATTACK_WINDOW_BEATS`), so the grid sits on the attack you hear, not a swelling kick's low end. The window is wide enough for a ~50 ms swell lag at 128 BPM (58 ms) and excludes 16ths (¼ beat) and offbeats (½).
  - **Drift** is measured on the same envelope and window. On real tracks: 11 of 14 beatmatchable (was 5 of 14), demo ground truth 4.2 / 4.6 ms (limit 10). Pipeline 0.2.1.
- **2026-10-04** `analysis.json` beats are stored to 1 µs (was 0.1 ms) because sync derives its rate from the local beat interval. Pipeline 0.2.2. Not a schema change (precision isn't specified).
- **2026-10-04** The smoke test loads the demo tracks by title, not "the first two rows". Real tracks now sort before them in the library.
- **2026-10-05** Chunk 6, transition recipes:
  - **Recipe clock:** recipe bars run on the outgoing deck's grid from a phrase boundary at least 1 bar ahead. During a roll, the slip anchor keeps that clock running.
  - **Exact steps and starts** (`setControlAt`, `playAt`) are scheduled on the audio clock. The store follows by timer, which only does bookkeeping. Ramps are written to the store each 25 ms tick and smoothed by the engine (12 ms).
  - **pause / loop / loopOff fire just after their bar**, never before: the engine floors loop starts to the grid, so early would loop the previous unit.
  - **Echo:** ¾ beat, feedback 0.45, band-limited repeats (250 Hz–5 kHz), fed post-crossfader and returned straight to the master.
  - **The incoming deck syncs at arm time** when the recipe requires beatmatching, and `playAt` starts it from its nearest bar line.
- **2026-10-05** Browser tests run Chromium with `--disable-audio-output` (the real Web Audio graph renders into a fake sink). With the Mac's output device in a bad state, even an empty AudioContext's clock stalled (5 ms per second) and every timing check failed. Tests also no longer play through the speakers.
- **2026-10-05** The smoke test's hand check asserts that inference runs (≥ 10 frames), no longer ≥ 10 fps. Headless Chromium runs the model on a software GPU whose speed follows machine load: the same code measured 15 and 3 fps a minute apart, and the pre-Chunk-6 commit measured 3 fps too. Real cost is measured headed (9.6 ms on an M3 Max).
- **2026-10-05** Chunk 7 reworked: the AI composes per-pair transitions from a moves vocabulary (Claude, structured outputs), a deterministic compiler turns them into recipes, and a rules critic simulates the mix and scores them. Jev can re-rank later. The old plan (Jev scoring preset triples) needed no AI to pick among five presets.
- **2026-10-05** Critic (provisional, to calibrate by ear in Chunk 11):
  - **Bass clash** counts the bass part, not the low band, because overlapping kicks are just a blend. The first version flagged a correct bass swap.
  - **Clean blending earns credit:** +0.5 per clean overlap bar, up to 16 bars, from a base of 88 so bonuses show. Before this, a cut couldn't be faulted and won 172 of 240 pairs, tying a clean blend at the cap.
  - **Caught a real preset flaw:** Filter handoff brings B in through a low-pass that keeps its bass.
- **2026-10-05** Demucs stems are separated from the mix at −6 dB with `--clip-mode none`, and the playback gain is recorded in TrackAnalysis v3 `audio.stems_gain_db` (pipeline 0.3.0). Reason: on loud masters a stem can peak above full scale, and Demucs's default then scales that whole stem down; drums came out 2.0–2.4 dB low, so the stems rebuilt the mix only to −15 dB. Integer FLAC can't hold values above 1.0 either. Now the rebuild reaches −29 to −35 dB with stem peaks ≤ 0.65 (`tests/test_stems.py`). Re-analysis keeps already-separated stems unless `--stems` is passed again.
- **2026-10-05** DC offset is removed before and after Demucs. Demucs adds its input's mean back to every stem, so CR0SS (DC −0.017) got stems summing to 4× the offset and rebuilt only to −11 dB, and muting a stem with DC thumps. Now −26 dB with stem DC ≈ 0; `test_stems.py` covers it.
- **2026-10-05** Vocal presence (composer and critic facts) is the vocal stem's level against the mix per bar: 0 at −24 dB, 1 at −14 dB. The first version measured against the stem's own loud bars, so bleed in an instrumental track (45–65 dB under the mix) read as vocals everywhere. Real vocals measured 3–17 dB under the mix; the synthetic demo's vocal bars come out exactly right.
- **2026-10-05** Live composer:
  - **Mid-track transitions:** exits and entries can be any phrase with at least 8 bars left. Previously exits had to be in A's second half and entries in B's first.
  - **Vocabulary v2:** `volume` and `rhythmic_gate`; a gate owns its deck's volume lane for its whole interval.
  - **B ends neutral:** the compiler rejects plans that leave B altered (volume, EQ, filter or a stem) at the end.
  - **Transitions v2:** content-hashed candidate ids.
  - **Local endpoint only:** loopback origin, one composition at a time. Not a public multi-user service; that's Chunk 8.
  - **Cost:** about $0.15 per 4-idea composition with Opus 5.5 (input ≈3k tokens + 5.6k cached system prompt, output ≈5.7k tokens), about 40–60 s.
- **2026-10-05** Across genres and keys:
  - **Keys as heard.** The board has no key lock, so a locked deck is transposed by the tempo ratio. Facts and critic compare the key B is heard in (`mix.key_relation`), and detune costs nothing up to 40 cents, rising to half a key step at 60. Calibrated on the user's report that zaza ↔ EvenFall blends (41 cents) "worked really well". Re-check by ear the demo pair (55 cents) and TEKTULA → Protohype (61 cents). The real fix is key lock (a time-stretching player, e.g. an AudioWorklet), which would also allow key shifting.
  - **Stem presence:** every stem's level against the mix, per bar, on the vocal scale (−24..−14 dB → 0..1). It's cached in `stems.json`, which replaces `vocals.json`.
  - **Rhythm clash** (only when the decks aren't locked): each deck's rhythmic level is max(drums, 0.7·bass, 0.5·other), from presence × stem gain through EQ and filter. Both decks above 0.3 counts as a clash, and half a bar or more is invalid. Without stems, any unlocked overlap is invalid, as before.
  - **Tempo ride:**
    - A's tempo lane ramps (≤ 1% per bar) to B's own tempo, clamped to ±8%. B enters at or after the end and syncs then; the player lands the ride on its final value first.
    - It's a ramp only, because the engine applies tempo when written, not as an exact step.
    - Before its first change the player keeps A's current tempo, so a deck already pitched doesn't snap to 0.
  - **Brake:** a playbackRate ramp to 0 on the audio clock. The deck's anchor is left alone, so the bar clock that times the rest of the recipe and B's entry runs on. The compiler adds a volume step to 0 at the brake's end, because a stopped source holds its last sample (DC).
  - **B may enter on A's stop bar.** All 4 of Claude's first brake plans did this, and the old rule threw them away.
  - **The planner merges** new candidates into saved pairs (by content-hashed id), the same as the backend, and recompiles pairs from an older transitions version from their stored plans.
- **2026-10-05** Track selection: `fetch_audius.py` adds `--sort popular`, `--min-plays` and a 1.5–10 minute window (skips DJ mixes and skits). A plain genre search returned four 25–108 minute mixes, a skit, and barely played uploads the user rejected by ear. Plays are a rough quality signal; vocals are confirmed after preprocessing from the stems, not the title.
- **2026-10-05** `bpm_hint` overrides for 3 new tracks whose tempo the tracker misread: Broey (116.7, i.e. ⅔ of 175 → 175), Trivecta "Alaska" (156.6 → 150), Sheco "AM:PM" (118.8 → 125). Each was checked against an onset autocorrelation. Broey and Sheco then measure 12.1 and 11.5 ms of drift, just over the 10 ms beatmatchable limit, so they stay unlocked. Whether the limit is too tight for fast or busy genres is untested; check by ear before changing it.
- **2026-10-06** The live composer returns one transition: best of 3 drafts, then up to 2 critic-guided revisions (`REFINE_ROUNDS`, `GOOD_SCORE = 95`). The critic's reasons now name transition bars (hits under half a bar apart are merged), so revisions can target them. Composer request v1 unchanged except the `candidates` default (now 3 = drafts). The Vite proxy timeout is now 480 s, since up to 3 calls run in one request.
- **2026-10-06** Claude's compositions rank above the rules baseline (board list, pre-selection, and `best` in transitions.json), each best score first. Reason: by ear, the composed Stars Collide → The Longest Road beat a rules drum bridge the critic scored 6.6 points higher. The critic's weights are unchanged (one comparison); its clean-blend credit (up to +8) is the first suspect when calibrating in Chunk 11.
- **2026-10-06** The structured-output schema is capped by the API ("compiled grammar is too large"), and a 14th move type crossed the cap. Rolls and held loops share one `loop` move (`style`) in what Claude writes; the compiler converts it to its internal `loop_roll` / `loop_hold`, which older saved plans also use. New moves must stay within 13 types, or merge like this.
- **2026-10-06** Creativity: concept cards per draft (random, feasible, not recently used), the last 12 compositions as `recent_work`, and a novelty term (8 points × toolkit similarity) when choosing which draft to refine. Recomposing the same pair deliberately isn't cached. Automation loops always slip and snap to the grid (`loop.slip`, add-only in C3).
- **2026-10-06** Structure detection (TrackAnalysis v4, pipeline 0.4.0), from the stems.
  - **Labels**, by rule, with no ground truth on real tracks:
    - A peak is a section at ≥ 0.8 of the loudest section's energy (0.85 demoted every earlier peak of Stars Collide, whose final drop is the loudest).
    - Drop: a peak after a build or breakdown, or the beat slamming back after beatless bars. A groove filling out (a drums-only intro into the full groove) is not a drop.
    - Chorus: otherwise, a sung peak in a repeated group.
    - Breakdown: drums presence < 0.3.
    - Build: a section rising ≥ 0.12 into a peak.
  - **Build cues:** the longest of 16/8/4 bars before a drop whose 2-bar energy steps never fall and rise ≥ 0.12 overall. Builds add layers in steps; a straight-line fit rejected Stars Collide's staircase.
  - **Drop cues** mark only entries into a peak.
  - **Hooks:** phrases on the 2/4-bar grid, vocal similarity ≥ 0.95 (0.92 made every sung bar of a looped progression "the hook"), instrumental ≥ 0.92, preferring phrases in peak sections.
  - **Checks:** ground truth on the demo tracks (labels, breakdown, drop and vocal cues, the 2-bar vocal hook) and synthetic arrangements (EDM, song, no stems, hook). Real tracks still need spot-checking by ear: the overview shows the cues.
- **2026-10-06** The composer gets each track's sections, cues and hooks, plus prompt guidance:
  - land B's drop where A's build would pay off;
  - loop a real hook;
  - exit at a vocal_out, never mid-hook;
  - keep B's vocal_in clear of A's vocals;
  - the bar arithmetic between A's, B's and transition bars.

  Two concepts need structure: "build handoff" (A has a build, B a drop) and "hook mash-up" (B has a vocal hook). "Hook loop" now needs a detected hook on A, and "false drop" a drop on B. The critic adds +3 when B's drop hits with B's drums and bass fully in.
  - **Live** (4 pairs, $1.35, 10 calls): Claude used the structure. Examples:
    - A "false drop" whose roll lands where A's own peak would have hit (93.0).
    - Exiting after Lissie's vocal hook finishes, with Crypto's beatless intro as the doorway (90.6).
    - Charmae's 2-bar hook placed at the exact transition bar, over A's vocal-free drop, on drums only (91.6).
    - One weak result: a breakdown swap at 72.9 after 3 rounds, because of level dips.

    Two played in Chromium: all steps applied, locked overlaps within 0.03 ms.
- **2026-10-06** Key lock and key shift (supersedes "keys as heard" above).
  - **What:** each deck ends in a key stage, a real-time pitch shifter (Signalsmith Stretch, MIT, WASM AudioWorklet in live-input mode). With key lock on (global toggle, default on) it shifts the deck by `key shift − 12·log2(rate)`. Tempo changes keep the key, and `−`/`+` on a deck transposes it by semitones (±6).
  - **The transport is untouched:** sync, rides, loops and scheduling all work as before. The brake still dives, because it ramps playbackRate without changing the nominal rate.
  - **Placement:** after the crossfade gain, so every control and automation step stays aligned with the content. The echo taps after it, so repeats are in key.
  - **Latency:** the shifter adds exactly its block length, 80 ms (measured with clicks at 40/60/120 ms: latency equals the block). So both decks pass through the same delay while key lock is on: the shifter when there's a shift to apply, otherwise a DelayNode of the same length (transparent), crossed over in 20 ms. Pitch changes are scheduled at output time t + latency, for the content they belong to.
  - **Display:** waveforms draw the heard position (playhead − latency).
  - **Off:** key lock off removes the latency (both decks together, with a short fade).
  - **Why 80 ms:** 120 ms felt laggy; 40 ms is a short window for bass.
  - **Cost:** about 1% of a core per deck (6 s rendered in ~60 ms).
  - **Measured in Chromium** (`e2e/key_lock.py`, bass pitch of a looped bar):
    - +8% tempo with key lock off: ×1.0827 (expect 1.08).
    - With key lock on: ×1.0000.
    - A +2 semitone shift: ×1.1134 (expect 1.1225, about 14 cents flat).
  - **Loading:** the library builds its worklet from its own functions' source text. Vite's dev pre-bundling and the production minifier both broke it, and it never started. It's excluded from pre-bundling and loaded at run time from the published file (`?url`). The chip shows "unavailable" if the shifter fails or doesn't start within 10 s. Checked in dev and a production build, with no third-party requests.
  - **Composer:** keys meet as written; plans gain `in_key_shift` (−2..+2, moves v5), applied to B before it enters and kept. Facts list `shift_options` and `best_in_key_shift`. The critic judges keys after the shift, no longer charges for pitch slides during rides, and the "stretch" penalty is halved (a feel change, not a pitch change).
  - **The prompt now pushes shifting over hiding pitched parts behind drums.** The rules baseline uses the best shift for its blends.
  - **Assumption:** the composer assumes key lock is on. With it off, composed transitions sound as before key lock.
  - **Live** (3 pairs whose keys clash by 2–3 steps, $0.56):
    - Protohype → Stars Collide: B shifted +2 to A's 5A, A rides to 128 and stutters into B's peak vocal (89.2).
    - Morgan Page → Crypto: B shifted −2 to 2A, A's 12-bar build pays off with Crypto's drop (94.4; the drop lands in full).
    - Fils de Luxe → Morgan Page: a turntable stop (88.0), no shift needed.

    None has key-clash bars. In Chromium, Crypto (4A) played heard as 2A (−2 st) beside A's 2A, synced within 0.01 ms.
  - **Bug found and fixed** (test first): the planner stored only out_start_bar and moves, so `in_key_shift` was lost from saved plans (recompiles and revision turns). 674 stored plans, mostly rules blends, were restored from their compiled key lanes.

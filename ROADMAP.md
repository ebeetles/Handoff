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
        │  pipeline/plan_transitions.py   (Chunk 7: legality in code, scoring by Jev)
        ▼
  library/transitions.json  ◄── contract: TransitionPlan (Chunk 7)

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
| C1 | TrackAnalysis v2 | `contracts/track_analysis.schema.json`, mirrored in `web/src/contracts/track.ts`, fixture in `contracts/fixtures/` | pipeline → web, planner |
| C2 | Control ids + 0..1 semantics | `web/src/control/controls.ts` | inputs, automation → engine, UI |
| C3 | Command union | `web/src/control/commands.ts` | inputs, co-DJ → engine |
| C4 | PointerSample | `web/src/input/gesture.ts` | input adapters (mouse, hands) → GestureController |
| C5 | Recipe JSON (Chunk 6) | `contracts/recipe.schema.json` (to create) | authored by hand → automation player, planner |
| C6 | TransitionPlan (Chunk 7) | `contracts/transitions.schema.json` (to create) | planner → backend, co-DJ |
| C7 | HTTP API (Chunk 8) | `backend/` OpenAPI (auto from FastAPI) | backend → web |

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
| 6 Transition recipes + automation | Not started |
| 7 Candidates + offline Jev scoring | Not started |
| 8 Backend | Not started |
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
- Sections, via bar-level self-similarity and checkerboard novelty, with rough labels.
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

**1b: next upgrades (do with real tracks, before Chunk 7):**
- [ ] Run on 10–20 real tracks across the genres you'll actually mix. Calibrate `BEATMATCHABLE_MAX_DRIFT_MS` / `DRIFT_MIN_SALIENCE` and the section novelty threshold, and record the values and reasons in the decision log below.
- [ ] Downbeat accuracy on real music is the weakest point. Spot-check every track in the board: the phrase lines on the waveform should land on obvious changes. Fix with `downbeat_shift`, and consider a neural downbeat tracker (e.g. Beat This!) if more than ~20% need fixing.
- [ ] Run Demucs on the real tracks (overnight or on Colab) and check that stem lengths match the mix.
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
- Changing tempo also shifts pitch (no key lock). The planned fix is offline Rubber Band renders for each planned transition.
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

## Chunk 7: Candidates and offline Jev scoring

**Goal.** For every ordered pair of tracks, a ranked list of good transitions, decided offline where it's cheap and deterministic.

**Builds.** `pipeline/plan_transitions.py` → `library/transitions.json` (contract C6).

1. **Legality, in code.** Choose a BPM ratio m in {1, ½, 2} within ±8% (port `chooseSync` and test both ports against `contracts/fixtures/sync_cases.json`). Then apply Camelot distance, beatmatchable flags, and stem availability. The output is the legal (recipe, exit phrase, entry phrase) triples, each with machine-readable reasons.
2. **Candidate points.** Exit phrases are the last 3 phrases of A plus any phrase that starts a low-energy section. Entry phrases are the first 2 phrases of B plus the phrase before B's first high-energy section.
3. **State for Jev: compact and discretized, never raw arrays.** Per phrase: section label, energy trend (e.g. "high→falling"), bass heaviness, vocals present, Camelot key, tags. Precomputed facts: key distance, BPM difference in %, ratio m.
4. **Jev questions per pair.** A `choice` over candidates (at most 255; chunk if more), a `score` for smoothness (5 levels), and a `noul` for energy clash. Keep the full probability distributions.
5. **Decision client.** `DecisionClient(base_url, api_key, model)` posting System One requests. Cache on disk by hash of the request body, so reruns are free and reproducible, and record latency. Before writing it, read TypeSafe's API docs for the endpoint, auth, limits, and pricing. Don't guess. Kev's README shows the request and response shape.
6. **Baselines, stored alongside for evaluation.** A rules-only ranker (closest BPM, then key, then last phrase into first phrase) and a random legal pick.

**Definition of done.**
- `transitions.json` validates against its schema for every pair.
- Every candidate carries its legality reasons and Jev's probabilities.
- A rerun with a warm cache makes zero network calls.

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

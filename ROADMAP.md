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
| C1 | TrackAnalysis v1 | `contracts/track_analysis.schema.json`, mirrored in `web/src/contracts/track.ts`, fixture in `contracts/fixtures/` | pipeline → web, planner |
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
| 5 Hand input | Not started |
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

- A beat grid. It beat-tracks, fits t_i = a + b·i by least squares, and, if the tempo is steady (RMS residual under 4% of a beat), replaces the tracked beats with the fitted line. It then refines the phase on a high-resolution onset envelope and extends the grid over the whole track.
- The downbeat phase, from low-band onsets, harmonic change, and overall onset strength.
- Key, via Krumhansl-Kessler profiles on chroma above C2, mapped to Camelot.
- Per-bar band energy (low, mid, high) and loudness.
- Sections, via bar-level self-similarity and checkerboard novelty, with rough labels.
- 16-bar phrases and a 3-band display waveform.

Stems come from Demucs (`--stems`) or from `<name>.stems/` folders. Per-file fixes go in `overrides.json`.

**Verified.** 8 pytest checks on two synthetic tracks with known answers:

- BPM within 0.1.
- Downbeat within 10 ms (measured about 4 ms).
- Camelot number correct.
- Exact section boundaries on one track.
- Schema validity, phrase coverage, and index consistency.

**Bugs the ground truth caught** (keep these in mind when editing):

1. Median onset aggregation gave 2/3× and 1/2× tempo errors.
2. Kick fundamentals leaked into chroma and produced wrong keys; fixed with chroma above C2.
3. The beat grid sat 28 ms late from a 2048-sample window; a high-resolution phase pass brought it to about 4 ms.
4. Weighting features before z-scoring undid the weights, so a breakdown was missed.

**1b: next upgrades (do with real tracks, before Chunk 7):**
- [ ] Run on 10–20 real tracks across the genres you'll actually mix. Calibrate `BEATMATCHABLE_MAX_RESIDUAL_RATIO` and the section novelty threshold, and record the values and reasons in the decision log below.
- [ ] Downbeat accuracy on real music is the weakest point. Spot-check every track in the board: the phrase lines on the waveform should land on obvious changes. Fix with `downbeat_shift`, and consider a neural downbeat tracker (e.g. Beat This!) if more than ~20% need fixing.
- [ ] Run Demucs on the real tracks (overnight or on Colab) and check that stem lengths match the mix.
- [ ] Optional: CLAP vibe tags per phrase, as strings in a new `tags` field (schema v2).

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
- First recipes: bass swap, drum bridge, filter handoff, echo out, loop roll. The loop roll needs loop sizes 1/2, 1/4, and 1/8 beat; the engine already accepts fractional beats.
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

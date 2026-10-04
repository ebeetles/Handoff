# Rules for coding agents (Claude Code, Codex, and humans)

Read `ROADMAP.md` first. It has the architecture, the contracts between parts, and the status of every chunk.

## How to work here

1. **One chunk at a time.** Stay inside the chunk you were asked to build. If you need something from another chunk, use its contract. Don't reach into its internals.
2. **Contracts are the seams.** These files define how the parts talk:
   - `contracts/track_analysis.schema.json` + `web/src/contracts/track.ts` + `contracts/fixtures/`
   - `web/src/control/controls.ts` (control ids, 0..1 values), `web/src/control/commands.ts`
   - `web/src/input/gesture.ts` (`PointerSample`, `TargetSpec`)

   To change one, bump its version, update every writer and reader in the same change, regenerate fixtures, and run all test suites. Never change one side alone.
3. **Done means tested.** Run the checks below and include their output in your summary. If a check can't run (no browser, no GPU), say so explicitly. Don't describe untested work as working, and only update chunk status in `ROADMAP.md` when its definition of done is met.
4. **Don't guess external APIs.** For TypeSafe/Jev, MediaPipe, Demucs, or LLM APIs, read the current docs before writing a client. If you can't, stop and ask.

## Checks

```bash
cd pipeline && python -m pytest -q          # ground-truth analysis tests (~1 min)
cd web && npm run typecheck && npm test     # strict TS + timing-math unit tests
cd web && npm run dev                       # then, in another shell:
python web/e2e/smoke.py                     # real-browser audio: launch, sync, loops, jumps, no errors
```

The smoke test needs a library with two beatmatchable tracks. The demo tracks work: see README.

## Audio rules (where the subtle bugs live)

- **One clock.** Schedule every audio event in `AudioContext.currentTime`. Never use `setTimeout`/`setInterval` to *trigger* sound. Timers may only drive a lookahead scheduler that schedules ahead on the audio clock.
- **Re-anchor before changing transport state.** Deck position is `pos + (now − ctxTime) · rate`. Any change to rate, loop, or position must go through `reanchor()` (or `seek()`), or the playhead jumps.
- **`seek(pos)` means "be at pos now."** It already compensates for the declick fade. Don't add your own offsets.
- **Discontinuities fade.** Stopping or starting sources mid-signal clicks. Use the deck's declick path.
- **Analyze what you serve.** The pipeline analyzes the exact FLAC the browser plays. Never serve MP3/AAC/Opus without measuring the decode offset against the beat grid.
- **Beats, bars, phrases come from the grid** (`BeatGrid`), never from `60 / bpm` arithmetic. Real tracks aren't perfectly uniform.

## Architecture rules

- Inputs (mouse, keyboard, hands, automation) only write to `ControlStore` or dispatch `Command`s. They never call deck or engine methods.
- The UI reads `engine.snapshot()` and the store. It never mutates the engine. Canvases may read deck position directly for 60 fps drawing.
- Automation writes with source `"auto"`. The store drops `auto` writes to controls a human is holding. Keep it that way.
- API keys live only in the backend (Chunk 8). Never put a key in frontend code or in the repo.

## Hygiene

- TypeScript is strict. No `any`, no `@ts-ignore` without a comment saying why.
- Python: type hints, and pure functions in `analyze.py`. Shell-outs go in `audio_io.py`.
- Don't commit audio, generated libraries, or `.env` files (see `.gitignore`).
- When you fix a bug a test should have caught, add the test first, then fix.
- Record any threshold change or design decision in the decision log at the bottom of `ROADMAP.md`.

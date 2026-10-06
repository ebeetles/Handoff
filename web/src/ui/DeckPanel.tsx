import type { CSSProperties } from "react";
import type { DeckSnapshot } from "../audio/engine";
import { TEMPO_RANGE } from "../audio/sync";
import type { DeckId } from "../control/controls";
import { STEM_NAMES } from "../contracts/track";
import { Fader, Pad, StemTile } from "./Controls";
import { Overview } from "./Overview";
import { PhraseRing } from "./PhraseRing";

const STEM_LABEL: Record<string, string> = { drums: "Drums", bass: "Bass", vocals: "Vocals", other: "Melody" };
const ROLLS = [1 / 2, 1 / 4, 1 / 8, 1 / 16];

// Laid out for hands (see ROADMAP Chunk 5, v0.6). Each deck mirrors the other so a hand works
// its own side: rows run from most to least used in a transition (transport, parts, loop,
// roll, cue & jump); Sync sits on the mixer side of Play; Cue (stops the deck) sits on the
// outer edge, away from Play; the tempo fader sits on the outer edge, off the path between
// the pads and the mixer, so a hand on its way to the mixer can't knock the deck out of sync.
const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function DeckPanel({ deck, s }: { deck: DeckId; s: DeckSnapshot }) {
  const off = !s.loaded;
  const status = s.error ?? s.loading ?? (s.loaded
    ? `${s.sectionLabel ? s.sectionLabel[0]!.toUpperCase() + s.sectionLabel.slice(1) + " section, " : ""}phrase ${s.phraseIndex + 1} of ${s.phraseCount}${s.beatmatchable ? "" : ". Tempo drifts, so sync may slip."}`
    : "Empty. Open the library to load a track.");
  return (
    <section className={`panel deck deck-${deck.toLowerCase()}`} aria-label={`Deck ${deck}`}>
      <Overview deck={deck} />
      <div className="deck-top">
        <PhraseRing s={s} />
        <div className="track-meta">
          <div className="track-title">{s.loaded ? s.title : `Deck ${deck}`}</div>
          <div className="track-artist">{s.loaded ? s.artist : "\u00a0"}</div>
          <div className="readouts">
            <div className="readout-big">{s.loaded ? s.bpm.toFixed(1) : "–"}</div>
            <div className="readout key-readout">
              <small>Key{s.loaded && Math.abs(s.pitchSemitones) >= 0.05 ? ` ${s.pitchSemitones > 0 ? "+" : ""}${Number.isInteger(s.pitchSemitones) ? s.pitchSemitones : s.pitchSemitones.toFixed(1)}` : ""}</small>
              {s.loaded ? (s.heardCamelot !== s.camelot ? `${s.camelot}→${s.heardCamelot}` : s.camelot) : "–"}
              <span className="key-shift">
                <Pad small command={{ type: "keyShift", deck, semitones: -1 }} disabled={off}>−</Pad>
                <Pad small command={{ type: "keyShift", deck, semitones: 1 }} disabled={off}>+</Pad>
              </span>
            </div>
            <div className="readout"><small>Tempo</small>{s.loaded ? `${((s.rate - 1) * 100).toFixed(1)}%` : "–"}</div>
            <div className="readout"><small>Left</small>{s.loaded ? fmtTime(Math.max(0, s.duration - s.position)) : "–"}</div>
          </div>
          <div className={`status-line ${s.error ? "error" : ""}`}>{status}</div>
        </div>
      </div>
      <div className="deck-controls">
        <div className="pad-area">
          <div className="pad-row transport-row" style={{ "--cols": 3 } as CSSProperties}>
            <Pad wide command={{ type: "togglePlay", deck }} active={s.playing && !s.pending} pending={s.pending} disabled={off}>
              {s.pending ? "Waiting" : s.playing ? "Pause" : "Play"}
            </Pad>
            <Pad command={{ type: "sync", deck }} active={s.synced} disabled={off}>Sync</Pad>
          </div>
          <div className="pad-group-label">Parts</div>
          <div className="pad-row" style={{ "--cols": 4 } as CSSProperties}>
            {STEM_NAMES.map((st) => (
              <StemTile key={st} id={`${deck}.stem.${st}`} label={STEM_LABEL[st]!} disabled={!s.hasStems} />
            ))}
          </div>
          <div className="pad-group-label">Loop (beats)</div>
          <div className="pad-row" style={{ "--cols": 4 } as CSSProperties}>
            {[1, 2, 4, 8].map((b) => (
              <Pad key={b} small command={{ type: "loop", deck, beats: b }} active={s.loop?.beats === b} disabled={off}>{b}</Pad>
            ))}
          </div>
          <div className="pad-group-label">Roll (beats; slips back in time when you let go)</div>
          <div className="pad-row" style={{ "--cols": 4 } as CSSProperties}>
            {ROLLS.map((b) => (
              <Pad key={b} small command={{ type: "loop", deck, beats: b }} active={s.loop?.beats === b} disabled={off}>1/{1 / b}</Pad>
            ))}
          </div>
          <div className="pad-group-label">Cue and jump (beats)</div>
          <div className="pad-row cue-row" style={{ "--cols": 5 } as CSSProperties}>
            <Pad small command={{ type: "cue", deck }} disabled={off}>Cue</Pad>
            {[-16, -4, 4, 16].map((b) => (
              <Pad key={b} small command={{ type: "jump", deck, beats: b }} disabled={off}>{b > 0 ? `+${b}` : `−${-b}`}</Pad>
            ))}
          </div>
        </div>
        <div className="tempo-col">
          <Fader id={`${deck}.tempo`} label="Tempo" readout={(v) => `${((v - 0.5) * 2 * TEMPO_RANGE * 100 >= 0 ? "+" : "")}${((v - 0.5) * 2 * TEMPO_RANGE * 100).toFixed(1)}%`} />
        </div>
      </div>
    </section>
  );
}

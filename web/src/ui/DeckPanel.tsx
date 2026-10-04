import type { CSSProperties } from "react";
import type { DeckSnapshot } from "../audio/engine";
import { TEMPO_RANGE } from "../audio/sync";
import type { DeckId } from "../control/controls";
import { STEM_NAMES } from "../contracts/track";
import { Fader, Pad, StemTile } from "./Controls";
import { Overview } from "./Overview";
import { PhraseRing } from "./PhraseRing";

const STEM_LABEL: Record<string, string> = { drums: "Drums", bass: "Bass", vocals: "Vocals", other: "Melody" };
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
            <div className="readout"><small>Key</small>{s.camelot || "–"}</div>
            <div className="readout"><small>Tempo</small>{s.loaded ? `${((s.rate - 1) * 100).toFixed(1)}%` : "–"}</div>
            <div className="readout"><small>Left</small>{s.loaded ? fmtTime(Math.max(0, s.duration - s.position)) : "–"}</div>
          </div>
          <div className={`status-line ${s.error ? "error" : ""}`}>{status}</div>
        </div>
      </div>
      <div className="deck-controls">
        <div className="pad-area">
          <div className="pad-row" style={{ "--cols": 3 } as CSSProperties}>
            <Pad command={{ type: "togglePlay", deck }} active={s.playing && !s.pending} pending={s.pending} disabled={off}>
              {s.pending ? "Waiting" : s.playing ? "Pause" : "Play"}
            </Pad>
            <Pad command={{ type: "cue", deck }} disabled={off}>Cue</Pad>
            <Pad command={{ type: "sync", deck }} active={s.synced} disabled={off}>Sync</Pad>
          </div>
          <div className="pad-group-label">Loop (beats)</div>
          <div className="pad-row" style={{ "--cols": 4 } as CSSProperties}>
            {[1, 2, 4, 8].map((b) => (
              <Pad key={b} small command={{ type: "loop", deck, beats: b }} active={s.loop?.beats === b} disabled={off}>{b}</Pad>
            ))}
          </div>
          <div className="pad-group-label">Jump (beats)</div>
          <div className="pad-row" style={{ "--cols": 4 } as CSSProperties}>
            {[-16, -4, 4, 16].map((b) => (
              <Pad key={b} small command={{ type: "jump", deck, beats: b }} disabled={off}>{b > 0 ? `+${b}` : `−${-b}`}</Pad>
            ))}
          </div>
          <div className="pad-group-label">Parts</div>
          <div className="pad-row" style={{ "--cols": 4 } as CSSProperties}>
            {STEM_NAMES.map((st) => (
              <StemTile key={st} id={`${deck}.stem.${st}`} label={STEM_LABEL[st]!} disabled={!s.hasStems} />
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

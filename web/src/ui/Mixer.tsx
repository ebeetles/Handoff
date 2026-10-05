import type { DeckSnapshot } from "../audio/engine";
import type { DeckId } from "../control/controls";
import { Crossfader, Fader, Knob } from "./Controls";

// Two columns per channel: tone knobs on the left, meter + volume fader on the right.
// Deck B mirrors deck A so each hand works its own side of the mixer.
function Channel({ deck, s }: { deck: DeckId; s: DeckSnapshot }) {
  return (
    <div className={`channel deck-${deck.toLowerCase()}`}>
      <div className="channel-head">{deck}</div>
      <div className="channel-body">
        <div className="channel-knobs">
          <Knob id={`${deck}.eqHigh`} label="High" bipolar size={60} />
          <Knob id={`${deck}.eqMid`} label="Mid" bipolar size={60} />
          <Knob id={`${deck}.eqLow`} label="Low" bipolar size={60} />
          <Knob id={`${deck}.filter`} label="Filter" bipolar size={70} />
        </div>
        <div className="volume-row">
          <div className="meter" aria-hidden><div className="meter-fill" style={{ height: `${s.level * 100}%` }} /></div>
          <Fader id={`${deck}.volume`} label="Volume" />
        </div>
      </div>
    </div>
  );
}

export function Mixer({ a, b }: { a: DeckSnapshot; b: DeckSnapshot }) {
  return (
    <section className="panel mixer" aria-label="Mixer">
      <div className="mixer-channels">
        <Channel deck="A" s={a} />
        <Channel deck="B" s={b} />
      </div>
      <Crossfader />
    </section>
  );
}

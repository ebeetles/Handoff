// AudioEngine: owns the AudioContext, both decks, and the master bus. It is the only
// thing that listens to ControlStore/CommandBus and touches Web Audio. UI reads state
// through snapshot(); it never pokes decks directly.
import { localLibrary, type LibrarySource } from "../hosting";
import { assertTrackAnalysis, STEM_NAMES, type StemName, type TrackAnalysis, type Waveform } from "../contracts/track";
import type { Command } from "../control/commands";
import { CommandBus } from "../control/commands";
import { ControlStore, DECKS, deckControl, type ControlId, type ControlSource, type DeckId } from "../control/controls";
import { Deck, ECHO_BEATS, type LoadedTrack, type SourceKey } from "./deck";
import { BeatGrid, loopRange } from "./grid";
import { keySemitones, keyValue, masterGain, rateToTempo, tempoRate, xfadeGains } from "./mapping";
import { alignedLaunchBar, barPhaseDelta, chooseSync, frac, transposeCamelot } from "./sync";
import { ctxTimeAt, positionAt, reanchor } from "./transport";

export interface DeckSnapshot {
  loaded: boolean;
  trackId: string;
  loading: string | null;
  error: string | null;
  title: string;
  artist: string;
  camelot: string;
  keyName: string;
  duration: number;
  position: number;
  playing: boolean;
  pending: boolean;
  rate: number;
  trackBpm: number;
  bpm: number;           // effective = local track BPM x rate
  barPos: number;        // fractional bar index (0 = first downbeat)
  beatInBar: number;     // 0..3 (negative bars before the first downbeat clamp to 0)
  phraseIndex: number;
  phraseCount: number;
  phraseBars: number;
  barInPhrase: number;   // 0-based integer
  barsToNextPhrase: number;
  sectionLabel: string;
  loop: { beats: number } | null;
  cue: number;
  level: number;
  synced: boolean;
  hasStems: boolean;
  beatmatchable: boolean;
  /** Semitones the deck is heard shifted from its own key: the key shift with key lock on, or
   *  the tempo's pitch change with it off. heardCamelot is the key it is heard in. */
  pitchSemitones: number;
  heardCamelot: string;
  keyShift: number;   // the key control, in semitones
}

const EMPTY: DeckSnapshot = {
  loaded: false, trackId: "", loading: null, error: null, title: "", artist: "", camelot: "", keyName: "", duration: 0,
  position: 0, playing: false, pending: false, rate: 1, trackBpm: 0, bpm: 0, barPos: 0, beatInBar: 0,
  phraseIndex: 0, phraseCount: 0, phraseBars: 16, barInPhrase: 0, barsToNextPhrase: 0, sectionLabel: "", loop: null, cue: 0,
  level: 0, synced: false, hasStems: false, beatmatchable: true, pitchSemitones: 0, heardCamelot: "", keyShift: 0,
};

export class AudioEngine {
  readonly ctx: AudioContext;
  readonly decks: Record<DeckId, Deck>;
  private readonly master: GainNode;
  private notices: { text: string; until: number } | null = null;
  /** Exact-time steps applied for automation (newest last, capped): for tests and debugging. */
  readonly automationLog: { control: ControlId; value: number; at: number }[] = [];

  constructor(readonly store: ControlStore, readonly bus: CommandBus, readonly library: LibrarySource = localLibrary("library")) {
    // 44.1 kHz context matches the served files: no resampling on decode, and less memory
    // than a 48 kHz context would use for the same stems.
    let ctx: AudioContext;
    try { ctx = new AudioContext({ sampleRate: 44100, latencyHint: "interactive" }); } catch { ctx = new AudioContext(); }
    this.ctx = ctx;
    this.master = ctx.createGain();
    const limiter = new DynamicsCompressorNode(ctx, { threshold: -3, knee: 0, ratio: 20, attack: 0.002, release: 0.2 });
    this.master.connect(limiter).connect(ctx.destination);
    this.decks = { A: new Deck(ctx, "A"), B: new Deck(ctx, "B") };
    for (const d of DECKS) {
      this.decks[d].out.connect(this.master);
      this.decks[d].fxOut.connect(this.master);
    }

    // Apply every control's initial value, then follow changes.
    this.applyAll();
    store.subscribeAll((id, v, src) => this.applyControl(id, v, src));
    bus.subscribe((cmd) => void this.handle(cmd));
  }

  /** Browsers start AudioContexts suspended until a user gesture. Call on first pointerdown. */
  resume(): void {
    if (this.ctx.state !== "running") void this.ctx.resume();
  }

  other(d: DeckId): Deck {
    return this.decks[d === "A" ? "B" : "A"];
  }

  notice(): string | null {
    return this.notices && this.notices.until > performance.now() ? this.notices.text : null;
  }

  private say(text: string): void {
    this.notices = { text, until: performance.now() + 3500 };
  }

  // ------------------------------------------------------------ controls -> audio

  private applyAll(): void {
    const ids: ControlId[] = ["xfader", "master", "keyLock"];
    for (const d of DECKS) for (const c of ["eqHigh", "eqMid", "eqLow", "filter", "volume", "tempo", "echo", "key"] as const) ids.push(deckControl(d, c));
    ids.forEach((id) => this.applyControl(id, this.store.get(id), "engine"));
  }

  /** Apply a control value to the audio graph, now or (automation steps) at AudioContext time `at`. */
  private applyControl(id: ControlId, v: number, source: ControlSource, at?: number): void {
    if (id === "master") return void this.master.gain.setTargetAtTime(masterGain(v), at ?? this.ctx.currentTime, 0.012);
    if (id === "xfader") {
      const g = xfadeGains(v);
      this.decks.A.setCrossfadeGain(g.a, at);
      this.decks.B.setCrossfadeGain(g.b, at);
      return;
    }
    if (id === "quantize") return;
    // Key lock is all-or-nothing: it adds the key stage's latency, which both decks must share.
    if (id === "keyLock") return DECKS.forEach((d) => this.decks[d].setKeyLock(v > 0.5, at));
    const [d, ...rest] = id.split(".") as [DeckId, ...string[]];
    const deck = this.decks[d];
    const c = rest.join(".");
    switch (c) {
      case "eqLow": return deck.setEq("low", v, at);
      case "eqMid": return deck.setEq("mid", v, at);
      case "eqHigh": return deck.setEq("high", v, at);
      case "filter": return deck.setFilter(v, at);
      case "volume": return deck.setVolume(v, at);
      case "echo": return deck.setEcho(v, at);
      case "key": return deck.setKeyShift(keySemitones(v), at);
      case "tempo":
        deck.setRate(tempoRate(v));
        this.updateEchoTime(deck);
        if (source !== "engine") deck.synced = false;
        return;
      default:
        if (c.startsWith("stem.")) {
          const when = at ?? (this.quantize ? deck.nextBeatCtxTime() : this.ctx.currentTime);
          deck.setStem(c.slice(5) as StemName, v > 0.5, when);
        }
    }
  }

  get quantize(): boolean {
    return this.store.get("quantize") > 0.5;
  }

  // ------------------------------------------------------------ commands

  /** "ready" once both decks' key stages run; "unavailable" if either failed (key lock then
   *  only keeps the decks' timing, not their key). */
  async keyLockStatus(): Promise<"ready" | "unavailable"> {
    await Promise.all(DECKS.map((d) => this.decks[d].key.ready));
    return DECKS.some((d) => this.decks[d].key.error) ? "unavailable" : "ready";
  }

  private async handle(cmd: Command): Promise<void> {
    this.resume();
    if (cmd.type === "setControlAt") return this.setControlAt(cmd.control, cmd.value, cmd.at);
    const deck = this.decks[cmd.deck];
    switch (cmd.type) {
      case "playAt": return this.playAt(deck, cmd.at, cmd.fromBar);
      case "brakeAt": return this.brakeAt(deck, cmd.at, cmd.beats);
      case "keyShift": {
        const id = deckControl(cmd.deck, "key");
        return this.store.set(id, keyValue(keySemitones(this.store.get(id)) + cmd.semitones), "engine");
      }
      case "pause": return deck.pause();
      case "loopOff": return deck.anchor.loop ? this.exitLoop(deck) : undefined;
      case "load": return this.load(cmd.deck, cmd.trackId);
      case "togglePlay": return deck.playing ? deck.pause() : this.launch(cmd.deck);
      case "cue": return this.cue(deck);
      case "sync": return this.sync(cmd.deck);
      case "loop": return this.loop(deck, cmd.beats, cmd.slip ?? false);
      case "jump": return this.jump(deck, cmd.beats);
      case "seekFraction": return this.seekFraction(deck, cmd.fraction);
    }
  }

  /** Play, quantized: if "snap to beat" is on and the other deck is playing, start on the
   *  other deck's next bar line (at matching bar phase) instead of right now. */
  private launch(d: DeckId): void {
    const me = this.decks[d];
    const other = this.other(d);
    if (!me.track) return;
    const now = this.ctx.currentTime;
    if (!this.quantize || !other.track || !other.playing || other.pending || other.anchor.loop) {
      me.play(now);
      return;
    }
    const og = other.track.grid;
    const oPos = other.position(now);
    const otherEff = og.bpmAt(oPos) * other.anchor.rate;
    const myPos = me.position(now);
    const m = chooseSync(me.track.grid.bpmAt(myPos), otherEff, 0.5)?.multiplier ?? 1;
    const myPhase = frac(me.track.grid.barAt(myPos));
    const secPerOtherBar = (4 * og.periodAt(oPos)) / other.anchor.rate;
    const targetBar = alignedLaunchBar(og.barAt(oPos), m, myPhase, 0.03 / secPerOtherBar);
    const when = ctxTimeAt(other.anchor, og.timeAtBar(targetBar));
    me.play(Math.max(now, when));
  }

  private cue(deck: Deck): void {
    if (!deck.track) return;
    if (deck.playing) {
      deck.pause();
      deck.seek(deck.cuePos);
      return;
    }
    const pos = deck.position();
    deck.cuePos = this.quantize ? deck.track.grid.nearestBeatTime(pos) : pos;
    deck.seek(deck.cuePos);
  }

  private sync(d: DeckId): void {
    const me = this.decks[d];
    const other = this.other(d);
    if (!me.track || !other.track) return this.say("Load a track on both decks to sync.");
    const now = this.ctx.currentTime;
    const oPos = other.position(now);
    const otherEff = other.track.grid.bpmAt(oPos) * other.anchor.rate;
    const myPos = me.position(now);
    const myBpm = me.track.grid.bpmAt(myPos);
    const choice = chooseSync(myBpm, otherEff);
    if (!choice) {
      return this.say(`Tempos too far apart to sync (${myBpm.toFixed(1)} vs ${otherEff.toFixed(1)} BPM).`);
    }
    if (!me.track.analysis.tempo.beatmatchable || !other.track.analysis.tempo.beatmatchable) {
      this.say("One of these tracks has a drifting tempo; sync may slip.");
    }
    me.setRate(choice.rate);
    this.updateEchoTime(me);
    this.store.set(deckControl(d, "tempo"), rateToTempo(choice.rate), "engine");
    me.synced = true;
    if (me.playing && !me.pending && other.playing && !other.pending) {
      const g = me.track.grid;
      const at = this.ctx.currentTime;   // one clock read for both decks and the seek
      const myBar = g.barAt(me.position(at));
      const delta = barPhaseDelta(myBar, other.track.grid.barAt(other.position(at)), choice.multiplier);
      if (Math.abs(delta) > 1e-3) {
        me.slip = null;
        me.setLoop(null);
        me.seek(g.timeAtBar(myBar + delta), at);
      }
    }
  }

  /** Automation: start a paused deck exactly at AudioContext time `at`, from bar `fromBar` (a
   *  composed transition's entry point) or, with snap on, from the nearest bar line. Either way
   *  its bars line up with the bar `at` was computed from. */
  private playAt(deck: Deck, at: number, fromBar?: number): void {
    if (!deck.track || deck.playing) return;
    const g = deck.track.grid;
    if (fromBar !== undefined) deck.seek(g.timeAtBar(fromBar));
    else if (this.quantize) deck.seek(g.timeAtBar(Math.max(0, Math.round(g.barAt(deck.position())))));
    deck.play(Math.max(this.ctx.currentTime, at));
  }

  /** Automation: a turntable stop. From `at`, the deck's playback rate falls to zero over `beats`
   *  beats, pitch diving with it; the recipe silences and pauses it afterwards. The deck's clock
   *  isn't re-anchored: it keeps counting as if the deck played on, so the recipe's bar clock (and
   *  the incoming deck's entry) is unaffected. */
  private brakeAt(deck: Deck, at: number, beats: number): void {
    if (!deck.track || !deck.playing) return;
    const when = Math.max(this.ctx.currentTime, at);
    deck.brake(when, (beats * deck.track.grid.periodAt(deck.position(when))) / deck.anchor.rate);
  }

  /** Automation: apply a step exactly at `at` on the audio clock. The store (and the UI)
   *  follows when the step sounds; that timer only does bookkeeping, it never triggers audio.
   *  A control a human is holding is left alone. */
  private setControlAt(id: ControlId, value: number, at: number): void {
    if (this.store.isHeld(id)) return;
    const now = this.ctx.currentTime;
    const when = Math.max(now, at);
    this.applyControl(id, value, "auto", when);
    this.automationLog.push({ control: id, value, at: when });
    if (this.automationLog.length > 256) this.automationLog.shift();
    setTimeout(() => this.store.set(id, value, "auto"), Math.max(0, (when - now) * 1000));
  }

  /** Keep the echo at ECHO_BEATS of what the deck is playing now. */
  private updateEchoTime(deck: Deck): void {
    if (!deck.track) return;
    deck.setEchoTime((ECHO_BEATS * deck.track.grid.periodAt(deck.position())) / deck.anchor.rate);
  }

  // ------------------------------------------------------------ clock reads (automation)

  /** The deck's musical clock: during a roll, where playback would be without it. */
  private clock(deck: Deck) {
    return deck.slip ?? deck.anchor;
  }

  /** Fractional bar the deck is at (or will be) at AudioContext time t. */
  barAt(d: DeckId, t: number): number {
    const deck = this.decks[d];
    return deck.track ? deck.track.grid.barAt(positionAt(this.clock(deck), t)) : 0;
  }

  /** AudioContext time at which a playing deck reaches `bar` (from its grid). */
  barToCtx(d: DeckId, bar: number): number {
    const deck = this.decks[d];
    if (!deck.track) return Infinity;
    return ctxTimeAt(this.clock(deck), deck.track.grid.timeAtBar(bar));
  }

  /** First phrase start at least `minBarsAhead` bars ahead of the deck, or null. */
  nextPhraseBar(d: DeckId, minBarsAhead: number): number | null {
    const t = this.decks[d].track;
    if (!t) return null;
    const bar = this.barAt(d, this.ctx.currentTime);
    return t.analysis.phrases.find((p) => p.start_bar >= bar + minBarsAhead)?.start_bar ?? null;
  }

  private loop(deck: Deck, beats: number, slip = false): void {
    if (!deck.track) return;
    if (deck.anchor.loop?.beats === beats) return this.exitLoop(deck);
    const { start, end } = loopRange(deck.track.grid, deck.position(), beats, this.quantize || slip);
    if (end > deck.track.analysis.duration_s) return this.say("Not enough track left for that loop.");
    // A roll (under a beat) slips: keep a clock of where playback would be without it, so
    // leaving the roll lands back in time. A fractional loop otherwise exits off the beat
    // (it advances a fraction of a beat per pass), which breaks sync with the other deck.
    // Automation asks for the same on longer loops (a held hook): its bar clock must keep running.
    if (beats < 1 || slip) deck.slip ??= deck.playing ? reanchor(deck.anchor, this.ctx.currentTime, { loop: null }) : null;
    else deck.slip = null;
    deck.setLoop({ start, end, beats });
  }

  private exitLoop(deck: Deck): void {
    const slip = deck.slip;
    deck.slip = null;
    deck.setLoop(null);
    const at = this.ctx.currentTime;
    if (slip && deck.playing) deck.seek(positionAt(slip, at), at);
  }

  private jump(deck: Deck, beats: number): void {
    if (!deck.track) return;
    const g = deck.track.grid;
    const at = this.ctx.currentTime;
    const target = g.timeAtBeat(g.beatAt(deck.position(at)) + beats);   // whole beats keep the phase
    deck.slip = null;
    deck.setLoop(null);
    deck.seek(target, at);
  }

  private seekFraction(deck: Deck, f: number): void {
    if (!deck.track) return;
    const g = deck.track.grid;
    const at = this.ctx.currentTime;
    let t = Math.min(1, Math.max(0, f)) * deck.track.analysis.duration_s;
    if (this.quantize) {
      if (deck.playing) {
        // Keep the current bar phase so a synced deck stays in time after the jump.
        const phase = frac(g.barAt(deck.position(at)));
        t = g.timeAtBar(Math.round(g.barAt(t) - phase) + phase);
      } else {
        t = g.nearestBeatTime(t);
      }
    }
    deck.slip = null;
    deck.setLoop(null);
    deck.seek(t, at);
  }

  // ------------------------------------------------------------ loading

  async load(d: DeckId, trackId: string): Promise<void> {
    const deck = this.decks[d];
    if (deck.playing) return this.say(`Deck ${d} is playing. Stop it before loading a new track.`);
    deck.unload();
    deck.loading = "Reading analysis";
    deck.error = null;
    try {
      const at = (path: string) => this.library.url(`${trackId}/${path}`);
      const analysis = await fetchJson<TrackAnalysis>(await at("analysis.json"));
      assertTrackAnalysis(analysis);
      const stems = analysis.audio.stems;
      const keys: SourceKey[] = stems ? [...STEM_NAMES] : ["mix"];
      // Ask for every file at once (hosted: one batch of signed links, downloads in parallel),
      // then decode one at a time.
      const [waveform, ...audio] = await Promise.all([
        at(analysis.waveform).then((u) => fetchJson<Waveform>(u)),
        ...keys.map((key) => at(key === "mix" ? analysis.audio.mix : stems![key]).then((u) => okFetch(u)).then((r) => r.arrayBuffer())),
      ]);
      const buffers = new Map<SourceKey, AudioBuffer>();
      for (const [i, key] of keys.entries()) {
        deck.loading = keys.length > 1 ? `Decoding ${key} (${i + 1}/${keys.length})` : "Decoding audio";
        buffers.set(key, await this.ctx.decodeAudioData(audio[i]!));
      }
      const lens = [...buffers.values()].map((b) => b.duration);
      if (Math.max(...lens) - Math.min(...lens) > 0.01) console.warn(`Deck ${d}: stem lengths differ`, lens);
      const track: LoadedTrack = {
        analysis, waveform, buffers, usingStems: !!stems,
        grid: new BeatGrid(analysis.beats, analysis.first_downbeat_index, analysis.beats_per_bar),
      };
      deck.setTrack(track);
      this.updateEchoTime(deck);
      this.store.set(deckControl(d, "tempo"), 0.5, "engine");
      for (const s of STEM_NAMES) this.store.set(deckControl(d, `stem.${s}`), 1, "engine");
      for (const s of STEM_NAMES) deck.setStem(s, true, this.ctx.currentTime);
    } catch (e) {
      deck.error = e instanceof Error ? e.message : String(e);
    } finally {
      deck.loading = null;
    }
  }

  // ------------------------------------------------------------ read side

  snapshot(d: DeckId): DeckSnapshot {
    const deck = this.decks[d];
    deck.tick();
    const t = deck.track;
    if (!t) return { ...EMPTY, loading: deck.loading, error: deck.error };
    const a = t.analysis;
    const pos = deck.position();
    const barPos = t.grid.barAt(pos);
    const bar = Math.max(0, Math.floor(barPos));
    const phrase = a.phrases.find((p) => bar >= p.start_bar && bar < p.start_bar + p.n_bars) ?? a.phrases[a.phrases.length - 1]!;
    const section = a.sections[phrase.section_index];
    const rate = deck.anchor.rate;
    return {
      loaded: true, trackId: a.id, loading: deck.loading, error: deck.error,
      title: a.title, artist: a.artist, camelot: a.key.camelot, keyName: a.key.name,
      duration: a.duration_s, position: pos, playing: deck.playing, pending: deck.pending, rate,
      trackBpm: a.tempo.bpm, bpm: t.grid.bpmAt(pos) * rate, barPos,
      beatInBar: barPos < 0 ? 0 : Math.floor(frac(barPos) * 4),
      phraseIndex: phrase.index, phraseCount: a.phrases.length, phraseBars: phrase.n_bars,
      barInPhrase: Math.min(phrase.n_bars - 1, bar - phrase.start_bar),
      barsToNextPhrase: phrase.start_bar + phrase.n_bars - Math.max(0, barPos),
      sectionLabel: section?.label ?? "",
      loop: deck.anchor.loop ? { beats: deck.anchor.loop.beats } : null,
      cue: deck.cuePos, level: deck.level(), synced: deck.synced, hasStems: deck.hasStems,
      beatmatchable: a.tempo.beatmatchable, keyShift: deck.keyShift,
      ...((): { pitchSemitones: number; heardCamelot: string } => {
        const locked = this.store.get("keyLock") > 0.5;
        const semis = locked ? deck.keyShift : 12 * Math.log2(rate);
        return { pitchSemitones: semis, heardCamelot: transposeCamelot(a.key.camelot, Math.round(semis)) };
      })(),
    };
  }
}

async function okFetch(url: string): Promise<Response> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Couldn't load ${url} (${r.status}). Is the library in web/public/library?`);
  return r;
}

async function fetchJson<T>(url: string): Promise<T> {
  return (await okFetch(url)).json() as Promise<T>;
}

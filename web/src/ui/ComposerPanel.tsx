import { useEffect, useRef, useState } from "react";
import type { DeckSnapshot } from "../audio/engine";
import { assertTrackAnalysis, type TrackAnalysis } from "../contracts/track";
import { composeLive } from "../coDJ/composer";
import { useServices } from "./context";

export function ComposerPanel({ out, inn, outDeck, inDeck, onClose, onPick }: {
  out: DeckSnapshot; inn: DeckSnapshot; outDeck: "A" | "B"; inDeck: "A" | "B"; onClose: () => void; onPick: (id: string) => void;
}) {
  const { transitions } = useServices();
  const dialog = useRef<HTMLDialogElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [track, setTrack] = useState<TrackAnalysis | null>(null);
  const [exit, setExit] = useState<number | null>(null);
  const [bars, setBars] = useState(16);
  const [brief, setBrief] = useState("Make it adventurous: a stem tease or rhythmic call-and-response, then a strong drop. Use what suits these tracks.");
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
    return () => abort.current?.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const r = await fetch(`${import.meta.env.BASE_URL}library/${encodeURIComponent(out.trackId)}/analysis.json`, { signal: controller.signal });
        if (!r.ok) throw new Error("Couldn't load this track's phrase map");
        const data: unknown = await r.json();
        assertTrackAnalysis(data);
        setTrack(data);
        const eligible = data.phrases.filter((p) => p.start_bar >= out.barPos + 1 && data.bars.length - p.start_bar >= 8);
        // Give a playing track time for inference. Users can choose a nearer phrase explicitly.
        setExit((eligible.find((p) => p.start_bar >= out.barPos + (out.playing ? 32 : 1)) ?? eligible[0])?.start_bar ?? null);
      } catch (e) {
        if (!controller.signal.aborted) setMessage(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => controller.abort();
  }, [out.trackId]);

  const passed = exit !== null && out.barPos > exit - 1;
  const submit = async () => {
    if (exit === null || passed) return;
    abort.current = new AbortController();
    setWorking(true);
    setMessage("Drafting three ideas, keeping the strongest, then checking and refining it. This usually takes 1–3 minutes; playback continues.");
    try {
      const doc = await composeLive({ schema_version: 1, out_track: out.trackId, in_track: inn.trackId,
        out_start_bar: exit, min_start_bar: Math.max(0, Math.ceil(out.barPos + 1)), max_bars: bars,
        candidates: 3, brief }, abort.current.signal);
      transitions.merge(doc);
      const pair = doc.pairs[0]!;
      if (pair.best === null) {
        const reasons = pair.candidates.flatMap((c) => [...c.errors, ...(c.critic?.valid === false ? c.critic.reasons : [])]);
        setMessage(`The transition didn't pass the checks after refining. ${reasons.slice(0, 2).join("; ")} Try another phrase or brief.`);
        return;
      }
      onPick(pair.candidates[pair.best]!.recipe!.id);
      onClose();
    } catch (e) {
      if (!abort.current.signal.aborted) setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  };
  return <dialog className="composer-panel" ref={dialog} onCancel={onClose}>
    <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div className="composer-heading"><h2>Compose a transition</h2>
        <button type="button" className="text-button" onClick={onClose} aria-label="Close composer">Close</button></div>
      <p>From deck {outDeck}: <strong>{out.title}</strong> → into deck {inDeck}: <strong>{inn.title}</strong>
        <br /><small>Wrong way round? Close this and use the {outDeck} → {inDeck} button to swap.</small></p>
      <label>Start on the outgoing track
        <select aria-label="Composer exit phrase" value={exit ?? ""} disabled={working || !track} onChange={(e) => setExit(Number(e.target.value))}>
          {exit === null && <option value="">No future phrase available</option>}
          {track?.phrases.filter((p) => track.bars.length - p.start_bar >= 8).map((p) =>
            <option key={p.start_bar} value={p.start_bar} disabled={out.barPos > p.start_bar - 1}>
              Bar {p.start_bar + 1} · {Math.floor(p.start_s / 60)}:{String(Math.floor(p.start_s % 60)).padStart(2, "0")} · {track.sections[p.section_index]?.label}
            </option>)}
        </select>
      </label>
      <label>Maximum transition length
        <select aria-label="Composer duration" value={bars} disabled={working} onChange={(e) => setBars(Number(e.target.value))}>
          {[8, 16, 32].map((n) => <option key={n} value={n}>{n} bars</option>)}
        </select>
      </label>
      <label>Creative direction
        <textarea aria-label="Creative direction" value={brief} maxLength={500} rows={3} disabled={working} onChange={(e) => setBrief(e.target.value)} />
      </label>
      <p className="composer-hint">Choose any phrase, including the middle of the track. The composer chooses an entry in the other track, then checks and refines its plan. Review it, then press Try transition.</p>
      <p className="composer-hint">If playback passes your chosen phrase while composing, choose a later phrase and compose again.</p>
      <div role="status" className="composer-message">{passed && !working ? "That phrase has passed. Choose a later one." : message}</div>
      <button className="text-button" type="submit" disabled={working || exit === null || passed || !track}>
        {working ? "Composing…" : "Compose transition"}
      </button>
    </form>
  </dialog>;
}

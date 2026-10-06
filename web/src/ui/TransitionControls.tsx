// Temporary "Try transition" control: pick a transition, arm it, watch it run. Lists the
// transitions composed for the loaded pair first (Chunk 7: by the AI or the rules composer,
// scored by the critic, best first), then the presets (Chunk 6). Chunk 9's co-DJ replaces the
// picking; the player stays.
import { useEffect, useState, useSyncExternalStore } from "react";
import { RECIPES } from "../coDJ/recipes";
import type { Recipe } from "../contracts/recipe";
import { useServices } from "./context";
import { ComposerPanel } from "./ComposerPanel";

export function TransitionControls() {
  const { coDJ, engine, transitions, store } = useServices();
  const st = useSyncExternalStore((cb) => coDJ.subscribe(cb), () => coDJ.status());
  const [picked, setPicked] = useState<string | null>(null);
  const [composerOpen, setComposerOpen] = useState(false);
  const [, tick] = useState(0);
  useEffect(() => {   // re-check what's possible as decks start/stop (4 Hz, UI only)
    const t = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(t);
  }, []);
  useEffect(() => transitions.subscribe(() => tick((n) => n + 1)), [transitions]);
  // The pair as it stands: the playing deck goes out (both playing: the crossfader's side).
  const a = engine.snapshot("A"), b = engine.snapshot("B");
  const outIsA = a.playing !== b.playing ? a.playing : store.get("xfader") <= 0.5;
  const [o, i] = outIsA ? [a, b] : [b, a];
  const composed = o.loaded && i.loaded ? transitions.composedFor(o.trackId, i.trackId) : [];
  const options: Recipe[] = [...composed.map((c) => c.recipe!), ...RECIPES];
  const recipe = options.find((r) => r.id === picked) ?? options[0]!;
  const cand = composed.find((c) => c.recipe!.id === recipe.id);
  const busy = st.state === "armed" || st.state === "running";
  const { reasons, out, inn } = coDJ.check(recipe);

  let line = cand ? `Score ${cand.critic!.score.toFixed(0)}: ${recipe.description}` : recipe.description;
  if (busy && st.recipe) {
    line = st.state === "armed"
      ? `${st.recipe.name}, ${st.out} to ${st.in}: starts in ${Math.max(1, Math.ceil(-st.bar))} bar${Math.ceil(-st.bar) > 1 ? "s" : ""}`
      : `${st.recipe.name}, ${st.out} to ${st.in}: bar ${Math.floor(st.bar) + 1} of ${st.recipe.bars}`;
    if (st.takenOver.length) line += `. You have: ${st.takenOver.join(", ")}`;
  } else if (st.state === "done" || st.state === "stopped" || st.state === "error") {
    line = st.message;
  } else if (reasons.length) {
    line = `Can't run yet: ${reasons.join("; ")}.`;
  }

  return (
    <div className="transition">
      <div className="transition-row">
        <select value={recipe.id} onChange={(e) => { setPicked(e.target.value); e.target.blur(); /* keep keyboard shortcuts working */ }}
                disabled={busy} aria-label="Transition recipe">
          {composed.length > 0 && (
            <optgroup label="Composed for this pair">
              {composed.map((c) => (
                <option key={c.id} value={c.recipe!.id}>{`${c.critic!.score.toFixed(0)} · ${c.idea} (${c.source === "llm" ? "AI" : "rules"})`}</option>
              ))}
            </optgroup>
          )}
          <optgroup label="Presets">
            {RECIPES.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </optgroup>
        </select>
        {busy
          ? <button className="text-button" onClick={() => coDJ.stop()}>Stop</button>
          : <button className="text-button" disabled={reasons.length > 0} title={reasons.length ? reasons.join("; ") : `${out} to ${inn}`}
                    onClick={() => { engine.resume(); coDJ.start(recipe); }}>Try transition</button>}
        <button className="text-button" disabled={busy || !o.loaded || !i.loaded || i.playing || o.trackId === i.trackId}
                onClick={() => setComposerOpen(true)}>Compose with AI</button>
      </div>
      <div className={`transition-status ${st.state}`} role="status">{line}</div>
      {cand?.recipe?.anchor && <details className="transition-details">
        <summary>{cand.source === "llm" ? "AI" : "Rules"} · A/out bar {cand.recipe.anchor.out_start_bar + 1} → B/in bar {cand.recipe.anchor.in_from_bar + 1} · {cand.recipe.bars} bars</summary>
        <p>{cand.rationale}</p>
        <p>{cand.plan.moves.map((m) => String(m.move).replaceAll("_", " ")).join(" → ")}</p>
        <p>{cand.critic?.reasons.join(" ")}</p>
      </details>}
      {composerOpen && <ComposerPanel key={`${o.trackId}:${i.trackId}`} out={o} inn={i}
        onClose={() => setComposerOpen(false)} onPick={setPicked} />}
    </div>
  );
}

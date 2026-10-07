import { Fragment, useEffect, useState } from "react";
import type { LibraryIndex, LibraryIndexEntry } from "../contracts/track";
import type { DeckId } from "../control/controls";
import { SHORTCUTS } from "../input/keyboard";
import { useServices } from "./context";

export function Library({ onClose, base }: { onClose: () => void; base: string }) {
  const { bus } = useServices();
  const [tracks, setTracks] = useState<LibraryIndexEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${base}/index.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`index.json not found (${r.status})`))))
      .then((idx: LibraryIndex) => setTracks(idx.tracks))
      .catch((e: Error) => setErr(e.message));
  }, [base]);

  const load = (deck: DeckId, id: string) => { bus.dispatch({ type: "load", deck, trackId: id }); onClose(); };

  return (
    <>
      <div className="library-scrim" onClick={onClose} />
      <div className="library" role="dialog" aria-label="Track library">
        <div className="library-head">
          <h2>Library</h2>
          <button className="text-button" onClick={onClose}>Close</button>
        </div>
        {err || (tracks && tracks.length === 0) ? (
          <p>
            No tracks yet. From <code>pipeline/</code>, run <code>python make_demo_tracks.py --out tracks</code> for two demo
            tracks, or put your own files in <code>tracks/</code>, then <code>python preprocess.py --in tracks --out ../web/public/library</code> and reopen this panel.
          </p>
        ) : (
          <p>Pick a deck for each track. Tracks with parts can mute drums, bass, vocals, and melody separately.</p>
        )}
        {tracks?.map((t) => (
          <div className="lib-row" key={t.id}>
            <div>
              <div className="lib-title">{t.title}</div>
              <div className="lib-sub">{[t.artist, t.has_stems && "parts available", !t.beatmatchable && "tempo drifts"].filter(Boolean).join(", ")}</div>
            </div>
            <div className="lib-stat"><span className="lib-num">{t.bpm.toFixed(1)}</span><span className="lib-unit">BPM</span></div>
            <div className="lib-stat"><span className="lib-num">{t.camelot}</span><span className="lib-unit">Key</span></div>
            <div className="lib-load">
              <button className="a" onClick={() => load("A", t.id)}>Load to A</button>
              <button className="b" onClick={() => load("B", t.id)}>Load to B</button>
            </div>
          </div>
        ))}
        <div className="shortcuts">
          {SHORTCUTS.map((s) => <Fragment key={s.keys}><b>{s.keys}</b><span>{s.does}</span></Fragment>)}
        </div>
      </div>
    </>
  );
}

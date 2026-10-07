// Hosted only: ask for the access code before the board loads anything. The free backend sleeps
// when idle, so checking the code can take up to a minute the first time; say so while it wakes.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { checkCode, HOSTED, saveCode, savedCode } from "../hosting";

export function AccessGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "ask" | "ok">(HOSTED ? (savedCode() ? "checking" : "ask") : "ok");
  const [code, setCode] = useState(savedCode());
  const [message, setMessage] = useState("");
  const abort = useRef<AbortController | null>(null);

  const tryCode = async (c: string) => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setState("checking");
    setMessage("Checking…");
    try {
      const verdict = await checkCode(c.trim(), (s) => setMessage(`Starting the server (it sleeps when idle; up to a minute)… ${s} s`), controller.signal);
      if (verdict === "ok") {
        saveCode(c.trim());
        setState("ok");
      } else {
        setState("ask");
        setMessage("That code didn't work. Check it and try again.");
      }
    } catch (e) {
      if (controller.signal.aborted) return;
      setState("ask");
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (state === "checking" && savedCode()) void tryCode(savedCode());
    return () => abort.current?.abort();
  }, []);   // first render only: try the code this browser remembers

  if (state === "ok") return <>{children}</>;
  return (
    <div className="gate">
      <form className="gate-card" onSubmit={(e) => { e.preventDefault(); if (code.trim()) void tryCode(code); }}>
        <div className="wordmark"><span className="logo" aria-hidden /> Handoff<span className="wordmark-sub">DJ board</span></div>
        <p>A DJ board you play with your hands, with an AI co-DJ. Enter the access code you were given.</p>
        <label>Access code
          <input aria-label="Access code" value={code} autoFocus autoComplete="off" spellCheck={false}
                 disabled={state === "checking"} onChange={(e) => setCode(e.target.value)} />
        </label>
        <button className="text-button primary" type="submit" disabled={state === "checking" || !code.trim()}>
          {state === "checking" ? "Opening…" : "Open the board"}
        </button>
        <div className="gate-message" role="status">{message}</div>
        <p className="gate-hint">Works best in Chrome on a laptop or desktop. Hand control needs a webcam; the mouse works too.</p>
      </form>
    </div>
  );
}

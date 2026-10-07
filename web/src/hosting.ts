// Where the board gets its library and its composer: locally (Vite dev server, files under
// public/library, composer proxied to 127.0.0.1:8000), or hosted (VITE_API_URL set at build
// time). Hosted, the library lives in a private R2 bucket: the backend checks the access code
// and hands out short-lived signed links, so the audio is never publicly reachable.

/** The backend's address, e.g. https://handoff-api.onrender.com ("" = local development). */
export const API_URL = String(import.meta.env.VITE_API_URL ?? "").trim().replace(/\/+$/, "");
export const HOSTED = API_URL !== "";

const CODE_KEY = "handoff.accessCode";

export function savedCode(): string {
  try { return localStorage.getItem(CODE_KEY) ?? ""; } catch { return ""; }
}

export function saveCode(code: string): void {
  try { localStorage.setItem(CODE_KEY, code); } catch { /* private mode: ask again next time */ }
}

export function forgetCode(): void {
  try { localStorage.removeItem(CODE_KEY); } catch { /* nothing stored */ }
}

/** fetch() against the backend, with the access code. Local: same-origin paths (Vite proxy). */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (HOSTED) headers.set("X-Access-Code", savedCode());
  return fetch(`${API_URL}${path}`, { ...init, headers });
}

/** Check a code with the backend. The free server sleeps when idle and takes up to a minute to
 *  wake, so keep trying while it's unreachable; onWait reports each wait.
 *
 *  A refusal of this page's address (CORS: the backend's ALLOWED_ORIGINS) also reaches fetch as a
 *  plain network failure, and used to be reported as "the server didn't start". So after a
 *  failure, ask the public health check (readable from any page): if the server answers it, it's
 *  up and the problem is the address, which is said at once. */
export async function checkCode(code: string, onWait: (seconds: number) => void, signal?: AbortSignal): Promise<"ok" | "wrong"> {
  const started = Date.now();
  for (;;) {
    try {
      const r = await fetch(`${API_URL}/api/session`, { headers: { "X-Access-Code": code }, signal });
      if (r.ok) return "ok";
      if (r.status === 401) return "wrong";
      if (r.status !== 502 && r.status !== 503 && r.status !== 504) throw new Error(`The server answered ${r.status}.`);
    } catch (e) {
      if (signal?.aborted) throw e;
      if (!(e instanceof TypeError)) throw e;
      const health = await fetch(`${API_URL}/api/health`, { signal }).then((r) => (r.ok ? r.json() as Promise<{ origins?: string[] }> : null)).catch(() => null);
      if (health) {
        const here = location.origin;
        throw new Error((health.origins ?? []).includes(here)
          ? "The server is up but refused the request from this page. Check the handoff-api logs."
          : `The server is up but doesn't accept this page's address. On Render, set ALLOWED_ORIGINS on handoff-api to exactly ${here} (it has ${(health.origins ?? []).join(", ") || "nothing"}).`);
      }
      if (Date.now() - started > 150_000) {
        throw new Error(`Couldn't reach the server at ${API_URL || "this site"}. Open ${API_URL}/api/health to check it's running, and that VITE_API_URL points to it.`);
      }
    }
    onWait(Math.round((Date.now() - started) / 1000));
    await new Promise((r) => setTimeout(r, 3000));
  }
}

/** Where to fetch a library file from (paths like "index.json", "<id>/audio/mix.flac"). */
export interface LibrarySource {
  url(path: string): Promise<string>;
}

export function localLibrary(base: string): LibrarySource {
  return { url: async (path) => `${base}/${path}` };
}

/** Signed links from the backend: requests made together go out as one batch, and each link is
 *  reused until shortly before it expires. */
export function hostedLibrary(): LibrarySource {
  const cache = new Map<string, { url: string; until: number }>();
  let queue: { path: string; resolve: (u: string) => void; reject: (e: unknown) => void }[] = [];
  const flush = async () => {
    const batch = queue;
    queue = [];
    try {
      const r = await apiFetch("/api/library/sign", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: [...new Set(batch.map((b) => b.path))] }),
      });
      if (r.status === 401) { forgetCode(); throw new Error("The access code is no longer valid. Reload and enter it again."); }
      if (!r.ok) throw new Error(`Couldn't reach the library (${r.status}).`);
      const { urls, expires_in } = (await r.json()) as { urls: Record<string, string>; expires_in: number };
      const until = Date.now() + Math.max(60, expires_in - 300) * 1000;   // refresh 5 min early
      for (const b of batch) {
        const url = urls[b.path];
        if (url) { cache.set(b.path, { url, until }); b.resolve(url); } else b.reject(new Error(`Not in the library: ${b.path}`));
      }
    } catch (e) {
      batch.forEach((b) => b.reject(e));
    }
  };
  return {
    url(path) {
      const hit = cache.get(path);
      if (hit && hit.until > Date.now()) return Promise.resolve(hit.url);
      return new Promise((resolve, reject) => {
        if (queue.length === 0) setTimeout(() => void flush(), 0);
        queue.push({ path, resolve, reject });
      });
    },
  };
}

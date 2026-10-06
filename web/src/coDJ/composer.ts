import type { ComposeRequest } from "../contracts/composer";
import { assertTransitions, type Transitions } from "../contracts/transitions";

export async function composeLive(request: ComposeRequest, signal?: AbortSignal): Promise<Transitions> {
  let response: Response;
  try {
    response = await fetch("/api/composer/compose", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal,
    });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new Error("Can't reach the composer. Start the local composer server and try again.");
  }
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("Composer is unavailable. Start the local composer server and try again.");
  }
  const result: unknown = await response.json();
  if (!response.ok) {
    const detail = (result as { detail?: unknown })?.detail;
    throw new Error(typeof detail === "string" ? detail : `Composition failed (${response.status}). Try again.`);
  }
  assertTransitions(result);
  if (result.pairs.length !== 1 || result.pairs[0]!.out_track !== request.out_track || result.pairs[0]!.in_track !== request.in_track) {
    throw new Error("Composer returned a different pair of tracks");
  }
  return result;
}

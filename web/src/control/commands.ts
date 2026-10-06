// Discrete actions (buttons/pads). Continuous values live in ControlStore; one-shot
// actions are Commands. Every input source dispatches the same Command objects.
import type { ControlId, DeckId } from "./controls";

export type Command =
  | { type: "togglePlay"; deck: DeckId }
  | { type: "cue"; deck: DeckId }
  | { type: "sync"; deck: DeckId }
  | { type: "loop"; deck: DeckId; beats: number; slip?: boolean }   // same size again = exit loop. slip (automation): the
                                                                    // deck's clock runs on through the loop, leaving lands back in
                                                                    // time, and the start always snaps to the grid
  | { type: "jump"; deck: DeckId; beats: number }
  | { type: "seekFraction"; deck: DeckId; fraction: number }
  | { type: "load"; deck: DeckId; trackId: string }
  // Added for automation (Chunk 6). `at` is AudioContext time.
  | { type: "playAt"; deck: DeckId; at: number; fromBar?: number }   // start exactly at `at`, from bar `fromBar` (default: the paused position's nearest bar line)
  | { type: "pause"; deck: DeckId }                     // explicit (togglePlay would start a paused deck)
  | { type: "loopOff"; deck: DeckId }                   // explicit exit (loop with the same beats toggles)
  | { type: "setControlAt"; control: ControlId; value: number; at: number }   // exact-time step; the store follows at `at`
  | { type: "brakeAt"; deck: DeckId; at: number; beats: number }
  | { type: "keyShift"; deck: DeckId; semitones: number };   // nudge the deck's key shift by this many semitones (-6..+6 overall)            // turntable stop over `beats` beats from `at` (then pause it)

export type CommandHandler = (cmd: Command) => void;

export class CommandBus {
  private handlers = new Set<CommandHandler>();
  dispatch = (cmd: Command): void => {
    this.handlers.forEach((h) => h(cmd));
  };
  subscribe(h: CommandHandler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }
}

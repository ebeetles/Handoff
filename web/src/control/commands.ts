// Discrete actions (buttons/pads). Continuous values live in ControlStore; one-shot
// actions are Commands. Every input source dispatches the same Command objects.
import type { DeckId } from "./controls";

export type Command =
  | { type: "togglePlay"; deck: DeckId }
  | { type: "cue"; deck: DeckId }
  | { type: "sync"; deck: DeckId }
  | { type: "loop"; deck: DeckId; beats: number }   // same size again = exit loop
  | { type: "jump"; deck: DeckId; beats: number }
  | { type: "seekFraction"; deck: DeckId; fraction: number }
  | { type: "load"; deck: DeckId; trackId: string };

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

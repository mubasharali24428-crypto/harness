/**
 * One pending review decision at a time (W24 keyboard-first review).
 *
 * The review loop asks the operator about one file, then the next. The answer can come from a
 * keybinding, a status-bar command or a button on a notification, and all three land here. This
 * class is the whole of that rule, kept free of `vscode` so it can be tested:
 *
 * - a keybinding answers whatever is pending *now*;
 * - a button quotes the id of the prompt it belongs to, so a notification left over from the
 *   previous file cannot answer the next one;
 * - "skip all remaining" is remembered for the rest of the run, so the following files resolve to
 *   a rejection without asking (the core still records each one as rejected on the server).
 */

export type Verdict = "approve" | "reject" | "stop";

export class DecisionGate {
  private seq = 0;
  private current: { id: number; resolve: (verdict: Verdict) => void } | null = null;
  private skipRemaining = false;

  /** True while a decision is waiting for an answer. */
  get pending(): boolean {
    return this.current !== null;
  }

  /** A new run: nothing pending, nothing skipped. A decision left over from a dead run is rejected. */
  reset(): void {
    const stale = this.current;
    this.current = null;
    this.skipRemaining = false;
    stale?.resolve("reject");
  }

  /**
   * Wait for one decision. `onAsk` runs once the gate is armed and receives the id that a prompt
   * must quote to answer it. After a "skip all" this resolves to "reject" without calling it.
   */
  ask(onAsk: (id: number) => void): Promise<"approve" | "reject"> {
    if (this.skipRemaining) return Promise.resolve("reject");
    return new Promise((resolve) => {
      const id = ++this.seq;
      this.current = {
        id,
        resolve: (verdict) => {
          if (verdict === "stop") this.skipRemaining = true;
          resolve(verdict === "approve" ? "approve" : "reject");
        },
      };
      onAsk(id);
    });
  }

  /** Answer the pending decision; false when there is none, or the id belongs to an older prompt. */
  answer(verdict: Verdict, id?: number): boolean {
    const current = this.current;
    if (!current || (id !== undefined && id !== current.id)) return false;
    this.current = null;
    current.resolve(verdict);
    return true;
  }
}

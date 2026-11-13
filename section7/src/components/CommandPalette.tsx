"use client";

// U0 command palette: Cmd/Ctrl+K opens it, "g then letter" jumps straight to a section.
// cmdk supplies the dialog, focus trap, filtering and listbox semantics.
import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { NAV_COMMANDS, isTypingTarget, stepShortcut } from "@/lib/commands";

const KBD = "rounded border border-border bg-panel-2 px-1.5 py-0.5 font-mono text-[11px] text-muted";

export default function CommandPalette({ workingHref }: { workingHref?: string | null }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const pending = useRef(false);

  useEffect(() => {
    let timer = 0;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(t?.tagName, !!t?.isContentEditable)) {
        pending.current = false;
        return;
      }
      const step = stepShortcut(pending.current, e.key);
      pending.current = step.pending;
      window.clearTimeout(timer);
      // a lone "g" is forgotten after 1.5 s, so a later letter is not a surprise jump
      if (step.pending) timer = window.setTimeout(() => (pending.current = false), 1500);
      if (step.href) {
        e.preventDefault();
        router.push(step.href);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(timer);
    };
  }, [router]);

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };
  const item =
    "flex cursor-pointer items-center justify-between rounded-md px-3 py-2 text-sm text-text-2 " +
    "data-[selected=true]:bg-accent-soft data-[selected=true]:text-text";

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mx-3 mb-2 flex items-center justify-between rounded-md border border-border bg-panel-2 px-3 py-1.5 text-left text-[13px] text-muted"
      >
        <span>Go to…</span>
        <kbd className={KBD}>⌘K</kbd>
      </button>
      <Command.Dialog
        open={open}
        onOpenChange={setOpen}
        label="Command palette"
        overlayClassName="fixed inset-0 z-[90] bg-black/50"
        contentClassName="fixed left-1/2 top-[18vh] z-[91] w-[min(560px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-panel shadow-2xl"
      >
        <Command.Input
          placeholder="Where to?"
          className="w-full border-0 border-b border-border-soft bg-transparent px-4 py-3 text-[15px] text-text outline-none placeholder:text-muted"
        />
        <Command.List className="max-h-[50vh] overflow-y-auto p-2">
          <Command.Empty className="px-3 py-6 text-center text-sm text-muted">Nothing matches.</Command.Empty>
          <Command.Group heading="Go to" className="text-[11px] uppercase tracking-wider text-muted [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5">
            {workingHref ? (
              <Command.Item value="Working current run" onSelect={() => go(workingHref)} className={item}>
                <span className="normal-case tracking-normal">Working (current run)</span>
              </Command.Item>
            ) : null}
            {NAV_COMMANDS.map((c) => (
              <Command.Item key={c.id} value={`${c.label} ${c.keywords ?? ""}`} onSelect={() => go(c.href)} className={item}>
                <span className="normal-case tracking-normal">{c.label}</span>
                <span className="flex gap-1 normal-case">
                  <kbd className={KBD}>g</kbd>
                  <kbd className={KBD}>{c.key}</kbd>
                </span>
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command.Dialog>
    </>
  );
}

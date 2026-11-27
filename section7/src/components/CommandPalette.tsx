"use client";

// U0 command palette: Cmd/Ctrl+K opens it, "g then letter" jumps straight to a section.
// cmdk supplies the dialog, focus trap, filtering and listbox semantics.
import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { isTypingTarget, stepShortcut } from "@/lib/commands";
import { paletteGroups, type PaletteEntry, type PaletteGroup } from "@/lib/paletteModel";
import { applyTheme, nextTheme, normalizeTheme } from "@/lib/theme";

const KBD = "rounded border border-border bg-panel-2 px-1.5 py-0.5 font-mono text-[11px] text-muted";

export interface PaletteProject {
  id: string;
  name: string;
}

/** The palette's groups and items, from the pure model. Exported so tests render exactly what the
 *  dialog shows (cmdk's Command.Group/Item are passed in as plain wrappers there). */
export function PaletteGroups({
  groups,
  item,
  group,
  onSelect,
  Group = Command.Group,
  Item = Command.Item,
}: {
  groups: PaletteGroup[];
  item: string;
  group: string;
  onSelect: (e: PaletteEntry) => void;
  Group?: React.ElementType;
  Item?: React.ElementType;
}) {
  return (
    <>
      {groups.map((g) => (
        <Group key={g.heading} heading={g.heading} className={group}>
          {g.entries.map((e) => (
            <Item key={e.id} value={e.value} onSelect={() => onSelect(e)} className={item} data-entry={e.id}>
              <span className="normal-case tracking-normal">
                {e.label}
                {e.shortcut ? <span className="sr-only">, shortcut {e.shortcut}</span> : null}
              </span>
              {e.keys ? (
                <span className="flex gap-1 normal-case" aria-hidden="true">
                  {e.keys.map((k) => (
                    <kbd key={k} className={KBD}>{k}</kbd>
                  ))}
                </span>
              ) : e.note ? (
                <span className="normal-case text-muted">{e.note}</span>
              ) : null}
            </Item>
          ))}
        </Group>
      ))}
    </>
  );
}

export default function CommandPalette({
  role = null,
  workingHref,
  projects = [],
  selectedProject,
  onSelectProject,
  onSignOut,
}: {
  /** The viewer's role; gated actions show only for a role that can take them. */
  role?: string | null;
  workingHref?: string | null;
  projects?: PaletteProject[];
  selectedProject?: string | null;
  onSelectProject?: (id: string) => void;
  onSignOut?: () => void;
}) {
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
  const run = (fn: () => void) => {
    setOpen(false);
    fn();
  };
  const groups = paletteGroups(role, {
    workingHref, projects, selectedProject, canSwitchProject: Boolean(onSelectProject), canSignOut: Boolean(onSignOut),
  });
  const select = (e: PaletteEntry) => {
    if (e.href) return go(e.href);
    if (e.run === "theme") {
      return run(() => applyTheme(nextTheme(normalizeTheme(document.documentElement.dataset.theme) ?? "dark")));
    }
    if (e.run === "signout" && onSignOut) return run(onSignOut);
    if (e.run && typeof e.run === "object" && onSelectProject) {
      const id = e.run.project;
      return run(() => onSelectProject(id));
    }
  };
  const group =
    "text-[11px] uppercase tracking-wider text-muted [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5";
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
          <PaletteGroups groups={groups} item={item} group={group} onSelect={select} />
        </Command.List>
      </Command.Dialog>
    </>
  );
}

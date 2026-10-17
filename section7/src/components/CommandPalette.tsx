"use client";

// A4-S5 (F136/F137): keyboard-first command palette using the ARIA combobox + listbox pattern.
import * as React from "react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { type Command, matchCommands, matchesShortcut } from "@/lib/commandPalette";
import { trapFocus } from "@/lib/focus";
import { makeT, type Translate } from "@/lib/i18n";

export function CommandPaletteView(props: {
  commands: Command[];
  query: string;
  active: number;
  onQuery: (q: string) => void;
  onActive: (i: number) => void;
  onRun: (c: Command) => void;
  onClose: () => void;
  t?: Translate;
}) {
  const t = props.t ?? makeT("en");
  const id = useId();
  const results = matchCommands(props.commands, props.query);
  const active = Math.min(props.active, Math.max(results.length - 1, 0));
  return (
    <div role="dialog" aria-modal="true" aria-label={t("palette.title")} data-testid="palette">
      <input
        role="combobox"
        aria-expanded="true"
        aria-controls={`${id}-list`}
        aria-activedescendant={results[active] ? `${id}-${results[active].id}` : undefined}
        aria-autocomplete="list"
        aria-label={t("palette.placeholder")}
        placeholder={t("palette.placeholder")}
        value={props.query}
        onChange={(e) => props.onQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); props.onActive((active + 1) % Math.max(results.length, 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); props.onActive((active - 1 + results.length) % Math.max(results.length, 1)); }
          else if (e.key === "Enter" && results[active]) { e.preventDefault(); props.onRun(results[active]); }
          else if (e.key === "Escape") { e.preventDefault(); props.onClose(); }
        }}
      />
      <ul role="listbox" id={`${id}-list`} aria-label={t("palette.title")} style={{ listStyle: "none", padding: 0 }}>
        {results.map((c, i) => (
          <li key={c.id} id={`${id}-${c.id}`} role="option" aria-selected={i === active} onClick={() => props.onRun(c)}
            style={{ padding: "10px 12px", minHeight: 44, background: i === active ? "#1f6feb" : "transparent", color: i === active ? "#fff" : "inherit" }}>
            {c.title}{c.shortcut ? <kbd style={{ marginInlineStart: 8, color: "inherit" }}>{c.shortcut.replace("mod", "Ctrl/⌘")}</kbd> : null}
          </li>
        ))}
      </ul>
      {results.length === 0 && <p role="status">{t("palette.none")}</p>}
    </div>
  );
}

export function CommandPalette({ commands, openShortcut = "mod+k", defaultOpen = false, t }: {
  commands: Command[]; openShortcut?: string; defaultOpen?: boolean; t?: Translate;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const opener = useRef<Element | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (matchesShortcut(e, openShortcut)) {
        e.preventDefault();
        opener.current = document.activeElement;
        setOpen((o) => !o);
        return;
      }
      if (open) return;
      const hit = commands.find((c) => c.shortcut && matchesShortcut(e, c.shortcut));
      if (hit) { e.preventDefault(); hit.run(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [commands, open, openShortcut]);
  useEffect(() => {
    if (!open || !box.current) return;
    const release = trapFocus(box.current);
    box.current.querySelector<HTMLInputElement>("input")?.focus();
    return () => { release(); (opener.current as HTMLElement | null)?.focus?.(); };
  }, [open]);
  const close = () => { setOpen(false); setQuery(""); setActive(0); };
  const tt = useMemo(() => t ?? makeT("en"), [t]);
  if (!open) return null;
  return (
    <div ref={box}>
      <CommandPaletteView commands={commands} query={query} active={active} onQuery={(q) => { setQuery(q); setActive(0); }}
        onActive={setActive} onRun={(c) => { close(); c.run(); }} onClose={close} t={tt} />
    </div>
  );
}

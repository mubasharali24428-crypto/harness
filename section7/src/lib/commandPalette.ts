// A4-S5 (F136): command registry, fuzzy matching and shortcut parsing/conflict detection.
export interface Command {
  id: string;
  title: string;
  keywords?: string[];
  shortcut?: string; // a single chord, e.g. "mod+shift+p" ("mod" = Ctrl or Cmd)
  run: () => void;
}

// Chords owned by the browser/OS: binding them would break the user's own tools.
export const RESERVED = new Set(["mod+w", "mod+t", "mod+n", "mod+q", "mod+l", "mod+r", "mod+tab", "mod+shift+t"]);

export interface Chord { mod: boolean; shift: boolean; alt: boolean; key: string }

export function parseShortcut(s: string): Chord | null {
  const parts = s.toLowerCase().split("+").map((p) => p.trim()).filter(Boolean);
  const key = parts.pop();
  if (!key || key.length === 0) return null;
  const mods = new Set(parts);
  for (const m of mods) if (!["mod", "shift", "alt"].includes(m)) return null;
  if (mods.size !== parts.length) return null; // duplicate modifier
  return { mod: mods.has("mod"), shift: mods.has("shift"), alt: mods.has("alt"), key };
}

export const normalize = (c: Chord) => `${c.mod ? "mod+" : ""}${c.shift ? "shift+" : ""}${c.alt ? "alt+" : ""}${c.key}`;

export function matchesShortcut(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">, shortcut: string): boolean {
  const c = parseShortcut(shortcut);
  if (!c) return false;
  return (e.ctrlKey || e.metaKey) === c.mod && e.shiftKey === c.shift && e.altKey === c.alt && e.key.toLowerCase() === c.key;
}

export function validateCommands(cmds: Command[]): string[] {
  const problems: string[] = [];
  const seenId = new Set<string>();
  const seenChord = new Map<string, string>();
  for (const c of cmds) {
    if (seenId.has(c.id)) problems.push(`duplicate id ${c.id}`);
    seenId.add(c.id);
    if (!c.shortcut) continue;
    const chord = parseShortcut(c.shortcut);
    if (!chord) { problems.push(`${c.id}: invalid shortcut '${c.shortcut}'`); continue; }
    const n = normalize(chord);
    if (RESERVED.has(n)) problems.push(`${c.id}: '${n}' is reserved by the browser`);
    const other = seenChord.get(n);
    if (other) problems.push(`${c.id}: '${n}' conflicts with ${other}`);
    else seenChord.set(n, c.id);
  }
  return problems;
}

function score(q: string, c: Command): number {
  const hay = [c.title, ...(c.keywords ?? [])].map((s) => s.toLowerCase());
  let best = 0;
  for (const h of hay) {
    if (h === q) best = Math.max(best, 100);
    else if (h.startsWith(q)) best = Math.max(best, 80);
    else if (h.split(/\s+/).some((w) => w.startsWith(q))) best = Math.max(best, 60);
    else if (h.includes(q)) best = Math.max(best, 40);
    else {
      let i = 0;
      for (const ch of h) if (ch === q[i]) i++;
      if (i === q.length) best = Math.max(best, 20); // subsequence
    }
  }
  return best;
}

export function matchCommands(cmds: Command[], query: string): Command[] {
  const q = query.trim().toLowerCase();
  if (!q) return cmds;
  return cmds
    .map((c) => ({ c, s: score(q, c) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.c.title.localeCompare(b.c.title))
    .map((x) => x.c);
}

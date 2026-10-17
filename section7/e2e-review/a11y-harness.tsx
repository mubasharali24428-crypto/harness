import * as React from "react";
import { createRoot } from "react-dom/client";
import { CommandPalette } from "../src/components/CommandPalette";
import { StatusView } from "../src/components/StatusView";
import { TaskInboxView } from "../src/components/TaskInbox";
import type { Command } from "../src/lib/commandPalette";
import { dirFor, formatDateTime, makeT, parseLocale } from "../src/lib/i18n";

const q = new URLSearchParams(location.search);
const locale = parseLocale(q.get("lang"));
const t = makeT(locale);
document.documentElement.lang = locale;
document.documentElement.dir = dirFor(locale);

function App() {
  const [last, setLast] = React.useState("none");
  const commands: Command[] = [
    { id: "inbox", title: "Open task inbox", shortcut: "mod+shift+i", run: () => setLast("inbox") },
    { id: "review", title: "Open review", keywords: ["diff"], run: () => setLast("review") },
    { id: "status", title: "Service status", run: () => setLast("status") },
  ];
  const items = [{ id: "i1", status: "pending_approval" as const, provider: "github", kind: "issue", title: "Fix login", body: "Steps", url: "https://example.com", actor: "alice", trigger: "label" }];
  return (
    <main>
      <h1>Harness</h1>
      <button id="before">before</button>
      <TaskInboxView items={items} canDecide busyId={null} error={null} notice={null} onDecide={() => {}} t={t} />
      <StatusView t={t} checkedAt="2026-03-08T06:30:00Z"
        components={[{ name: "identity", state: "operational" }, { name: "sandbox", state: "not_configured" }, { name: "context", state: "unreachable" }]} />
      <p data-testid="when">{formatDateTime("2026-03-08T06:30:00Z", locale, q.get("tz") ?? "UTC")}</p>
      <p data-testid="last">{last}</p>
      <CommandPalette commands={commands} t={t} defaultOpen={q.get("palette") === "1"} />
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

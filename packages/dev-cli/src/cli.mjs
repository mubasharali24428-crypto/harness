// Zero-dependency CLI. The token comes from HARNESS_TOKEN only — never argv, never printed.
const P = "/api/v1/developer-workflow";

export class CliError extends Error {}

const redact = (s, token) => (token ? String(s).split(token).join("[token]") : String(s));

function safeBase(raw) {
  const u = new URL(raw);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (u.username || u.password) throw new CliError("base URL must not contain credentials");
  if (u.protocol !== "https:" && !(u.protocol === "http:" && local))
    throw new CliError("base URL must be https (http only for localhost)");
  return u.origin;
}

async function call(env, which, method, path, body) {
  const token = env.HARNESS_TOKEN;
  if (!token) throw new CliError("set HARNESS_TOKEN (not accepted as an argument)");
  const base = safeBase(which === "intake" ? env.HARNESS_INTAKE_BASE || env.HARNESS_BASE : env.HARNESS_BASE);
  const res = await fetch(base + path, {
    method,
    redirect: "error",
    headers: { authorization: `Bearer ${token}`, ...(body ? { "content-type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const e = data?.error ?? data?.detail?.error ?? {};
    throw new CliError(redact(`${res.status} ${e.code ?? "ERROR"}: ${e.message ?? "request failed"}`, token));
  }
  return data;
}

const kv = (pairs) =>
  Object.fromEntries(pairs.map((p) => {
    const i = p.indexOf("=");
    if (i < 1) throw new CliError(`expected key=value, got '${p}'`);
    return [p.slice(0, i), p.slice(i + 1)];
  }));

export const USAGE = `harness-task <command>
  templates                         list task templates
  render <template> key=value...    render a goal (does not start a run)
  tutorial                          create the tutorial project, print its repoRef
  onboarding                        show derived onboarding progress
  readiness <sessionId>             repository readiness report
  intakes [status]                  list queued issue/PR intakes (HARNESS_INTAKE_BASE)
  approve <intakeId> | reject <intakeId>   human decision; nothing is run
env: HARNESS_BASE, HARNESS_TOKEN, [HARNESS_INTAKE_BASE]`;

export async function run(argv, env = process.env, out = console.log) {
  const [cmd, ...a] = argv;
  const need = (n, what) => { if (a.length < n) throw new CliError(`usage: ${what}`); };
  switch (cmd) {
    case "templates": {
      const r = await call(env, "wf", "GET", `${P}/task-templates`);
      r.items.forEach((t) => out(`${t.id}\t${t.title}\t(${t.params.join(", ")})`));
      return;
    }
    case "render": {
      need(1, "render <template> key=value...");
      const r = await call(env, "wf", "POST", `${P}/task-templates/${encodeURIComponent(a[0])}/render`, { values: kv(a.slice(1)) });
      out(r.goal);
      return;
    }
    case "tutorial": {
      const r = await call(env, "wf", "POST", `${P}/tutorial-project`);
      out(r.repoRef);
      return;
    }
    case "onboarding": {
      const r = await call(env, "wf", "GET", `${P}/onboarding`);
      r.steps.forEach((s) => out(`${s.done ? "[x]" : "[ ]"} ${s.label}`));
      out(r.complete ? "complete" : `next: ${r.next}`);
      return;
    }
    case "readiness": {
      need(1, "readiness <sessionId>");
      const r = await call(env, "wf", "GET", `${P}/code-sessions/${encodeURIComponent(a[0])}/readiness`);
      r.checks.forEach((c) => out(`${c.status.toUpperCase().padEnd(5)} ${c.label}${c.fix ? ` — ${c.fix}` : ""}`));
      out(`verdict: ${r.verdict} (${r.score}/${r.total})`);
      return;
    }
    case "intakes": {
      const q = a[0] ? `?status=${encodeURIComponent(a[0])}` : "";
      const r = await call(env, "intake", "GET", `${P}/intake/intakes${q}`);
      r.items.forEach((i) => out(`${i.id}\t${i.status}\t${i.provider}\t${i.kind}\t${i.title}`));
      return;
    }
    case "approve":
    case "reject": {
      need(1, `${cmd} <intakeId>`);
      const r = await call(env, "intake", "POST", `${P}/intake/intakes/${encodeURIComponent(a[0])}/${cmd}`);
      out(`${r.status}${r.dispatch === "needs-integration" ? " (recorded; no run started)" : ""}`);
      return;
    }
    default:
      throw new CliError(USAGE);
  }
}

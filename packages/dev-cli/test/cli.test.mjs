import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { run, CliError } from "../src/cli.mjs";

const svc = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../integrations/editors/service");
const py = path.join(svc, ".venv/bin/python");
let proc, base;
const lines = [];
const out = (l) => lines.push(l);

before(async () => {
  if (!existsSync(py)) return;
  proc = spawn(py, [path.join(svc, "tests/serve_e2e.py")], { stdio: ["ignore", "pipe", "inherit"] });
  base = await new Promise((resolve, reject) => {
    let buf = "";
    proc.stdout.on("data", (d) => {
      buf += d;
      const l = buf.split("\n").find((x) => x.startsWith("{"));
      if (l) resolve(JSON.parse(l).base);
    });
    proc.on("exit", () => reject(new Error("service exited")));
  });
});
after(() => proc?.kill("SIGKILL"));

const env = (token = "tok-eng-a") => ({ HARNESS_BASE: base, HARNESS_TOKEN: token });
const real = { skip: !existsSync(py) && "service venv missing" };

test("templates + render against the real service; render never starts a run", real, async () => {
  lines.length = 0;
  await run(["templates"], env(), out);
  assert.ok(lines.some((l) => l.startsWith("fix-bug\t")));
  lines.length = 0;
  await run(["render", "fix-bug", "summary=login fails", "expected=redirects"], env(), out);
  assert.match(lines[0], /^Fix this bug: login fails/);
});

test("tutorial then onboarding reflect real state", real, async () => {
  lines.length = 0;
  await run(["tutorial"], env("tok-eng-b"), out);
  assert.ok(lines[0].includes("tutorial"));
  lines.length = 0;
  await run(["onboarding"], env("tok-eng-b"), out);
  assert.ok(lines.includes("[x] Create the tutorial project"));
  assert.ok(lines.includes("next: session"));
});

test("server errors surface as clean messages; read-only role is denied", real, async () => {
  await assert.rejects(run(["tutorial"], env("tok-view-a"), out), /403 ROLE_DENIED/);
  await assert.rejects(run(["templates"], env("nope"), out), /401/);
});

test("token must come from env, base must be https or localhost, no credentials", async () => {
  await assert.rejects(run(["templates"], { HARNESS_BASE: "http://localhost:1" }, out), /HARNESS_TOKEN/);
  await assert.rejects(run(["templates"], { HARNESS_BASE: "http://evil.example", HARNESS_TOKEN: "t" }, out), /https/);
  await assert.rejects(run(["templates"], { HARNESS_BASE: "https://u:p@x.example", HARNESS_TOKEN: "t" }, out), /credentials/);
  await assert.rejects(run(["render"], { HARNESS_BASE: "http://localhost:1", HARNESS_TOKEN: "t" }, out), CliError);
  await assert.rejects(run(["bogus"], {}, out), /harness-task/);
});

test("token is redacted from error text", async () => {
  const http = await import("node:http");
  const srv = http.createServer((_, res) => {
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "X", message: "boom SECRET-TOKEN-123 boom" } }));
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  const e = { HARNESS_BASE: `http://127.0.0.1:${srv.address().port}`, HARNESS_TOKEN: "SECRET-TOKEN-123" };
  await assert.rejects(run(["templates"], e, out), (err) => !err.message.includes("SECRET-TOKEN-123") && /\[token\]/.test(err.message));
  srv.close();
});

test("intake commands hit the intake base and approve says no run started", async () => {
  const http = await import("node:http");
  const seen = [];
  const srv = http.createServer((req, res) => {
    seen.push(`${req.method} ${req.url} ${req.headers.authorization}`);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(req.url.endsWith("/approve") ? { status: "approved", dispatch: "needs-integration" } : { items: [{ id: "i1", status: "pending_approval", provider: "github", kind: "issue", title: "T" }] }));
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  const e = { HARNESS_BASE: "https://unused.example", HARNESS_INTAKE_BASE: `http://127.0.0.1:${srv.address().port}`, HARNESS_TOKEN: "tk" };
  lines.length = 0;
  await run(["intakes", "pending_approval"], e, out);
  await run(["approve", "i1"], e, out);
  assert.equal(seen[0], "GET /api/v1/developer-workflow/intake/intakes?status=pending_approval Bearer tk");
  assert.match(lines.at(-1), /no run started/);
  srv.close();
});

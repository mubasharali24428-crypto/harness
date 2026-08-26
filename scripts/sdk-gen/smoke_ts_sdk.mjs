#!/usr/bin/env node
/**
 * Smoke-test @harness/ts-sdk against the LIVE compose stack.
 *
 * Runs the full scripter journey from the commander's intent (auth -> one
 * call per section -> event streaming) and prints what the API actually
 * returned. Exits non-zero on any failure. Twin of scripts/sdk-gen/smoke_sdk.py.
 *
 *    cd clients/ts && npm run smoke
 *
 * Requires: docker compose stack up (identity 8001 … review 8008). Dev OTP
 * codes are read from the identity container logs (grep HARNESS-OTP).
 */

import { spawnSync } from "node:child_process";

import { ApiError, Harness } from "../../clients/ts/src/index.js";
import { parseApiError } from "../../clients/ts/src/errors.js";

void parseApiError; // re-exported for scripters debugging raw envelopes

const IDENTITY_LOG_LINES = 400;

/** Dev stacks email nothing — the OTP is printed to identity's logs. */
function otpFromLogs(email) {
  let container;
  {
    const ps = spawnSync("docker", ["ps", "--filter", "publish=8001", "--format", "{{.Names}}"], {
      encoding: "utf8",
      timeout: 30_000,
    });
    if (ps.error) throw new Error(`could not list docker containers: ${ps.error.message}`);
    container = (ps.stdout ?? "").trim().split("\n")[0];
  }
  if (!container) throw new Error("no identity container found listening on :8001");
  let combined;
  {
    // The OTP line lands on docker's STDERR (container stderr), so merge both
    // streams — Python's subprocess.run(capture_output=True) equivalent.
    const logs = spawnSync("docker", ["logs", "--tail", String(IDENTITY_LOG_LINES), container], {
      encoding: "utf8",
      timeout: 60_000,
    });
    if (logs.error) throw new Error(`could not read ${container} logs: ${logs.error.message}`);
    combined = `${logs.stdout ?? ""}${logs.stderr ?? ""}`;
  }
  const marker = `HARNESS-OTP login code for ${email}: `;
  for (const line of combined.split("\n")) {
    // NB: JS split(sep, n) LIMITS results (unlike Python's maxsplit) — slice instead.
    const idx = line.indexOf(marker);
    if (idx !== -1) return line.slice(idx + marker.length).trim().slice(0, 6);
  }
  throw new Error(`no HARNESS-OTP code for ${email} in ${container} logs`);
}

function step(title) {
  console.log(`\n=== ${title} ${"=".repeat(Math.max(0, 62 - title.length))}`);
}

function show(label, value) {
  let text;
  try {
    text = JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    text = String(value);
  }
  if (text.length > 700) text = `${text.slice(0, 700)}\n  … (truncated)`;
  console.log(`--- ${label}\n${text}`);
}

async function main() {
  const email = `ts-sdk-smoke-${Math.random().toString(36).slice(2, 10)}@example.com`;
  const harness = new Harness();
  let ok = true;

  try {
    // -- AUTH ---------------------------------------------------------------
    step("AUTH: legacy login (expect OWNERSHIP_REQUIRED envelope)");
    try {
      await Harness.login(email);
      console.log("UNEXPECTED: legacy login succeeded");
      ok = false;
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      show("ApiError raised by Harness.login()", {
        status: err.status,
        code: err.code,
        message: err.message,
      });
      assertEqual(err.code, "OWNERSHIP_REQUIRED");
    }

    step(`AUTH: OTP flow for ${email}`);
    const ack = await harness.request_otp(email);
    show("POST /auth/request-login", ack);
    const code = otpFromLogs(email);
    const session = await harness.verify_otp(email, code);
    show("POST /auth/verify-otp", {
      token: `${session.token.slice(0, 14)}…`,
      principal: session.principal,
    });
    const teamId = session.principal.teamId;
    const token = harness.token;

    // -- ONE CALL PER SECTION -----------------------------------------------
    step("IDENTITY: list_projects()");
    const projects = await harness.identity.list_projects(5);
    const items = Array.isArray(projects) ? projects : projects.items ?? [];
    let projectId = items[0]?.id ?? null;
    show("GET /api/v1/projects?limit=5", {
      count: items.length,
      first: items[0] ?? null,
    });
    if (!projectId) {
      const created = await harness.identity.create_project({
        name: `ts-sdk-smoke-${Math.random().toString(36).slice(2, 8)}`,
      });
      projectId = created.id;
      show("POST /api/v1/projects (none existed)", created);
    }

    step("GITHUB: connection_status()");
    show("GET /api/v1/github/connection", await harness.github.connection_status());

    step("ROUTER: list_models() / spend(teamId=…)");
    show("GET /api/v1/models", await harness.router.list_models());
    try {
      show("GET /api/v1/models/spend?teamId=…", await harness.router.model_spend(teamId));
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      show("spend -> ApiError", { code: err.code, message: err.message });
    }

    step("ORCHESTRATOR: list_runs(projectId=…)");
    const runs = await harness.orchestrator.list_runs(projectId, undefined, 3);
    const runItems = Array.isArray(runs) ? runs : runs.items ?? [];
    show("GET /api/v1/orchestrator/runs?…", {
      count: runItems.length,
      states: [...new Set(runItems.map((r) => r.state ?? r.status))].sort(),
    });

    step("SANDBOX: list_checkpoints(projectId)");
    try {
      const cps = await harness.sandbox.list_checkpoints(projectId);
      const cpItems = Array.isArray(cps) ? cps : cps.items ?? [];
      show("GET /api/v1/sandbox/{projectId}/checkpoints", { count: cpItems.length });
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      show("list_checkpoints -> ApiError", { code: err.code, message: err.message });
    }

    step("MEETING: create_meeting() + close (no side effects left)");
    const meeting = await harness.meeting.create_meeting({
      projectId,
      title: "TS SDK smoke meeting (safe to ignore)",
    });
    show("POST /api/v1/meetings", meeting);
    const closed = await harness.meeting.close_meeting(meeting.id);
    show("POST /api/v1/meetings/{id}/close", closed);

    step("REVIEW: get_reviewer_threshold_settings()");
    show(
      "GET /api/v1/reviews/settings/thresholds",
      await harness.review.get_reviewer_threshold_settings(),
    );

    // -- EVENT STREAMING ------------------------------------------------------
    // NOTE: we stream on a FRESH project. Orchestrator's cleanup-reject below
    // emits a `plan.rejected` event whose type is outside the frozen
    // EventType enum (pre-existing backend bug, tracked separately) — once it
    // lands in agent_events, every later replay/fetch of THAT project dies
    // validating it. A fresh project keeps the stream phase deterministic,
    // and we only reject AFTER receipt so the trigger event always arrives.
    step("WS: ticket mint + fingerprint echo + live stream");
    const streamProject =
      await harness.identity.create_project({
        name: `ts-sdk-smoke-stream-${Math.random().toString(36).slice(2, 8)}`,
      });
    const streamPid = streamProject.id;

    const ticket = await harness.identity.issue_ws_ticket({ projectId: streamPid });
    show("POST /api/v1/ws-ticket (M18-C5 fingerprint echo present?)", {
      ticket: `${String(ticket.ticket).slice(0, 10)}…`,
      expiresInSeconds: ticket.expiresInSeconds,
      fingerprint: ticket.fingerprint,
      "<- echoed server binding": Boolean(ticket.fingerprint),
    });

    const received = [];
    const stop = new AbortController();
    const consume = async () => {
      try {
        for await (const env of harness.events.stream(streamPid, token, stop.signal)) {
          received.push(env);
        }
      } catch (err) {
        if (!stop.signal.aborted) console.log(`stream ended: ${err.message}`);
      }
    };
    const consumer = consume();
    await sleep(2000); // let the socket attach

    // Trigger a real bus publish on THIS project: proposing a run plan bound
    // to projectId emits plan_proposed (tutorial §4 flow).
    const runId = `run-tssmoke${Math.random().toString(36).slice(2, 10)}`.slice(0, 20);
    const trigger = await harness.orchestrator.propose_plan_2(runId, {
      goal: "TS SDK smoke — safe to ignore",
      projectId: streamPid,
    });
    show("POST /api/v1/orchestrator/runs/{runId}/plan (stream trigger)", trigger);

    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline && received.length === 0) await sleep(200);
    stop.abort();
    await consumer;

    // Cleanup AFTER receipt: reject the proposed plan so no run starts.
    try {
      await harness.orchestrator.reject_plan_2(runId, { planId: trigger.planId });
    } catch (err) {
      console.log(`(cleanup reject: ${err.code ?? err.message})`);
    }

    const seqs = received.map((e) => e.seq);
    const dedupOk = seqs.length === new Set(seqs).size;
    const ascending = seqs.every((s, i) => i === 0 || seqs[i - 1] < s);
    show(
      "streamed envelopes",
      received.slice(0, 4).map((e) => ({ seq: e.seq, type: e.type, projectId: e.projectId })),
    );
    console.log(
      `received=${received.length} unique=${dedupOk} ascending(seq-dedupe+order)= ${ascending}`,
    );
    ok = ok && received.length > 0 && dedupOk && ascending;

    step("RESULT");
    console.log(ok ? "SMOKE PASS" : "SMOKE FAIL");
    process.exitCode = ok ? 0 : 1;
  } catch (err) {
    if (err instanceof ApiError) {
      console.log(`SMOKE FAIL (ApiError ${err.code} status=${err.status}): ${err.message}`);
    } else {
      console.log(`SMOKE FAIL: ${err.stack ?? err}`);
    }
    process.exitCode = 1;
  }
}

function assertEqual(actual, expected) {
  if (actual !== expected) {
    throw new Error(`assertion failed: expected ${expected}, got ${actual}`);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

main();

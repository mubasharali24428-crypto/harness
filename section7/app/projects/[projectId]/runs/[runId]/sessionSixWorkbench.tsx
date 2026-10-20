"use client";

import * as React from "react";
import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { AnnounceContext, LiveRegions, useAnnouncer } from "@/components/LiveAnnouncer";
import { Button } from "@/components/ui";
import { ORCHESTRATOR_BASE } from "@/lib/api";

type AnyRow = Record<string, any>;
const base = `${ORCHESTRATOR_BASE}/api/v1/orchestrator`;
const box = { border: "1px solid var(--border)", borderRadius: 6, padding: 12, margin: "10px 0" };
const field = { display: "block", width: "100%", margin: "5px 0 10px", padding: 8 };

async function call(path: string, token: string, init: RequestInit = {}): Promise<any> {
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: { authorization: `Bearer ${token}`, ...(init.body instanceof FormData ? {} : { "content-type": "application/json" }), ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: any = null;
    try { detail = await res.json(); } catch { /* response may be plain text */ }
    throw new Error(detail?.detail?.error?.message ?? detail?.error?.message ?? `Request failed (${res.status})`);
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") ?? "";
  return type.includes("application/json") ? res.json() : res.blob();
}
const send = (path: string, token: string, method: string, body?: unknown) => call(path, token, {
  method, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const safe = (v: unknown) => encodeURIComponent(String(v));
const listOf = (v: any, ...keys: string[]): AnyRow[] => {
  for (const k of keys) if (Array.isArray(v?.[k])) return v[k];
  return [];
};

export function SessionSixWorkbench({ runId, token }: { runId: string; token: string }) {
  const { announce: localAnnounce, region } = useAnnouncer();
  const announce = useContext(AnnounceContext) ?? localAnnounce;
  const [data, setData] = useState<Record<string, any>>({});
  const [latestExport, setLatestExport] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [task, setTask] = useState({ statement: "", successCriteria: "", constraints: "", ownerUserId: "", expectedVersion: 0 });
  const [planText, setPlanText] = useState("");
  const [comment, setComment] = useState("");
  const [editPatch, setEditPatch] = useState('{"op":"replace","path":"/goal","value":""}');
  const [subtaskTitle, setSubtaskTitle] = useState("");
  const [completion, setCompletion] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [query, setQuery] = useState("");
  const [searchFilter, setSearchFilter] = useState({ kind: "", projectId: "", participantId: "", status: "", approvalState: "", from: "", to: "" });
  const [exportScope, setExportScope] = useState(runId);
  const [exportKind, setExportKind] = useState("run_history");
  const [format, setFormat] = useState("json");
  const [deletionReason, setDeletionReason] = useState("");
  const planLoaded = useRef(false);
  const pid = safe(runId);

  const reload = useCallback(async () => {
    setError("");
    const paths: Record<string, string> = {
      task: `/runs/${pid}/task`, plans: `/runs/${pid}/plan/versions`, subtasks: `/runs/${pid}/subtasks`,
      limits: `/runs/${pid}/execution-limits`, status: `/runs/${pid}/execution-status`, completion: `/runs/${pid}/completion`,
      artifacts: `/runs/${pid}/artifacts`, changes: `/runs/${pid}/changes`, git: `/runs/${pid}/git-state`,
      permissions: `/runs/${pid}/git-permissions`, lineage: `/runs/${pid}/lineage`, exports: "/exports", deletions: "/deletion-requests", searchStatus: "/search/status",
    };
    const entries = await Promise.all(Object.entries(paths).map(async ([key, path]) => {
      try { return [key, await call(path, token)] as const; } catch { return [key, null] as const; }
    }));
    const next = Object.fromEntries(entries);
    setData((current) => ({ ...current, ...next }));
    const t = next.task;
    if (t) setTask({ statement: t.statement ?? "", successCriteria: (t.successCriteria ?? []).join("\n"), constraints: (t.constraints ?? []).join("\n"), ownerUserId: t.owner?.userId ?? "", expectedVersion: t.version ?? 0 });
    const versions = listOf(next.plans, "versions", "items");
    if (versions.length && !planLoaded.current) {
      setPlanText(JSON.stringify(versions[0].content ?? {}, null, 2));
      planLoaded.current = true;
    }
  }, [pid, token]);

  useEffect(() => { void reload(); }, [reload]);

  const act = async (label: string, work: () => Promise<any>) => {
    setBusy(true); setError(""); setNotice("");
    try { await work(); await reload(); setNotice(label); announce?.(label); }
    catch (e) { const msg = (e as Error).message; setError(msg); announce?.(msg, true); }
    finally { setBusy(false); }
  };

  const download = async (path: string, name: string) => {
    const blob = await call(path, token);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
  };

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    const params = new URLSearchParams({ q: query, limit: "25" });
    Object.entries(searchFilter).forEach(([k, v]) => { if (v) params.set(k, v); });
    setBusy(true); setError("");
    try { const hits = await call(`/search?${params}`, token); setData((d) => ({ ...d, hits })); setNotice("Search updated"); announce?.("Search updated"); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  return <>
    {region}
    <section aria-label="Plans, execution, artifacts, and history" style={{ marginTop: 20 }}>
    <h2>Plan and work history</h2>
    {error && <p role="alert" className="pill red">{error}</p>}
    {notice && <p role="status" className="pill blue">{notice}</p>}

    <section style={box} aria-labelledby="task-heading">
      <h3 id="task-heading">Task statement</h3>
      <label>Task<textarea value={task.statement} onChange={(e) => setTask({ ...task, statement: e.target.value })} style={field} rows={3} /></label>
      <label>Success criteria<textarea value={task.successCriteria} onChange={(e) => setTask({ ...task, successCriteria: e.target.value })} style={field} rows={2} /></label>
      <label>Constraints<textarea value={task.constraints} onChange={(e) => setTask({ ...task, constraints: e.target.value })} style={field} rows={2} /></label>
      <label>Owner user ID<input value={task.ownerUserId} onChange={(e) => setTask({ ...task, ownerUserId: e.target.value })} style={field} /></label>
      <Button disabled={busy || !task.statement.trim()} onClick={() => void act("Task statement saved", () => send(`/runs/${pid}/task`, token, "PUT", { statement: task.statement, successCriteria: task.successCriteria.split("\n").filter(Boolean), constraints: task.constraints.split("\n").filter(Boolean), ownerUserId: task.ownerUserId || null, expectedVersion: task.expectedVersion }))}>Save task</Button>
      <span> Version {data.task?.version ?? 0}</span>
    </section>

    <section style={box} aria-labelledby="plans-heading">
      <h3 id="plans-heading">Plan versions</h3>
      <ul>{listOf(data.plans, "versions", "items").map((v) => <li key={v.version}>
        <button type="button" onClick={() => void act(`Loaded plan version ${v.version}`, async () => { const x = await call(`/runs/${pid}/plan/versions/${v.version}`, token); setPlanText(JSON.stringify(x.content ?? x.version?.content ?? {}, null, 2)); })}>
          Version {v.version} · {v.state} · {String(v.contentSha256 ?? v.content_sha256 ?? "").slice(0, 12)}
        </button>{" "}<button type="button" onClick={() => void act(`Loaded version ${v.version} diff`, async () => { const diff = await call(`/runs/${pid}/plan/versions/${v.version}/diff`, token); setData((d) => ({ ...d, planDiff: diff })); })}>View diff</button>
        {" "}<button type="button" onClick={() => void act(`Loaded review for version ${v.version}`, async () => {
          const [comments, edits] = await Promise.all([call(`/runs/${pid}/plan/versions/${v.version}/comments`, token), call(`/runs/${pid}/plan/versions/${v.version}/edits`, token)]);
          setData((d) => ({ ...d, planReview: { version: v.version, comments, edits } }));
        })}>View comments and edits</button>
        {v.can?.approve ? <>{" "}<Button disabled={busy} onClick={() => void act(`Plan version ${v.version} approved`, () => send(`/runs/${pid}/plan/versions/${v.version}/approve`, token, "POST", {}))}>Approve version {v.version}</Button></> : null}
      </li>)}</ul>
      <label>Plan content (JSON)<textarea value={planText} onChange={(e) => setPlanText(e.target.value)} style={{ ...field, fontFamily: "monospace" }} rows={8} /></label>
      <Button disabled={busy} onClick={() => void act("Plan version created", () => send(`/runs/${pid}/plan/versions`, token, "POST", { content: JSON.parse(planText), expectedVersion: Math.max(0, ...listOf(data.plans, "versions", "items").map((v) => Number(v.version))) }))}>Create proposed version</Button>
      <form onSubmit={(e) => { e.preventDefault(); const n = Number((listOf(data.plans, "versions", "items")[0] ?? {}).version); if (n) void act("Comment added", () => send(`/runs/${pid}/plan/versions/${n}/comments`, token, "POST", { body: comment })); }}>
        <label>Reviewer comment<textarea value={comment} onChange={(e) => setComment(e.target.value)} style={field} rows={2} /></label><Button type="submit" disabled={busy || !comment.trim()}>Add comment</Button>
      </form>
      <label>Proposed plan edit (JSON patch)<textarea value={editPatch} onChange={(e) => setEditPatch(e.target.value)} style={{ ...field, fontFamily: "monospace" }} rows={2} /></label>
      <Button disabled={busy} onClick={() => { const n = Number((listOf(data.plans, "versions", "items")[0] ?? {}).version); if (n) void act("Plan edit proposed", () => send(`/runs/${pid}/plan/versions/${n}/edits`, token, "POST", { patch: [JSON.parse(editPatch)] })); }}>Propose edit</Button>
      {data.planDiff && <pre tabIndex={0} aria-label="Plan version difference" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(data.planDiff, null, 2)}</pre>}
      {data.planReview && <div aria-label="Plan comments and proposed edits"><h4>Version {data.planReview.version} review</h4>
        <ul>{listOf(data.planReview.comments, "comments", "items").map((c) => <li key={c.id}>{c.author?.name ?? c.author?.userId}: {c.body}</li>)}</ul>
        <ul>{listOf(data.planReview.edits, "edits", "items").map((edit) => <li key={edit.id}>{edit.rationale ?? "Proposed edit"} · {edit.state} {edit.state === "pending" && <>
          <Button disabled={busy} onClick={() => void act("Plan edit accepted", () => send(`/runs/${pid}/plan/versions/${data.planReview.version}/edits/${safe(edit.id)}/decide`, token, "POST", { decision: "accept" }))}>Accept edit</Button>{" "}
          <Button disabled={busy} onClick={() => void act("Plan edit rejected", () => send(`/runs/${pid}/plan/versions/${data.planReview.version}/edits/${safe(edit.id)}/decide`, token, "POST", { decision: "reject" }))}>Reject edit</Button>
        </>}</li>)}</ul>
      </div>}
      {data.plans?.versions?.some((v: AnyRow) => v.version > 1) && <p role="note">Native plan versions are proposed for review. Version approval and running that content are not yet connected; the existing approval flow applies to the legacy plan.</p>}
    </section>

    <section style={box} aria-labelledby="execution-heading">
      <h3 id="execution-heading">Subtasks and execution</h3>
      <form onSubmit={(e) => { e.preventDefault(); void act("Subtask added", async () => { await send(`/runs/${pid}/subtasks`, token, "POST", { title: subtaskTitle }); setSubtaskTitle(""); }); }}>
        <label>New subtask<input value={subtaskTitle} onChange={(e) => setSubtaskTitle(e.target.value)} style={field} /></label><Button type="submit" disabled={busy || !subtaskTitle.trim()}>Add subtask</Button>
      </form>
      <ul>{listOf(data.subtasks, "items", "subtasks").map((s) => <li key={s.id}>{s.title} — {s.state} {s.state !== "done" && <Button disabled={busy} onClick={() => void act("Subtask marked complete", () => send(`/runs/${pid}/subtasks/${safe(s.id)}/complete`, token, "POST", { expectedVersion: s.version }))}>Complete</Button>}</li>)}</ul>
      <p>Run state: {data.status?.state ?? "unavailable"}. Safe resume: {data.status?.resume?.safe === true ? "available" : data.status?.resume?.safe === false ? "blocked" : "unknown"}.</p>
      <details><summary>Execution limits and usage</summary><pre>{JSON.stringify({ limits: data.limits?.limits, status: data.status }, null, 2)}</pre></details>
      <label>Completion report<textarea value={completion} onChange={(e) => setCompletion(e.target.value)} style={field} rows={2} /></label>
      <Button disabled={busy || !completion.trim()} onClick={() => void act("Completion reported for review", () => send(`/runs/${pid}/completion/report`, token, "POST", { summary: completion, outputs: [] }))}>Report completion</Button>
      <details><summary>Completion review</summary><pre>{JSON.stringify(data.completion, null, 2)}</pre>
        {data.completion?.review?.canAccept && <Button disabled={busy} onClick={() => void act("Completion accepted", () => send(`/runs/${pid}/completion/accept`, token, "POST", { reportDigest: data.completion.agentReported.digest }))}>Accept completion</Button>}
      </details>
      <Button variant="danger" disabled={busy} onClick={() => { const reason = window.prompt("Reason for abandoning this run?"); if (reason) void act("Run abandoned", () => send(`/runs/${pid}/abandon`, token, "POST", { reason })); }}>Abandon run</Button>{" "}
      <Button disabled={busy} onClick={() => void act("Retry requested", () => send(`/runs/${pid}/retry`, token, "POST", { requestId: crypto.randomUUID(), reason: "Requested from run workspace" }))}>Retry run</Button>
    </section>

    <section style={box} aria-labelledby="artifacts-heading">
      <h3 id="artifacts-heading">Artifacts and changes</h3>
      <label>Upload an artifact<input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={field} /></label>
      <Button disabled={busy || !file} onClick={() => void act("Artifact uploaded", async () => {
        const f = file!; const url = `${base}/runs/${pid}/artifacts?name=${safe(f.name)}`;
        const res = await fetch(url, { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": f.type || "application/octet-stream" }, body: f });
        if (!res.ok) throw new Error(`Upload failed (${res.status})`); setFile(null);
      })}>Upload</Button>
      <ul>{listOf(data.artifacts, "items").map((a) => <li key={a.id}>{a.name} · {a.sizeBytes} bytes · {a.state}{" "}
        <Button onClick={() => void act("Artifact download started", () => download(`/runs/${pid}/artifacts/${safe(a.id)}/content`, a.name))}>Download</Button>{" "}
        <Button onClick={() => void act("Artifact preview loaded", async () => { const p = await call(`/runs/${pid}/artifacts/${safe(a.id)}/preview`, token); setData((d) => ({ ...d, preview: p })); })}>Preview</Button>
      </li>)}</ul>
      {data.preview && <pre aria-label="Safe artifact preview" tabIndex={0} style={{ maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{typeof data.preview === "string" ? data.preview : JSON.stringify(data.preview, null, 2)}</pre>}
      <h4>Change review</h4><ul>{listOf(data.changes, "items", "changes").map((c) => <li key={c.id}>{c.path} · {c.reviewState}
        {c.reviewState === "pending" && <>
          <Button disabled={busy} onClick={() => void act("Change approved", () => send(`/runs/${pid}/changes/${safe(c.id)}/review`, token, "POST", { decision: "approved", diffSha: c.diffSha }))}>Approve change</Button>{" "}
          <Button disabled={busy} onClick={() => void act("Change rejected", () => send(`/runs/${pid}/changes/${safe(c.id)}/review`, token, "POST", { decision: "rejected", diffSha: c.diffSha }))}>Reject change</Button>
        </>}
      </li>)}</ul>
      <details><summary>Git state, permissions, and lineage</summary><pre>{JSON.stringify({ git: data.git, permissions: data.permissions, lineage: data.lineage }, null, 2)}</pre></details>
    </section>

    <section style={box} aria-labelledby="search-heading">
      <h3 id="search-heading">Search and export</h3><p>Search results are rechecked against current access. Index status: {data.searchStatus?.consistency ?? "load status"}.</p>
      <form onSubmit={search}><label>Search terms<input value={query} onChange={(e) => setQuery(e.target.value)} style={field} /></label>
        <label>Kind<select value={searchFilter.kind} onChange={(e) => setSearchFilter({ ...searchFilter, kind: e.target.value })} style={field}><option value="">All kinds</option>{["run", "session", "comment", "event", "artifact", "approval"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>Project ID<input value={searchFilter.projectId} onChange={(e) => setSearchFilter({ ...searchFilter, projectId: e.target.value })} style={field} /></label>
        <label>Participant ID<input value={searchFilter.participantId} onChange={(e) => setSearchFilter({ ...searchFilter, participantId: e.target.value })} style={field} /></label>
        <label>Status<input value={searchFilter.status} onChange={(e) => setSearchFilter({ ...searchFilter, status: e.target.value })} style={field} /></label>
        <label>Approval state<input value={searchFilter.approvalState} onChange={(e) => setSearchFilter({ ...searchFilter, approvalState: e.target.value })} style={field} /></label>
        <label>From<input type="date" value={searchFilter.from} onChange={(e) => setSearchFilter({ ...searchFilter, from: e.target.value })} style={field} /></label>
        <label>To<input type="date" value={searchFilter.to} onChange={(e) => setSearchFilter({ ...searchFilter, to: e.target.value })} style={field} /></label>
        <Button type="submit" disabled={busy}>Search</Button>
      </form>
      <ul>{listOf(data.hits, "items", "hits").map((h) => <li key={`${h.kind}-${h.id}`}>{h.title} · {h.kind} · {h.snippet}</li>)}</ul>
      <form onSubmit={(e) => { e.preventDefault(); void act("Export requested", async () => { setLatestExport(await send("/exports", token, "POST", { kind: exportKind, scopeId: exportScope, format })); }); }}>
        <label>Export type<select value={exportKind} onChange={(e) => setExportKind(e.target.value)} style={field}><option value="run_history">Run history</option><option value="session_history">Session history</option><option value="audit_evidence">Audit evidence</option></select></label>
        <label>Run or session ID<input value={exportScope} onChange={(e) => setExportScope(e.target.value)} style={field} /></label>
        <label>Format<select value={format} onChange={(e) => setFormat(e.target.value)} style={field}><option>json</option><option>ndjson</option></select></label><Button type="submit" disabled={busy}>Create export</Button>
      </form>
      {latestExport && <pre>{JSON.stringify(latestExport, null, 2)}</pre>}
      <ul>{listOf(data.exports, "items", "exports").map((x) => <li key={x.id}>{x.kind} · {x.state} {x.state === "completed" && <Button onClick={() => void act("Export download started", () => download(`/exports/${safe(x.id)}/download`, `${x.id}.${x.format}`))}>Download</Button>}</li>)}</ul>
      <form onSubmit={(e) => { e.preventDefault(); void act("Deletion request submitted", () => send("/deletion-requests", token, "POST", { reason: deletionReason, requestId: crypto.randomUUID() })); }}>
        <label>Request deletion of your data<textarea value={deletionReason} onChange={(e) => setDeletionReason(e.target.value)} style={field} rows={2} /></label><Button type="submit" disabled={busy}>Request deletion</Button>
      </form>
      <ul>{listOf(data.deletions, "items", "requests").map((x) => <li key={x.id}>Deletion request · {x.state} · {x.reason}</li>)}</ul>
    </section>
    <Button disabled={busy} onClick={() => void reload()}>Refresh work history</Button>
    </section>
  </>;
}

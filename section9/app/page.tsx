import AppShell from "@/components/AppShell";
import { mockEvents, mockPlan } from "@/lib/mockApi";

export default function CommandCenter() {
  const events = mockEvents();
  const last = events[events.length - 1];
  return (
    <AppShell active="/">
      <h1>Command Center — demo-repo</h1>
      <div className="grid">
        <div className="card">
          <h2>Agent Status</h2>
          <div className="status-line">
            <span>run-001</span>
            <span className="pill yellow">paused ⇄ executing</span>
          </div>
          <div className="status-line">
            <span>branch</span>
            <span>agent/demo</span>
          </div>
          <div className="status-line">
            <span>cost (run)</span>
            <span>${mockPlan.estimatedCostUsd.toFixed(3)}</span>
          </div>
          <div className="status-line">
            <span>last event</span>
            <span>{last.type}</span>
          </div>
        </div>

        <div className="card">
          <h2>Current Plan (approved)</h2>
          <ul>
            {mockPlan.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>

        <div className="card event-feed">
          <h2>Activity Stream</h2>
          {[...events].reverse().map((e) => (
            <div className="event-row" key={e.seq}>
              <span className="event-ts">{new Date(e.ts).toLocaleTimeString()}</span>
              <span className="event-type">{e.type}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Guardrails</h2>
          <div className="status-line">
            <span>auto-merge</span>
            <span className="pill red">never</span>
          </div>
          <div className="status-line">
            <span>plan gate</span>
            <span className="pill green">enforced</span>
          </div>
          <div className="status-line">
            <span>done-signal review</span>
            <span className="pill green">armed</span>
          </div>
          <div className="status-line">
            <span>forbidden paths</span>
            <span>.env*, infra/</span>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

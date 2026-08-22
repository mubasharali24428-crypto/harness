import type { AgentPlan } from "@contracts/plan";
import { panelStyle, headingStyle } from "@/lib/styles";

export function PlanPanel({ plan }: { plan: AgentPlan | null }) {
  if (!plan) {
    return (
      <section style={panelStyle}>
        <h2 style={headingStyle}>Plan</h2>
        <p style={{ color: "#6b7280", fontSize: 13 }}>No plan proposed yet for this run.</p>
      </section>
    );
  }

  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Plan</h2>
      <p style={{ marginTop: 0 }}>{plan.goal}</p>

      <PlanList title="Steps" items={plan.steps} ordered />
      <PlanList title="Files likely to change" items={plan.filesLikelyToChange} mono />
      <PlanList title="Tests" items={plan.tests} mono />
      <PlanList title="Risks" items={plan.risks} />
      <PlanList title="Forbidden paths" items={plan.forbiddenPaths} mono />

      <p style={{ fontSize: 13, color: "#9aa4b2" }}>
        <strong>Expected result:</strong> {plan.expectedResult}
      </p>

      <div style={{ display: "flex", gap: 24, marginTop: 12, fontSize: 13 }}>
        <span>Estimated cost: ${plan.estimatedCostUsd.toFixed(4)}</span>
        <span
          style={{ color: "#6b7280" }}
          title="No cost_ledger read endpoint exists yet - Section 3 isn't even a declared dependency of Section 7. Wired for it, no data source yet."
        >
          Actual cost: — (no live source yet)
        </span>
      </div>
    </section>
  );
}

function PlanList({
  title,
  items,
  ordered,
  mono,
}: {
  title: string;
  items: string[];
  ordered?: boolean;
  mono?: boolean;
}) {
  if (items.length === 0) return null;
  const Tag = ordered ? "ol" : "ul";
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 12, color: "#9aa4b2", marginBottom: 2 }}>{title}</div>
      <Tag
        style={{
          margin: 0,
          paddingLeft: 20,
          fontFamily: mono ? "ui-monospace, monospace" : undefined,
          fontSize: 13,
        }}
      >
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </Tag>
    </div>
  );
}

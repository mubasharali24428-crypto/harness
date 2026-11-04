// W18 — /pricing: what each tier actually enforces.
//
// The numbers come from lib/pricing.json, which a backend test compares to
// harness_contracts.tiers.tier_caps() and the constants the doors read. The page
// therefore separates what REFUSES (runs/day, context chunks) from what is only
// DECLARED (seat limits, retention target) instead of implying every row is a
// gate. Amounts stay "Contact us": pricing is a business decision under counsel
// review (PRICING.md), and inventing a number here would be the same dishonesty
// in a nicer font.

import * as React from "react";
import { cssVar, fontSize, radius, space } from "@/components/tokens";
import { CAP_FIELDS, formatCap, pricing } from "@/lib/pricing";

const page: React.CSSProperties = {
  minHeight: "100vh",
  background: `radial-gradient(1200px 600px at 50% -200px, rgba(var(--accent-rgb), 0.10), transparent), ${cssVar("bg")}`,
  color: cssVar("text"),
};

const shell: React.CSSProperties = {
  maxWidth: 1040,
  margin: "0 auto",
  padding: `${space.xl * 2}px ${space.xl * 2}px ${space.xl * 4}px`,
};

const h1: React.CSSProperties = { fontSize: 30, margin: 0 };
const lead: React.CSSProperties = { color: cssVar("muted"), marginTop: space.sm, lineHeight: 1.6 };
const sectionTitle: React.CSSProperties = {
  fontSize: 20,
  margin: `${space.xl * 2}px 0 ${space.md}px`,
};

const card: React.CSSProperties = {
  background: cssVar("panel"),
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.lg,
  padding: space.xl,
};

const grid: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
  gap: space.lg,
};

const table: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: fontSize.sm,
};

const th: React.CSSProperties = {
  textAlign: "left",
  padding: `${space.sm}px ${space.md}px`,
  borderBottom: `1px solid ${cssVar("border")}`,
  color: cssVar("muted"),
  fontWeight: 600,
};

const td: React.CSSProperties = {
  padding: `${space.sm}px ${space.md}px`,
  borderBottom: `1px solid ${cssVar("borderSoft")}`,
};

const badge = (enforced: boolean): React.CSSProperties => ({
  display: "inline-block",
  marginLeft: space.sm,
  padding: `0 ${space.sm}px`,
  borderRadius: radius.pill,
  border: `1px solid ${cssVar("border")}`,
  color: enforced ? cssVar("accent") : cssVar("muted"),
  fontSize: fontSize.xs,
});

const link: React.CSSProperties = {
  color: cssVar("accent"),
  textDecoration: "none",
};

export default function PricingPage() {
  return (
    <main style={page}>
      <div style={shell}>
        <h1 style={h1}>Pricing</h1>
        <p style={lead}>
          Self-hosted: every tier runs the same stack on your infrastructure. A tier buys rights
          and limits, never a different binary. Every number below is checked against the
          enforcement code by a test, so this page cannot promise a cap the product does not have.
        </p>

        <div style={{ ...grid, marginTop: space.xl }}>
          {pricing.tiers.map((tier) => (
            <section key={tier.id} style={card} aria-label={`${tier.name} plan`}>
              <h2 style={{ fontSize: 20, margin: 0 }}>{tier.name}</h2>
              <p style={{ color: cssVar("text2"), margin: `${space.sm}px 0 0` }}>{tier.price}</p>
              <p style={{ color: cssVar("muted"), fontSize: fontSize.xs, margin: 0 }}>
                {tier.priceNote}
              </p>
            </section>
          ))}
        </div>

        <h2 style={sectionTitle}>What varies by tier</h2>
        <div style={card}>
          <table style={table}>
            <thead>
              <tr>
                <th style={th}>Limit</th>
                {pricing.tiers.map((tier) => (
                  <th key={tier.id} style={th}>
                    {tier.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {CAP_FIELDS.map((field) => {
                const enforced = pricing.enforced.includes(field);
                return (
                  <tr key={field}>
                    <td style={td}>
                      {pricing.capLabels[field]}
                      <span style={badge(enforced)}>{enforced ? "enforced" : "declared"}</span>
                    </td>
                    {pricing.tiers.map((tier) => (
                      <td key={tier.id} style={td}>
                        {formatCap(pricing.caps[field][tier.id])}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p style={{ color: cssVar("muted"), fontSize: fontSize.xs, marginTop: space.md }}>
            An enforced cap is refused at the door: the 21st run of a day answers 402 with an
            upgrade prompt, and the index refuses to store past its ceiling. A declared limit has
            no door behind it yet and is listed because you would ask.
          </p>
        </div>

        <h2 style={sectionTitle}>Every deployment</h2>
        <div style={card}>
          <table style={table}>
            <tbody>
              <tr>
                <td style={td}>Concurrent runs per team</td>
                <td style={td}>{pricing.platform.runConcurrencyPerTeam}</td>
              </tr>
              <tr>
                <td style={td}>Engine pool (whole deployment)</td>
                <td style={td}>{pricing.platform.enginePoolMax}</td>
              </tr>
              <tr>
                <td style={td}>Workspaces per team</td>
                <td style={td}>{pricing.platform.workspacesPerTeam}</td>
              </tr>
              <tr>
                <td style={td}>SIEM sinks per team</td>
                <td style={td}>{pricing.platform.siemSinksPerTeam}</td>
              </tr>
              <tr>
                <td style={td}>SCIM tokens per team</td>
                <td style={td}>{pricing.platform.scimTokensPerTeam}</td>
              </tr>
            </tbody>
          </table>
          <p style={{ color: cssVar("muted"), fontSize: fontSize.xs, marginTop: space.md }}>
            Concurrency is one deploy-wide setting, not a tier lever; raise it with the hardware.
          </p>
        </div>

        <h2 style={sectionTitle}>Team tier only</h2>
        <div style={card}>
          <table style={table}>
            <tbody>
              {pricing.teamOnly.map((row) => (
                <tr key={row.surface}>
                  <td style={td}>{row.surface}</td>
                  <td style={td}>{row.limit}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ color: cssVar("muted"), fontSize: fontSize.xs, marginTop: space.md }}>
            These are refused with 403 PLAN_REQUIRED on any other plan — real gates, not marketing.
          </p>
        </div>

        <p style={{ ...lead, marginTop: space.xl }}>
          Start with the <a style={link} href="/docs">quickstart</a>, or go straight to{" "}
          <a style={link} href="/login">
            sign in
          </a>
          . Dollar amounts are under counsel review; nothing on this page is a quote.
        </p>
      </div>
    </main>
  );
}

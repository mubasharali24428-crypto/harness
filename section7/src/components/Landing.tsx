"use client";

// Public landing page for signed-out visitors. The app itself is unchanged:
// a session sends people straight to the Command Center; no session gets the
// story, the guardrails, and two honest entry points (sign in, or watch a
// recorded run).
//
// Honesty rules carried from the demo lane: the replay is labelled a replay,
// the product is labelled a private pilot, and nothing promises autonomy the
// server does not enforce (auto-merge is "never", approval is required).

import * as React from "react";
import { cssVar, fontSize, radius, space } from "./tokens";

const pageStyle: React.CSSProperties = {
  minHeight: "100vh",
  background: `radial-gradient(1200px 600px at 50% -200px, rgba(88,166,255,0.12), transparent), ${cssVar("bg")}`,
  color: cssVar("text"),
};

const shell: React.CSSProperties = {
  maxWidth: 1040,
  margin: "0 auto",
  padding: `0 ${space.xl * 2}px`,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: space.lg,
  padding: `${space.xl * 2}px 0`,
  flexWrap: "wrap",
};

const brandStyle: React.CSSProperties = {
  fontWeight: 700,
  letterSpacing: 2,
  fontSize: fontSize.md,
};

const navLink: React.CSSProperties = {
  color: cssVar("text"),
  textDecoration: "none",
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.md,
  padding: `${space.sm}px ${space.lg}px`,
  fontSize: fontSize.sm,
  background: cssVar("panel"),
};

const primaryCta: React.CSSProperties = {
  ...navLink,
  background: cssVar("accent"),
  borderColor: cssVar("accent"),
  color: cssVar("onAccent"),
  fontWeight: 600,
};

const eyebrow: React.CSSProperties = {
  display: "inline-block",
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.pill,
  padding: `${space.xs}px ${space.lg}px`,
  color: cssVar("muted"),
  fontSize: fontSize.xs,
  letterSpacing: 1,
  textTransform: "uppercase",
};

const h1Style: React.CSSProperties = {
  fontSize: "clamp(34px, 6vw, 56px)",
  lineHeight: 1.08,
  margin: `${space.xl * 2}px 0 ${space.xl}px`,
  letterSpacing: -1,
};

const leadStyle: React.CSSProperties = {
  color: cssVar("muted"),
  fontSize: 17,
  lineHeight: 1.6,
  maxWidth: 680,
  margin: 0,
};

const cardStyle: React.CSSProperties = {
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.lg,
  background: cssVar("panel"),
  padding: space.xl * 2,
};

const cardTitle: React.CSSProperties = {
  margin: `0 0 ${space.md}px`,
  fontSize: fontSize.md,
  color: cssVar("text"),
};

const cardBody: React.CSSProperties = {
  margin: 0,
  color: cssVar("muted"),
  fontSize: fontSize.sm,
  lineHeight: 1.6,
};

const FEATURES: Array<{ title: string; body: string }> = [
  {
    title: "A plan gate",
    body: "Nothing executes until a person approves the plan. Rejections, edits and guidance are durable events, not chat scrollback.",
  },
  {
    title: "Confined execution",
    body: "Seatbelt or Landlock, an egress allow-list and a per-run workspace. Forbidden paths are enforced by the shell gate, never by prompt wording.",
  },
  {
    title: "Human acceptance",
    body: "The agent reports completion; a person accepts it. The server refuses self-acceptance, so “done” always has a name behind it.",
  },
  {
    title: "Metered spend",
    body: "Every model call lands in a cost ledger, and a run pauses at its budget limit instead of quietly overspending.",
  },
];

const GUARDRAILS = ["auto-merge: never", "plan approval: required", "sandbox: allow-list + OS confinement", "provider keys: yours"];

export function LandingView() {
  return (
    <div style={pageStyle} data-testid="landing">
      <div style={shell}>
        <header style={headerStyle}>
          <span style={brandStyle}>HARNESS</span>
          <nav aria-label="Get started" style={{ display: "flex", gap: space.md, flexWrap: "wrap" }}>
            <a href="/login" style={navLink}>
              Sign in
            </a>
            <a href="/demo" style={primaryCta}>
              Watch a recorded run
            </a>
          </nav>
        </header>

        <main>
          <section aria-labelledby="hero-title" style={{ paddingBottom: space.xl * 3 }}>
            <span style={eyebrow}>Private design-partner build</span>
            <h1 id="hero-title" style={h1Style}>
              Ship AI-written code.
              <br />
              Keep the veto.
            </h1>
            <p style={leadStyle}>
              Harness puts a plan gate, a confined sandbox and a human acceptance
              step between your agent and your repository. Every run is planned,
              metered, checkpointed and reversible.
            </p>
            <div
              style={{
                display: "flex",
                gap: space.md,
                flexWrap: "wrap",
                marginTop: space.xl * 2,
              }}
            >
              <a href="/login" style={{ ...primaryCta, padding: `${space.md}px ${space.xl * 2}px` }}>
                Enter the harness
              </a>
              <a href="/demo" style={{ ...navLink, padding: `${space.md}px ${space.xl * 2}px` }}>
                Watch a recorded run
              </a>
            </div>
            <p style={{ color: cssVar("muted"), fontSize: fontSize.xs, marginTop: space.lg }}>
              The demo is a clearly labelled replay of a real session — no provider
              keys, no live execution.
            </p>
          </section>

          <section aria-labelledby="how-title" style={{ paddingBottom: space.xl * 3 }}>
            <h2 id="how-title" style={{ fontSize: fontSize.md, letterSpacing: 1, textTransform: "uppercase", color: cssVar("muted") }}>
              What sits between the model and main
            </h2>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
                gap: space.lg,
                marginTop: space.lg,
              }}
            >
              {FEATURES.map((f) => (
                <article key={f.title} style={cardStyle}>
                  <h3 style={cardTitle}>{f.title}</h3>
                  <p style={cardBody}>{f.body}</p>
                </article>
              ))}
            </div>
          </section>

          <section
            aria-label="Guardrails"
            style={{
              border: `1px solid ${cssVar("border")}`,
              borderRadius: radius.lg,
              padding: space.xl * 2,
              marginBottom: space.xl * 3,
              display: "flex",
              gap: space.lg,
              flexWrap: "wrap",
              justifyContent: "space-between",
              background: cssVar("panel2"),
            }}
          >
            {GUARDRAILS.map((g) => (
              <code key={g} style={{ fontSize: fontSize.sm, color: cssVar("muted") }}>
                {g}
              </code>
            ))}
          </section>

          <section aria-labelledby="closing-title" style={{ paddingBottom: space.xl * 4, textAlign: "center" }}>
            <h2 id="closing-title" style={{ fontSize: 26, marginBottom: space.md }}>
              See it with real output
            </h2>
            <p style={{ ...leadStyle, margin: "0 auto", textAlign: "center" }}>
              The replay walks a completed run end to end: plan, execution,
              review and cost. Two clicks, no signup key required.
            </p>
            <div style={{ marginTop: space.xl }}>
              <a href="/demo" style={{ ...primaryCta, padding: `${space.md}px ${space.xl * 2}px` }}>
                Watch the recorded run
              </a>
            </div>
          </section>
        </main>

        <footer
          style={{
            borderTop: `1px solid ${cssVar("border")}`,
            padding: `${space.xl * 2}px 0`,
            color: cssVar("muted"),
            fontSize: fontSize.xs,
            display: "flex",
            justifyContent: "space-between",
            gap: space.md,
            flexWrap: "wrap",
          }}
        >
          <span>Harness — private pilot. Terms and privacy are under counsel review.</span>
          <span>
            <a href="/login" style={{ color: cssVar("muted") }}>
              Sign in
            </a>
          </span>
        </footer>
      </div>
    </div>
  );
}

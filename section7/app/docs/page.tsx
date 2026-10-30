// W18 — /docs: the entry point a stranger needs, in the product.
//
// Deliberately short and executable: the four commands below are the ones
// docs/quickstart.md was run with against the compose stack, and the same steps
// are asserted by `make e2e-compose`. Anything longer belongs in the Markdown
// documents the bottom of the page links to, so this page does not become a
// second, drifting copy.

import * as React from "react";
import { cssVar, fontSize, radius, space } from "@/components/tokens";

const page: React.CSSProperties = {
  minHeight: "100vh",
  background: `radial-gradient(1200px 600px at 50% -200px, rgba(var(--accent-rgb), 0.10), transparent), ${cssVar("bg")}`,
  color: cssVar("text"),
};

const shell: React.CSSProperties = {
  maxWidth: 900,
  margin: "0 auto",
  padding: `${space.xl * 2}px ${space.xl * 2}px ${space.xl * 4}px`,
};

const h1: React.CSSProperties = { fontSize: 30, margin: 0 };
const lead: React.CSSProperties = { color: cssVar("muted"), marginTop: space.sm, lineHeight: 1.6 };
const stepTitle: React.CSSProperties = { fontSize: 20, margin: `${space.xl}px 0 ${space.sm}px` };
const body: React.CSSProperties = { color: cssVar("text2"), lineHeight: 1.7, margin: 0 };

const code: React.CSSProperties = {
  background: cssVar("panel"),
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.md,
  padding: space.lg,
  overflowX: "auto",
  fontSize: fontSize.sm,
  color: cssVar("text"),
  margin: `${space.sm}px 0 0`,
};

const link: React.CSSProperties = { color: cssVar("accent"), textDecoration: "none" };

const cards: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: space.lg,
  marginTop: space.lg,
};

const card: React.CSSProperties = {
  background: cssVar("panel"),
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.lg,
  padding: space.lg,
};

function Step(props: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 style={stepTitle}>
        {props.n}. {props.title}
      </h2>
      {props.children}
    </section>
  );
}

export default function DocsPage() {
  return (
    <main style={page}>
      <div style={shell}>
        <h1 style={h1}>Documentation</h1>
        <p style={lead}>
          From a checkout to a governed run you can read back: log in, seed a run, inspect its
          steps, see the events and the audit row. These are the same commands the stack&apos;s own
          journey test runs, not illustrations.
        </p>

        <Step n={1} title="Start the stack">
          <p style={body}>
            Docker Compose v2 and a master key. Identity waits for the database and the migrations,
            so <code>/ready</code> is the honest signal, not <code>/health</code>.
          </p>
          <pre style={code}>{`docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml run --rm migrate
curl -fsS http://127.0.0.1:8001/ready     # {"ok":true,"db":"ok","migrations":"ok"}`}</pre>
        </Step>

        <Step n={2} title="Log in (passwordless)">
          <p style={body}>
            Request a code, read it from the identity log in a local deployment (with SMTP it goes
            to your inbox), and verify. The first login creates your account and a solo team where
            you are the admin.
          </p>
          <pre style={code}>{`curl -sS -X POST http://127.0.0.1:8001/api/v1/auth/request-login \\
  -H 'content-type: application/json' -d '{"email":"me@example.com"}'
# the code is in: docker logs harness-identity-1 | grep "login code"
curl -sS -X POST http://127.0.0.1:8001/api/v1/auth/verify-otp \\
  -H 'content-type: application/json' -d '{"email":"me@example.com","code":"<code>"}'`}</pre>
        </Step>

        <Step n={3} title="Seed a run and read it">
          <p style={body}>
            The seed is a <strong>recorded replay</strong>, and the API says so: the run&apos;s engine
            resource reports <code>replayed</code>, never a live claim. Re-seeding is idempotent.
          </p>
          <pre style={code}>{`curl -sS -X POST http://127.0.0.1:8004/api/v1/orchestrator/demo/seed \\
  -H "authorization: Bearer $TOKEN" -o seed.json
curl -sS "http://127.0.0.1:8004/api/v1/orchestrator/runs/$RUN" \\
  -H "authorization: Bearer $TOKEN" | python3 -m json.tool`}</pre>
        </Step>

        <Step n={4} title="Do it for real on your repo">
          <p style={body}>
            Import the conventions you already have (<code>CLAUDE.md</code>,{" "}
            <code>AGENTS.md</code>, Cursor rules) into the project&apos;s rules, then open a run. A
            human approves the plan before the agent touches anything, and a human merges — there is
            no auto-merge on any tier.
          </p>
          <pre style={code}>{`python3 scripts/migrate_agent_config.py --project-id <id>          # plan only
HARNESS_TOKEN=<token> python3 scripts/migrate_agent_config.py --project-id <id> --apply`}</pre>
        </Step>

        <h2 style={{ ...stepTitle, marginTop: space.xl * 2 }}>Where to go next</h2>
        <div style={cards}>
          <div style={card}>
            <a style={link} href="/pricing">
              Pricing and limits
            </a>
            <p style={{ ...body, marginTop: space.xs }}>
              Every cap on that page is checked against the enforcement code, including which ones
              are only declared.
            </p>
          </div>
          <div style={card}>
            <a style={link} href="/demo">
              The recorded walkthrough
            </a>
            <p style={{ ...body, marginTop: space.xs }}>
              A labelled replay of a full run: plan, approval, diff, review. Nothing pretends to be
              live.
            </p>
          </div>
          <div style={card}>
            <span style={{ color: cssVar("text") }}>Sample repositories</span>
            <p style={{ ...body, marginTop: space.xs }}>
              <code>examples/fix-a-failing-test</code> (it fails on purpose) and{" "}
              <code>examples/review-a-pr</code> are the shortest path through the loop.
            </p>
          </div>
        </div>

        <p style={{ ...lead, marginTop: space.xl }}>
          The full documents live in the repository under <code>docs/</code> — start with{" "}
          <code>docs/quickstart.md</code>, <code>docs/migrating-from-claude-code-and-codex.md</code>{" "}
          and <code>docs/runbooks/ops.md</code>. If this page and <code>make e2e-compose</code> ever
          disagree, the script is right.
        </p>
      </div>
    </main>
  );
}

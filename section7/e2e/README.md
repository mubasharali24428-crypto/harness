# section7/e2e — Playwright hero-journey suite (M13-H3a, H-014)

End-to-end coverage of the product's core flow, in order:

```
login → project → meeting → message → brief → approve brief →
propose plan → approve plan → start run → run detail → runs index → review
```

Assertion law: observable UI truth only (visible copy, headings, roles,
stable data-testids) — never implementation details.

## Files

| Path | Purpose |
|---|---|
| `../playwright.config.ts` | Config: `baseURL` (env `E2E_BASE_URL`, default `http://localhost:3000`), retries=1, trace on-first-retry, single worker (the journey is stateful), global-setup guard. Specs match `*.e2e.ts` so vitest's default glob never picks them up. |
| `global-setup.ts` | Probes the stack before any browser work; skips everything with exit code **246** when it's down, so CI jobs without compose stay green. |
| `support.ts` | Login helpers covering both real auth paths (below). |
| `journey.e2e.ts` | The journey specs. |

## Stack-guard topology

The Next frontend does **not** proxy `/api/v1` — `src/lib/api.ts` calls the
backend services directly on their host ports (CORS allow-lists :3000).
The guard therefore checks:

1. the frontend origin answers (any HTTP response),
2. the API answers `/health` or `/api/v1/health` on
   `E2E_API_URL` (default `http://localhost:8001`, the identity service the
   journey logs in through).

Exit code **246** = "skipped: stack down" (distinct from failure = 1).

## Running locally

```sh
docker compose -f deploy/docker-compose.yml up -d --wait
cd section7 && npx playwright install chromium   # once
E2E_IDENTITY_URL=http://localhost:8001 npm run e2e
```

Environment knobs:

| Variable | Meaning | Default |
|---|---|---|
| `E2E_BASE_URL` | Frontend origin under test | `http://localhost:3000` |
| `E2E_API_URL` / `E2E_IDENTITY_URL` | Identity base for health probe + OTP capture | `http://localhost:8001` |
| `E2E_IDENTITY_CONTAINER` | Container whose logs carry dev-notifier OTP lines | `harness-identity-1` |
| `E2E_OTP_CODE` | Supply a known code instead of docker-log capture | — |
| `E2E_ORCHESTRATOR_URL` | Orchestrator base for the gate leg | `http://localhost:8004` |
| `E2E_SKIP_STACK_CHECK` | `1` bypasses the health guard | unset |

## Auth strategies (both are real app paths)

1. **Dev bypass** — stacks running `HARNESS_REQUIRE_OWNERSHIP=false`:
   `POST /api/v1/auth/login {email}` → `{token, principal}` (same path
   `deploy/seed_demo.py` drives). The suite drives the actual `/login` form.
2. **OTP via dev notifier** — with ownership enforced the form flips itself to
   code entry; the suite requests the code via `POST /auth/request-login` and
   reads it from the identity container log
   (`HARNESS-OTP login code for <email>: <code>` — the notifier's dev printer;
   no SMTP exists on dev stacks).

The suite never fabricates session state; tokens land in localStorage via the
app's own `saveSession()` flow. Note: `login_or_register` seeds every fresh
team with a `demo-repo` project, so the create-project card only appears for
teams with zero projects — the spec handles both branches.

## Vintage-aware legs (why some steps branch)

Compose images are baked at build time, so a running stack can lag HEAD.
The suite detects capabilities instead of failing on drift:

* **Plan gate:** if the orchestrator serves `/api/v1/orchestrator/plans`
  (`planGateDeployed` probe) the leg drives the W1-C1↔W1-C2 pair contract
  exactly as `app/dashboard/planGateApi.ts` does — server-minted run id.
  On pre-plan-gate images it falls back to the SAME lifecycle via the per-run
  routes `src/lib/api.ts` has always consumed
  (`POST /runs/{id}/plan` → `/plan/approve` → `/start`). Server logs are the
  proof either way (`docker logs harness-orchestrator-1` shows the 201/200/200).
  When a page finally mounts `<PlanGateCard>`, set `GATE_VIA_UI = true` and the
  already-written `[data-testid=plan-gate]` branch takes over.
* **Run detail states:** dry-mode engines settle at `awaiting_done_signal`
  right after start; live engines report `executing`. StatusPanel renders the
  state verbatim (underscores → spaces); both are accepted.
* **Runs index:** gate-minted runs bind no projectId, and the index is
  team-scoped by project ownership — so a fresh gate run may legitimately be
  absent. The spec asserts only what the page guarantees (paged shape).
* **Review empty state:** pre-M12-E4b stacks render "No pull request selected"
  (+ pill "no PR loaded"); HEAD renders PrEmptyState's "No pull request yet…"
  (+ pill "no PR yet"). Both honest variants are accepted.

## Intended CI recipe (H3b owns `.github/workflows/ci.yml` — NOT wired here)

```yaml
e2e:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with: { node-version: 22 }
    - name: Start compose stack
      run: docker compose -f deploy/docker-compose.yml up -d --build --wait
    - name: Install deps + browsers
      working-directory: section7
      run: |
        npm ci
        npx playwright install --with-deps chromium
    - name: Run hero-journey E2E
      working-directory: section7
      env:
        E2E_IDENTITY_URL: http://localhost:8001
      run: npm run e2e
    - uses: actions/upload-artifact@v4
      if: failure()
      with:
        name: playwright-report
        path: section7/playwright-report/
```

Pair-contract note (H-P2): H3b's `make e2e` should converge to exactly this —
compose up → `npm run e2e` in `section7/`. The global-setup guard keeps the
target honest where no stack exists (clear skip message, exit 246).
H3b may also want `test-results/` + `playwright-report/` gitignored under
`section7/` (transient Playwright artifacts).

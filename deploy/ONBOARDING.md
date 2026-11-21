# First-Run Security Checklist — read before connecting a private repository

**Pilot-grade software, not a production-security certification.** Read the current
`docs/legal/SECURITY_QUESTIONNAIRE.md`, `docs/pilot-remediation/A01-DISPOSITIONS-2026-11-11.md`
and the Week 3/4 evidence before accepting customer data. The host/boundary policy still
requires owner, security and counsel approval; this checklist does not grant that approval.

- [ ] **Execution boundary**: identify the actual backend and host. The live orchestrator
      uses a per-run container through the worker profile; standalone shell gates use
      Seatbelt/Landlock. Do not assume these paths provide identical guarantees.
- [ ] **Known limits**: within-workspace read scope is not enforced by container mounts;
      Landlock has no network boundary; the worker daemon holds the host Docker socket.
      Review the open findings and approved mitigations before a pilot.
- [ ] **Network egress**: verify the deployed network/proxy configuration and allow-list.
      Do not claim that every backend is network-confined or that every remote is pinned.
- [ ] **Secrets**: never put production secrets in a pilot workspace, model context,
      screenshots or logs. Verify the selected provider's data handling and retention terms.
- [ ] **Forbidden paths**: inspect the approved plan and deployed policy. Policy checks
      are not a substitute for filesystem isolation or independent negative tests.
- [ ] **Merges are manual**. Humans review and merge; no auto-merge.
- [ ] **Plan gate**: no execution before human approval. The displayed plan may be a
      template; do not describe it as model-generated unless current evidence establishes that.
- [ ] **Evidence**: retain actual approvals, events, diffs, test output, costs and failures.
      A completed engine call or reviewer summary alone is not proof of correct delivery.

# Onboarding order

> **Before step 1:** configure the stack from the repo root:
>
> ```sh
> cp .env.example .env   # then edit .env — see "GitHub OAuth" below
> ```
>
> `.env.example` documents every variable the stack reads (name, purpose,
> default, required-or-optional). At minimum set `HARNESS_MASTER_KEY`
> (`openssl rand -hex 32`) before a real boot; see that file for details.
> `.env*` is gitignored — your secrets stay local.

1. Follow `docs/TUTORIAL.md` for the current boot, authentication and plan-gate contract.
2. Open the configured frontend URL (default http://localhost:3000).
3. Prove email ownership; create a real project. Use synthetic/non-production code first.
4. Connect GitHub only if needed and approved — operator OAuth setup is below.
5. Propose a plan, inspect it, and have an authorized human approve or reject it.
6. Start the approved run with the reviewed worker configuration; observe actual events.
7. Inspect real changes and test output, resolve staged approvals, then record acceptance.
8. Merge manually only after review. Invitations, meetings and PR creation are optional
   surfaces; do not treat them as prerequisites for the first governed task.

If login is blocked with an ownership error, the stack is running with
`HARNESS_REQUIRE_OWNERSHIP=true`: use **Request login code** (email OTP)
instead of the plain dev login — the code is emailed when SMTP is configured
(`HARNESS_SMTP_*` in `.env.example`), or printed to the identity container's
logs in dev:

```sh
docker compose -f deploy/docker-compose.yml logs identity | grep HARNESS-OTP
```

## GitHub OAuth (step 3) — requires operator-provided credentials

The compose file ships `GITHUB_CLIENT_ID=""` / `GITHUB_CLIENT_SECRET=""` on
purpose: **there is no built-in OAuth app**. Every operator must register their
own before this step can work — until then GitHub connect returns a clean
`503 NOT_CONFIGURED`, which is expected, not a bug.

1. Go to <https://github.com/settings/developers> → **New OAuth App**.
2. Homepage URL: where the frontend runs (e.g. `http://localhost:3000`).
3. Authorization callback URL — must match `HARNESS_GITHUB_REDIRECT_URI`
   exactly:
   `http://localhost:8002/api/v1/github/oauth/callback`
   (replace host/port for non-local deployments; keep the path).
4. Copy the client ID + generate a client secret into `.env`:

   ```sh
   GITHUB_CLIENT_ID=...        # from the OAuth app page ("Client ID")
   GITHUB_CLIENT_SECRET=...    # "Generate a new client secret"
   ```

5. Restart the github service: `docker compose -f deploy/docker-compose.yml up -d github`
6. Scopes requested during connect: `repo` + `read:user`.

No GitHub app yet? Skip step 3 entirely — every other onboarding step works
without it.

Telemetry: error logs, usage analytics, and prompt/code *metadata* are recorded
locally by default. Full prompt text and full code content are collected only
after explicit opt-in through the telemetry settings API (server-side
enforcement; see `section10/src/harness_onboarding/telemetry.py`).

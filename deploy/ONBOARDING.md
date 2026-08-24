# First-Run Security Checklist — read before connecting a private repository

- [ ] **Sandbox scope**: agents run inside ephemeral containers. Commands are
      allow-listed (tests, linters, builds, lockfile installs). Deletes, force-push,
      secret reads, package publishes, and infra tools are blocked in code.
- [ ] **Network egress**: sandbox containers may reach only pinned registries
      (pypi.org, npmjs.com) and your repo's own git remote. Nothing else.
- [ ] **Secrets**: your API keys are encrypted at rest and decrypted only inside the
      model-router service. Secrets never enter model context or logs. Never paste
      secrets into chat or commit them to the demo repo.
- [ ] **Forbidden paths**: plans carry `forbidden_paths` (default: `.env*`, `infra/`).
      Agents cannot touch those paths — enforced in code, not policy documents.
- [ ] **Merges are manual**. Always. This app never merges a PR; humans merge on
      GitHub after review. There is no auto-merge anywhere in the system.
- [ ] **Plan gate**: no code executes until a human approves the agent's plan.
      No exceptions, including "quick fixes".
- [ ] **Audit trail**: plan approvals, pauses, takeovers, review decisions, and
      merges are appended to an append-only audit log.

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

1. Open http://localhost:3000
2. Log in (email creates your solo team; you are admin)
3. Connect GitHub (Settings → OAuth) — **requires manual pre-setup**, see below
4. Create a project pointing at the seeded demo repo (`demo-repo`, seeded by the
   compose `migrate` one-shot / `deploy/seed_demo.py`) — or your own private repo
   once you've read this checklist
5. Invite teammates (admin → invite by email); add agent seats if wanted
6. Start a Meeting; type `@ai turn this into a brief`; review + APPROVE the brief
7. Follow the guided demo: brief → plan proposal → approve → sandboxed run on its
   own branch → pause/edit → done-signal → reviewer pass → draft PR → Review Area
8. Approve in Review Area, then merge manually on GitHub

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

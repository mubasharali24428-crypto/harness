# CI-minutes runbook — the 3,000-minute problem and its options (2026-11-21)

The GitHub Actions included minutes (3,000/month on Pro) are exhausted. This
document records why, what was already changed about it, and the remaining
options — including the ones that cost nothing and the ones that cost a
decision (P-D5).

## 1. Why the minutes evaporated (measured from ci.yml)

Per CI run before the diet: **16 jobs** (9 pytest matrix legs + static gates +
mcp + language + frontend + e2e compose journey + migrations on real PG +
security scan). Each leg paid its own setup tax (checkout + fresh venv + pip
install of contracts + section — nothing cached). Rough arithmetic from the
observed run times: a full run ≈ **55–70 minutes**; a heavy workday with 4–5
pushes + a PR update ≈ **300–400 minutes**. Three such days = the monthly
allowance. Two force multipliers made it worse:

- **docs-only pushes ran everything** — a markdown-only commit paid for the
  e2e compose journey (40-min timeout) and all nine sections;
- **superseded runs were never cancelled** — pushing twice while the first run
  was in flight paid for both.

## 2. What changed on 2026-11-21 (already implemented, no capability loss)

In `.github/workflows/ci.yml`:

1. **`changes` gate job** (dorny/paths-filter, SHA-pinned): each section, mcp,
   language, e2e, and the PG-migrations job now runs only when its own paths
   changed (always including `contracts/**` and `Makefile` — a contract change
   is every section's business). **Unconditional by design:** `static-gates`
   (cheap, law guards), `frontend` (a branch-protection required check — never
   skipped into ambiguity), `security-scan` (secrets posture runs on every
   tree). On `schedule`/`workflow_dispatch` every filter reports true: the
   nightly probe pays for the whole suite.
2. **`concurrency: cancel-in-progress`** — a superseded push cancels its
   stale run instead of paying for both.
3. **pip caching** in `setup-python` (per-section wheels) — cuts the biggest
   per-leg setup tax.

Measured effect (job counts, PR events):

| Scenario | Before | After |
|---|---|---|
| docs-only PR | 16 jobs ≈ 55–70 min | **4 jobs ≈ 10–15 min** |
| one-section change | 16 jobs | **5–6 jobs ≈ 15–25 min** |
| contracts-wide change | 16 jobs | 16 jobs (unchanged — correct) |
| master push | 16–17 | same as the equivalent change set |

Expected overall saving: **60–75%** of typical usage.

## 3. Billing — check these two things (owner, 2 minutes)

- `github.com/settings/billing` → **Actions minutes**: shows used/included
  and the **monthly reset date**. Unused minutes do not roll over.
- **Spending limit**: if it is $0, Actions for a private repo *stops* when the
  included allowance is gone; raising the limit bills at **$0.008/Linux-minute**
  (~$4 per full CI run at pre-diet size, ~$1 after the diet).

## 4. The free, unlimited option: a self-hosted runner (recommended next)

Self-hosted runners consume **zero minutes**, and this repo is **private**
(the dangerous case is only public repos, where a malicious PR could abuse a
self-hosted runner). This machine already runs the whole RC stack in Docker —
it can also run CI:

1. Repo → Settings → Actions → Runners → **New self-hosted runner** → macOS
   (or the Linux Docker VM, which is closer to CI's ubuntu environment).
2. Run the download/configure snippet it prints (one command each; labels
   like `macos-local` or `linux-local`).
3. Keep the runner process alive (a second launchd entry, mirroring how
   `deploy/backup/install-launchd.sh` manages nightly.sh).
4. Tag the workflow: `runs-on: [self-hosted, linux-local]` — per-job, so a
   hybrid is possible (self-hosted for heavy e2e, GitHub-hosted for the rest).

Trade-offs, stated plainly: the runner is online only when the machine is
awake; CI shares CPU with the local stack; and `make clean` hygiene matters
(no ephemeral runner). For a one-person project this is the highest-leverage
move after the diet.

## 5. `act` — verify before pushing (free, local)

[nektos/act](https://github.com/nektos/act) runs the SAME workflow YAML locally
in Docker: `act pull_request --job pytest --matrix section:section1`. Useful to
catch workflow-shape mistakes (like a bad `if:`) without spending a single
hosted minute. Caveat: compose-in-Docker needs the host Docker socket mounted
(`act --container-daemon-socket`), so the e2e journey is the one job that
still belongs on hosted/self-hosted runners.

## 6. Other platforms — the honest comparison

| Platform | Free allowance | Migration cost | Verdict |
|---|---|---|---|
| GitHub Actions (current) | 3,000 min/mo (Pro) | — | keep; diet + self-hosted runner |
| CircleCI | ~6,000 credits/mo | whole matrix rewrite; loses required-check wiring, gitleaks action | not worth it |
| GitLab CI | 400 compute-min/mo | full repo move | allowance is *worse* |
| Buildkite / Jenkins / Woodpecker | runner-based (free) | full rewrite + hosting | same class as a self-hosted runner, more work |

The workflow is deeply GitHub-native (required checks incl. `check-ts-dist`,
gitleaks, the compose journey, paths-filter). Migration would cost days and
lose the diet's benefits. **Do not migrate for minutes; run a self-hosted
runner instead.**

## 7. The P-D5 connection (W11 owner decision)

**Public repositories get unlimited GitHub Actions minutes free.** The plan
already schedules the open-source flip decision as P-D5 (W11, default: stays
private). If the flip happens for any reason, the minutes problem disappears
entirely (and self-hosted runners become a security liability on public repos
— the two options are mutually exclusive per repo state). Until then: private
+ diet + self-hosted.

## 8. Two notes recorded the same day

- The scheduled CI failure (run 44914915851) and PR #26's insta-failed jobs
  were **GitHub/Azure infrastructure**, not minutes and not code: "Cache
  service responded with 400", Azure "Our services aren't available right
  now", jobs dying in 2s with 0 steps. Recovery = rerun when the platform
  recovers (`gh run rerun 44918292744 --failed`).
- The **verdict bot** workflow (`.github/workflows/verdict-bot.yml`) is
  dispatch-only with a 10-minute timeout — negligible minutes by design; it
  stays cheap under any of these options.

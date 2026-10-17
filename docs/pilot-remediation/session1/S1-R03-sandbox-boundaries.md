# S1-R03 — Sandbox, filesystem, process, package-install, network boundaries

Status: IMPLEMENTING (design frozen below; verification appended when green).
Register: `RISK_REGISTER.md` §R-03. All findings were executed against the real
validator during recon (bypass commands confirmed passing).

## Findings (from register, recon-confirmed)

- R-03a shell-feature bypasses (exploitable today): double-quoted `$( )` /
  backticks skipped by the quote-aware scanner (they ARE expanded by `/bin/sh`
  inside double quotes); `find -exec` hides the real command; shell globs defeat
  forbidden-path checks (`db/*/sensitive/init.sql`); `${VAR}` braced form misses
  the sensitive-env prefix check.
- R-03b installers unconstrained: `pip install`, `python -m pip install`,
  `npm i`/`npm ci`, `npx --yes` pass (only *publish* is blocked);
  `pinned_registries` config is never consulted at runtime.
- R-03c network fully open: Seatbelt `(allow network*)`; Landlock ignores
  network (ABI v1); compose adds `extra_hosts: host.docker.internal` and no
  egress restriction.
- R-03d confinement fail-open: explicit `confinement="seatbelt"/"landlock"`
  requested but unavailable degrades to `"off"` with only a warning;
  `apply_landlock_in_child` returns False on failure and nobody checks it —
  the child keeps running unconfined.
- R-03e env leak into agent processes: `HARNESS_DATABASE_URL` (DB creds)
  survives `sanitize_environment` (compose sets it; prefix `HARNESS_` not
  covered).
- R-03f destroy/timeout hygiene: destroy SIGKILLs direct children only (no
  process group; `sleep &` grandchildren survive); streaming `exec` applies the
  300s budget only after stdout EOF — a hung/dripping command never times out.
- MITIGATED (kept): edit path traversal + symlink guard; container cpus/mem/pids
  caps; destructive/force-push/publish/infra command regexes with tests.

## Threat model
Attacker: the agent itself (or code it runs) inside the sandbox, assumed to be
steerable via prompt injection (see R-04). Assets: host/other-tenant files,
DB credentials, spend, network reach beyond the container, process table.
Controls claimed today that do NOT hold: single-command-per-exec, no command
substitution, forbidden-path enforcement, no unvetted package installs,
confined writes, bounded execution time, teardown kills everything.

## Fix design (smallest honest change per finding)

- R-03a (policy.py):
  1. Scanner: inside double quotes also reject backticks and `$(` (single
     quotes stay literal — matching real shell semantics).
  2. Sensitive-var check: regex `\$\{?(\w+)\}?` over every token, so braced,
     mid-token and unbraced forms all resolve to the variable name before the
     prefix test.
  3. `find` with `-exec/-execdir/-ok/-okdir` rejected (pattern can't hide a
     follower from first-token validation).
  4. Glob expansion before path checks: every token containing `*?[` is
     pre-expanded against the task workspace (validate_command gains an
     optional `cwd`); the absolute, workspace-relative and realpath forms of
     each match are run through the SAME secret-file and forbidden-path
     matchers. Additive-only: literals behave exactly as before.
- R-03b (policy.py): block install verbs (`pip install`, `python -m pip
  install`, `npm install|i|ci`, `yarn/pnpm add|install`, `npx -y/--yes`,
  `yarn/pnpm dlx`). Pilot decision: agents do not install packages. The
  existing `test_allowlist_commands_pass` entry for `npm ci` is updated —
  it pinned exactly the unconstrained behavior this finding flags.
  Residual: `npx <pkg>` without `-y` still prompts-and-downloads when tapped;
  egress proxy (post-pilot) is the real control.
- R-03c: pilot decision — sandbox network stays AVAILABLE (agents need docs/
  fetch), but the choice becomes explicit and operator-visible: deploy
  `app_sandbox` reads `HARNESS_SANDBOX_CONFINEMENT` (default `auto`) and
  `HARNESS_SANDBOX_ALLOW_NETWORK` (default `1`) into `LocalSandboxRunner`;
  documented residual (egress allow-list/proxy deferred; compose
  `extra_hosts` kept for host model endpoints).
- R-03d (fail-closed): explicitly requested confinement that is unavailable
  now raises `SecurityPolicyViolationError` at task creation instead of
  degrading to `off`; Landlock apply failure inside the child raises
  (subprocess propagates preexec_fn exceptions to the parent) instead of
  returning False and continuing unconfined. `auto` keeps best-available
  selection (documented).
- R-03e (policy.py): add `HARNESS_` to `SENSITIVE_ENV_PREFIXES` (covers
  `HARNESS_DATABASE_URL` and all assembly config).
- R-03f (local_runner.py): spawn with `start_new_session=True`; kill via
  `os.killpg` (fallback `proc.kill`); streaming `exec` gets a real deadline —
  each read waits at most `remaining` and a pre-read deadline check fires the
  timeout; same group-kill on cancellation and in `exec_structured`.

## Rollback
Per-file revert; no schema/wire changes. Deploy env vars default to today's
behavior (`auto`, network on).

## Residuals
- Network egress remains open (pilot decision, above).
- `npx <pkg>` without `-y` (interactive install prompt) not blocked.
- `git -c core.fsmonitor=...`-class git option injection not audited (session 2).

## Verification results (R-03 COMPLETE — five findings fixed, network residual)

Commands: `cd sectionN && ./.venv/bin/python -m pytest tests/ -q` and
`./.venv/bin/ruff check src tests`; deploy file py_compile'd.

| Check | Result |
|---|---|
| section5 | 176 passed (+14: `tests/unit/test_s1_r03_sandbox_policy.py`), 1 skipped (pre-existing landlock linux-only) |
| section10 | 69 passed (assembly unaffected) |
| deploy/runners/services.py | parses; app_sandbox runner now env-driven (`HARNESS_SANDBOX_CONFINEMENT`, `HARNESS_SANDBOX_ALLOW_NETWORK`) |
| ruff | clean (section5) |

Deliberate test updates (policy STRENGTHENED, not weakened):
`test_policy.py` valid-list dropped `npm ci`; stress `test_allow_npm_ci`
became `test_block_npm_ci` — both pinned the unconstrained-installer behavior
this risk flagged.

Per-finding closure:
- R-03a: double-quoted `$( )`/backticks rejected; sensitive-var check is
  regex-over-name (braced/mid-token forms); `find -exec*/-ok*` rejected;
  glob tokens pre-expanded against the task workspace and the
  abs/relative/realpath spellings run through the same secret + forbidden
  matchers. Tests: all four confirmed bypasses + single-quote literal
  control + safe-glob control.
- R-03b: install verbs blocked (pip/pip3, `python -m pip`, npm install/i/ci,
  yarn/pnpm add/install, npx -y/--yes, yarn/pnpm dlx) with `npm test` /
  `npx vitest run` controls.
- R-03c: RESIDUAL — sandbox network stays available at pilot (documented
  decision); the choice is now explicit/operator-visible via the deploy env
  vars above; egress allow-listing deferred to post-pilot.
- R-03d: explicit `seatbelt`/`landlock` request that is unavailable raises at
  task creation (fail closed); landlock child-apply failure raises and
  propagates through `preexec_fn` to abort the spawn; `auto` unchanged.
- R-03e: `HARNESS_` added to sensitive env prefixes — `HARNESS_DATABASE_URL`
  and friends never reach agent processes.
- R-03f: spawn uses `start_new_session=True`; `_kill` group-kills via
  `os.killpg` (grandchildren die); streaming `exec` enforces the budget on
  EVERY read (hung + dripping-output tests) and on post-EOF reap;
  `exec_structured` and cancellation paths group-kill too; destroy group-kills.
- Residuals (in note header): network egress; `npx <pkg>` without `-y`;
  git option-injection audit (session 2).
- No commits made (working tree intentionally still dirty — review pending).

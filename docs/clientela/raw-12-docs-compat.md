# Harness Docs-vs-Reality & Compatibility Audit — Mechanical Sweep C+I (attempt 4)

- Scope: `/Users/user/untitled folder 2/harness` — READ-ONLY; no repo file was modified.
- Method: pure write-from-facts. Every finding below was pre-verified by attempts 1–3 (killed by provider 503s) and parallel analysts. Zero fresh exploration was performed, per task constraint.
- Legend: `[HIGH]` blocks a working deployment · `[MED]` wrong/misleading docs or degraded platform support · `[LOW]` hygiene.
- Format: `[location/component] claim → reality → fix` — one finding per line.

## A. Docs-stale (documented claims contradict code/runtime)

- [HIGH] [section10/README.md:47] Claims **49 tests** → pytest actually collects **54** → update the count (better: derive it from `pytest --collect-only` in CI so it cannot rot again).
- [HIGH] [STATUS.md] Test gate recorded as **'15'** → the real gate is the 54-collected suite (≈3.6× larger) → refresh the STATUS metric; hand-copied counters have rotted twice already.
- [HIGH] [contracts packaging] pyproject declares version **0.3.0** → installed/imported metadata reports **0.2.0** → reconcile build vs install (stale wheel, lockfile, or editable-install metadata).
- [HIGH] [section1/tests/conftest.py] Pins contracts == **0.1.0** → matches neither pyproject (0.3.0) nor installed (0.2.0); three coexisting "versions of record" → align the pin to the canonical version or delete it.
- [HIGH] [section1/tests/conftest.py] Version-pin check lives **inside conftest.py** → pytest never collects tests defined in conftest, so the guard silently never runs in the suite → move it into a real `test_*.py` module.
- [MED] [section1/tests/conftest.py] Invoked directly, the pin test **fails** (0.1.0 vs 0.2.0/0.3.0) → a "safety net" that has only ever been red → fix the pin first, then relocate it (item above) so CI actually exercises it.
- [MED] [AGENTS.md] Instructs *"Pin the SDK version; re-run task0/hello.py on every bump"* → deepseek-harness-sdk declares **no version pin in any pyproject** → add the pin AGENTS.md presupposes, or rewrite the instruction to match what exists.
- [MED] [README:47 vs STATUS.md vs collector] Suite size appears as **49 / 15 / 54** across three documents → no single source of truth for basic facts → publish counts from CI artifacts only, never hand-edited.

## B. Docs-missing (each expected file/section is a separate item)

- [HIGH] [repo root] **LICENSE** absent → legal terms undefined for any consumer/forker → add a license file and the matching classifier in pyproject.
- [MED] [repo root] **CONTRIBUTING.md** absent → no documented dev setup, test command, or gate (the real 54-test gate) → author one; embed the actual commands.
- [MED] [repo root] **SECURITY.md** absent → no channel or SLA for privately reporting vulnerabilities → add contact + disclosure policy.
- [MED] [repo root] **CHANGELOG.md** absent → the 3-way version drift (0.1.0/0.2.0/0.3.0) is unauditable → seed a changelog starting with the version reconciliation.
- [MED] [repo root] **User guide** absent → operators are left with scattered README/ONBOARDING fragments → write an end-to-end guide (install → configure → run → verify).
- [MED] [repo root / docs/] **Architecture diagram** absent or stale → service topology (orchestrator, sandbox, meeting, models, github, postgres) is invisible to newcomers → add a diagram kept next to docker-compose.
- [HIGH] [config] **.env.example** absent although services.py reads ~**20 HARNESS_\*** variables → operators must reverse-engineer config from source → ship an `.env.example` mirroring every variable with sane defaults.
- [HIGH] [docs] No consolidated **HARNESS_\* env-var reference** (name → consuming service → required? → default) → misconfiguration class already proven real by the CORS parser bug → generate the table from services.py and keep it in docs/.
- [HIGH] [compose] **HARNESS_GITHUB_CLIENT_ID** ships **empty**, and the need for operator-supplied GitHub OAuth credentials is documented nowhere → the documented onboarding flow is dead on arrival → document required secrets beside the step that consumes them.
- [HIGH] [ONBOARDING.md] GitHub OAuth step has **no setup prerequisites** (creating the OAuth app, provisioning client id/secret) → step cannot be completed as written → expand with credential-provisioning instructions or gate the step on their presence.
- [MED] [docs] No **supported-database statement**: compose pins modern Postgres while a SQLite fallback exists in code → users cannot tell SQLite is effectively untested → declare Postgres the only supported backend (or fix SQLite, items D13–D15).
- [MED] [deploy docs] No **minimum-Postgres-version** note → migrations are untested below the compose-pinned version → state the supported floor and test it in CI.
- [MED] [deploy docs] No **per-service networking prerequisites** (which containers need `host.docker.internal`) → breakage in fully-containerized stacks is silent → document, or enforce via compose (items D5–D8).
- [LOW] [observability docs] No **log-format contract** saying which streams are JSON → consumers guess → document the structured-logging policy (ties to items D16–D18).
- [MED] [frontend docs] No **browser/secure-origin requirement** → frontend silently crashes on plain-HTTP LAN deployments → document the HTTPS/localhost requirement (ties to items D1–D4).

## C. Terminology drift

- [MED] ["dsh" vs "DeepSeek Harness"] Used interchangeably across AGENTS.md, STATUS.md, and code identifiers → searchability and onboarding suffer → pick the canonical name; confine abbreviations to code identifiers.
- [MED] ["engine" vs "agent harness"] Interchanged in docs and comments → ambiguous referent in issues/discussions → reserve "engine" for the specific orchestration component or drop the word; define it once.
- [MED] ["team" vs "org"] Interchanged → risk of mismatch if config keys/API fields encode one term and docs say the other → choose one (suggest "org") and alias-map during any migration.
- [MED] ["run" vs "job"] Interchanged for executions → ambiguity in status output, logs, and tooling flags → standardize on one (suggest "run"), deprecate the other in prose.
- [LOW] [repo] **No glossary exists anywhere** → drift recurs unchecked → add a TERMS.md (or a glossary section in the user guide) listing the four canonical choices above.

## D. Compatibility / platform (one platform per item)

### D.1 Frontend secure-context (Web Crypto)

- [HIGH] [frontend auth.ts] Calls **crypto.randomUUID()** → undefined on insecure origins → TypeError thrown at auth time → feature-detect and fall back to a v4 UUID helper (or polyfill).
- [HIGH] [frontend meeting page] Same **crypto.randomUUID()** call → identical crash vector in the meeting flow → share one guarded util instead of two raw call sites.
- [HIGH] [platform: LAN HTTP] `http://<LAN-IP>:port` deployments (typical Docker/self-host setups) **hard-break** at auth/meeting screens → block release or ship the fallback + an HTTPS-only notice.
- [MED] [platform: localhost] `http://localhost` is exempt (secure-context rule) → the bug is masked in local dev and CI → add an insecure-origin smoke check to the QA checklist.

### D.2 Docker networking (extra_hosts coverage)

- [HIGH] [docker-compose] `extra_hosts: host.docker.internal:host-gateway` applied **only to orchestrator + sandbox** → every other service lacks the host alias → add it wherever host-adjacent URLs are dialed, or replace host aliases with compose service DNS names.
- [HIGH] [meeting container] References **localhost-adjacent URL(s)** → resolves to the container's own loopback → connection refused without the mapping → same fix as above.
- [HIGH] [models container] Same localhost-adjacent reference → same in-container failure mode → same fix.
- [HIGH] [github client container] Same → same fix.
- [MED] [topology] Stack works today only because some services run on the host; a **fully containerized boot fails** → add a compose profile / e2e test that brings up the entire stack in containers.

### D.3 Windows checkouts

- [HIGH] [entrypoint.sh] Plain bash script with **no CRLF guard** → Windows clones with `core.autocrlf=true` inject `\r`; container entry fails ("\r: command not found" / bad interpreter) → add `.gitattributes` (`*.sh text eol=lf`) and/or a `dos2unix "$0"` self-heal at script top.
- [MED] [Windows contributors] Broken first-run experience with no documentation hint → state the LF requirement in CONTRIBUTING/setup docs (depends on B2 existing).

### D.4 Database portability

- [MED] [compose vs migrations] Postgres pinned to a **modern** major while migrations are untested below it → undeclared compatibility floor → declare and CI-test the oldest supported Postgres.
- [HIGH] [migration 0012] **Partial-index syntax** is Postgres-flavored → SQLite fallback errors on this migration → wrap in a dialect guard or rewrite in a portable form.
- [HIGH] [migrations generally] **TIMESTAMPTZ** column types are invalid in SQLite → fallback schema creation fails → route timestamps through dialect-aware types.
- [HIGH] [SQLite fallback overall] Advertised fallback diverges from PG-only SQL → the fallback is effectively fictional → either commit to Postgres-only (remove the fallback) or add a SQLite CI job that proves parity.

### D.5 Logging pipeline

- [MED] [runtime logs] `harness.*` loggers emit **structured JSON** interleaved with **raw uvicorn.access** plaintext lines → one stream, two formats → attach access records to the JSON pipeline via a logging filter/formatter on `uvicorn.access`.
- [MED] [ops consumers] JSON shippers/alerters choke on or drop the plaintext lines → unreliable ingestion and false-negative alerts → after the fix, document the guaranteed JSON contract (closes B14).
- [LOW] [quick mitigation] Until unified: silence or reroute uvicorn.access (`--no-access-log` or a dedicated handler) so the stream is at least homogeneous.

## Highest-leverage fixes (ordered)

1. Document + wire GitHub OAuth credentials (B9/B10) — documented flow is currently impossible.
2. Ship `.env.example` + HARNESS_* reference (B7/B8) — removes the largest unknown-config surface.
3. Guard `crypto.randomUUID()` (D1/D2) — one shared helper un-breaks LAN HTTP deployments.
4. Fix contracts version drift + relocate the pin test (A3–A6) — restores a meaningful guard.
5. Decide SQLite: support it (D13–D15) or remove the pretense (B11) — stops advertising a broken path.
6. Reconcile suite-size numbers from CI (A1/A2/A8) — cheap, prevents further doc rot.

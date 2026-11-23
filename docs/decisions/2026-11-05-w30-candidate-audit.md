# Decision: W30 candidate audit — verdicts with evidence and open questions

**Date:** 2026-11-05
**Status:** ACCEPTED — conservative, gate-first. No candidate is accepted for
production use by this record; each verdict is the work order for W33 or the
owner's next ruling. Deferrals are decisions, not limbo.
**Decision owner:** Mubashar Ali (repo commit identity; the operator directing
this session). The agent inventoried and verified; it adopted nothing.
**Recorded by:** the coding agent.
**Related:** [`roadmap-3.0.md`](../archive/roadmap-3.0.md) (stage 7),
[`m2-continuation-plan.md`](../m2-continuation-plan.md) (W30),
[`m2-continuation-plan.md`](../m2-continuation-plan.md) (W33),
[`hx-execution-architecture.md`](../hx-execution-architecture.md),
[`agents.md`](../agents.md),
[`2026-10-31-hx-language-surface.md`](2026-10-31-hx-language-surface.md),
[`2026-11-04-w31-scheduler.md`](2026-11-04-w31-scheduler.md)

---

## The question this settles

W30 (stage 7, Integration audit): for each candidate the roadmap surveys —
**Scheduler, Drift Workbench, Headroom, agentic-immunity, Tree-sitter, Hermes
rights** — is it safe, owned and compatible for Harness use? The exit evidence is
an independently reviewable decision per candidate: adopt, pilot, defer or
reject, with evidence and unresolved questions. Verdicts here are scoped per
candidate. No candidate in this record authorizes vendoring, dispatch, expanded
permissions, or a new control plane.

Everything below was verified locally on 2026-11-05 against the repositories
listed, not against READMEs.

---

## Candidate 1 — Scheduler (`/Users/user/scheduler` @ `5b726cb`)

| Field | Evidence |
|---|---|
| Provenance/ownership | Mubashar Ali (this operator). Faculty-classroom timetabling web app (Django 5, SQLite, OR-Tools CP-SAT), greenfield scaffold with MOCK data; frozen `Session`/`Room`/`ChangeRequest` shapes and frozen `Solver.solve()`; A2 solver lane in `solver/`. |
| License | **None.** No LICENSE file; all-rights-reserved by default. |
| Dependencies | Django 5, django-ninja, OR-Tools 9.15.6755, SQLite; no other add-ons. |
| Tests/verification | `pytest solver/` → **202 passed** (19 allocation) at the recorded revision; CI green on the pushed branch (split commits, verified in this session); W31 comparison zero validator violations at 10/50/100/200 tasks. |
| Integration surface | Neutral pure-dict contract `solver/allocation_types.py` + interval CP-SAT model `solver/allocation.py` (tasks, capabilities, dependencies, deadlines, priorities, capacity, availability); frozen timetable contract untouched. Imported into Harness by path (`SCHEDULER_ROOT`); **no code is copied or vendored**. |
| Data flow | Synthetic fixtures only; no student or ERP data; no network. |

**Verdict — PILOT (approved 2026-11-05, pilot scope only).** The W31 pilot ADR
(`2026-11-04-w31-scheduler.md`) authorizes W32 to use this as a *proposal source*
behind the existing approval boundary. It authorises nothing else.
**Unresolved:** distribution/bundling rights (no LICENSE); porting the neutral
module into this repo (would be a copy); any shipped build that includes it.
Owner ruling required before any distribution.

## Candidate 2 — Drift Workbench (`/Users/user/drift-workbench-deploy`, `/Users/user/Desktop/drift-workbench-site`)

| Field | Evidence |
|---|---|
| Provenance/ownership | Mubashar Ali (this operator). Commercial network-diagnostics MCP tool: `npx -y @drift-workbench/mcp-server` (v1.0.7, 20 tools); pre-launch "Founding License" $29 Gumroad; local-first, zero telemetry, no accounts. Marketing site is Next.js (v16) + React 19 (site repo). |
| License | **Proprietary** — no LICENSE file; Gumroad sale, pre-launch cohort. Roadmap mentions a *possible* open-sourced verifier "planned, gated on revenue" (`roadmap3.0.md` V1–V3, no phase without revenue evidence). |
| Dependencies | MCP server (packaging unknown from site); site: Next.js 16 + React 19 + TS. |
| Tests/quality | Marketing "QA" figures (16/16 unit, 8/8 fault, 50/50 parallel probes) — **self-reported, not inspected**; no run of its suite was attempted in this audit (not on disk as a test tree at the site path; no CI verified here). |
| Data flow | Local probes against local network; only trial activation contacts the registry, once; no ERP/customer data involved. |
| Fit for this repo | Diagnostic only; network-level capability grant to a worker would be broad; `/` for W33 as *optional, permissioned diagnostic through the tool gateway*. |

**Verdict — DEFER.** Not accepted now. Drift could matter to Harness only if W33
(gate: benchmark-measured, permissioned diagnostic value) is reached and this
product proves its suite and registry behaviour in Harness conditions. Not a
component of the Hx/Section 4 stack; not a control-plane candidate.
**Unresolved:** registry/account surface for a worker's permissioned access; exact
§W33 acceptance criteria; whether any verifier becomes open source (per its own
V1+ gating). **Owner trigger to revisit:** a W33 work order, with revenue/demand
evidence for the product and a rights decision.

## Candidate 3 — Headroom (`/Users/user/headroom`, `github.com/chopratejas/headroom` @ `e8151f05`, v0.29.0)

| Field | Evidence |
|---|---|
| Provenance/ownership | Third-party open source (`chopratejas/headroom`); context-compression layer for agents (library, proxy, MCP, 60–95% token cuts claimed). Local clone on disk; current `main` tip `e8151f05`. |
| License | **Apache-2.0** (LICENSE file present in root; NOTICE present; SPDX-consistent naming). |
| Dependencies | Rust workspace + Python (`uv`/`pyproject`); no Harness coupling. |
| Tests/quality | Real CI matrix (ci.yml badge), benchmarks tree (`benchmarks/`, `agent-evals/`, `e2e/`, `tests/`), SECURITY.md, CODE_OF_CONDUCT, docs site, sbom/SBOM dir, deny.toml (Rust auditing config). Evidence is safer than Drift Workbench's self-report, though its suite was not run in this audit. |
| Data flow | All agent content passes through the compressor — by design. This is the risk dimension: prompt-level redaction/exfiltration review, provider-comparison equivalence, cost accounting, and prompt determinism are unverified here. |
| Fit for this repo | **W25 benchmark first** (stage 4/5 acceptance): compare task success, cost and recovery with and without compression on this repo's benchmark before anything else. |

**Verdict — DEFER to W33/W25, pilot only with benchmark evidence.** Adopting a
context compressor without the Harness W25 benchmark would be adopting context
on README claims. Apache-2.0 makes a trial lawful; it does not make it wise.
**Unresolved:** benchmark quality on this repo's tasks; privacy/redaction review of
compressor internals vs Harness secret detector; equivalence claims
(orchestrator-equivalence needs proof, not the marketing page).

## Candidate 4 — agentic-immunity-layer (`/Users/user/Desktop/agentic-immunity-layer`, no git repo)

| Field | Evidence |
|---|---|
| Provenance/ownership | Mubashar Ali (this operator). Python skeleton: `immunity/{models,attestation,tokens,pep}.py`; 0.0.1; not version controlled; no README, no LICENSE. |
| License | **None.** All-rights-reserved by default. |
| Dependencies | Python ≥3.11; standard library only; no Harness coupling. |
| Tests/quality | One test file (`test_phase1.py`, 49 lines) against HMAC attestation, TTL tokens, PEP scope matching, revocation. ~6 KB of code. |
| Data flow | In-process attestation → token issuance → scoped calls; audit-chain by construction; no network. |
| Fit for this repo | Concepts match the identity/policy boundary (Harness policy = deny/grants; attestation = program identity). Code is far too immature to integrate; it would be rebuilt inside `harness_contracts`/`policy_enforce` patterns anyway. |

**Verdict — PILOT-of-ideas now, REJECT code.** Borrow the vocabulary and the
proof shapes (attestation, revocation bites next crossing, audit chain) into the
W32/W33 design docs; do not import or vendor this prototype. If it matures, it is a
discussion input to W33 along with OPA/Cedar (as its own Phase 2 notes).
**Unresolved:** ownership/rights if ever productised; SPIFFE/OPA maturity vs
Harness's existing `policy_engine`.

## Candidate 5 — Tree-sitter (dependency, not a product)

| Field | Evidence |
|---|---|
| Provenance/ownership | Third-party parser library used across the ecosystem. |
| License | Typically MIT per grammar; packaging decision required (this repo has no it yet). |
| Fit for this repo | The `section5` AST-search spike (Python `ast`, limits documented) proves the parse-and-bound shape; multi-language symbol extraction for the index/LSP is the open problem this would solve. |
| Current state | **Not present in this repository, in any manifest.** `docs` reference it as anticipated stage/follow-up tooling only. |

**Verdict — DEFER.** No need until the index/LSP quality gate targets
multi-language symbol extraction; introducing grammars/scanners before that
acceptance criteria exists would duplicate an existing Python-only path.
**Revisit trigger:** stage 6/LSP work or a measured failure of the current
index/search quality gates.

## Candidate 6 — Hermes (`/Users/user/Compiler_Project`, github mubasharali24428-crypto/hermes-lang)

| Field | Evidence |
|---|---|
| Provenance/ownership | Mubashar Ali (this operator). C++17 language: tree-walking interpreter + bytecode VM + GC + package manager + GUI; CI badge shows 530+ tests passing at HEAD; repo includes LICENSE and README. |
| License | **PROPRIETARY — "NO LICENSE GRANTED"** (`/Users/user/Compiler_Project/LICENSE`). Viewing permitted; everything else reserved. |
| Fit for this repo | **Ruled out by the 2026-10-31 Hx decision record.** Harness uses Hx (the checker in `packages/language`) — not the Hermes runtime, not its VM, no ABI. No Hx code is lowered to Hermes bytecode. This boundary exists precisely so no Hermes rights are exercised. |
| Data flow | Language build/test artifacts stay in the Compiler_Project repository. |

**Verdict — OUT OF SCOPE for this repository, by owner decision
(2026-10-31).** No adoption path is proposed or needed. The only lawful way to
un-resolve Hermes rights is an owner-signed change to that record — and this audit
recommends none: the Hermes/Hx separation stands.

---

## What adoption evidence each winner still lacks

| Candidate | Missing proof before any build |
|---|---|
| Scheduler (pilot) | W30/W33 ruling on distribution rights (no LICENSE); porting path if we ever want it in-tree |
| Drift Workbench | Revenue/demand evidence for its own V1 gate; Harness-permissioned worker access model; a registry/telemetry surface review |
| Headroom | W25 benchmark comparison on this repo (quality, cost, recovery); redaction review; privacy/isolation review |
| immunity ideas | W32/W33 adoption criteria vs `harness_contracts` policy surface; revocation semantics review (already present in M6 — verify parity) |
| Tree-sitter | A measured failure of current search/index quality (Python `ast` path); per-language grammar packaging |
| Hermes | Nothing — the stage 8/Hx boundary is the answer; any change needs a new owner-signed record |

## Open questions for the owner

1. Scheduler distribution: accept it as permanently out-of-tree (import by path), or add a LICENSE and port it?
2. Drift Workbench: is it a Harness candidate at all, or is it a product under its own track? (Current evidence: own product; no Harness fit until W33 evidence exists.)
3. Headroom: authorize W25 benchmark integration (which section runs it, pass/fail bar)?
4. Immunity ideas: fold attestation/revocation vocabulary into W32 design docs as input, or ignore until OPA/Cedar lands?
5. Readiness re-evaluation: schedule the W20 readiness rerun now, or after W28?

## Consequences

- No code is added to this repository by this record; sections are unchanged.
- Stage 7 is closed as *written decision + evidence* for the six surveyed candidates. None is adopted for production code.
- W33 gains a prefilled register: adopt/pilot/defer/reject per candidate, and each candidate's remaining proof.
- The 2026-10-31 Hx record and this record together leave Hermes/Hx untouched; Section isolation, contracts and goldens are unchanged (this record is documentation only).

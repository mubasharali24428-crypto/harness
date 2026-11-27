# Decision — G1 (Landlock network boundary) and G3 (root-equivalent worker daemon)

**Date:** 2026-11-22 · **Decided by:** owner (recorded in the 10X Wave A session) ·
**Supersedes:** the "undispositioned" status of G1/G3 in `docs/briefs/evidence/90day/p04-sandbox-review-2026-11-11.md` §5 and the findings ledger's "remaining sandbox decisions" row ·
**Scope:** pilot posture only. §2 boundaries, all code, and the production release gate (10.14) are unchanged. · **Src:** 10X Wave A item X-04, `FUTURE PLAN 2.0.md` §20.

## G1 — Landlock has no network hooks (drill-confirmed)

**Finding:** `docs/briefs/evidence/pilot-prep/g1-landlock-network-noop-2026-11-21.json` — a Landlock-confined child connected out to `example.com:443` despite `allow_network=False`. Landlock ABI v1 has no network hooks (`confinement.py:446` documents this as a limitation, not a regression).

**Decision: ACCEPT the policy-layer network boundary for the pilot.**
- Linux runs keep the per-run internal Docker network with external DNS killed (`container_runtime.py:90-95`), and where egress is required, only the allow-list HTTPS CONNECT proxy (`section5/src/harness_sandbox/egress_proxy.py`).
- The pilot remains macOS-first, where Seatbelt denies network outright.
- **No Landlock network claim may appear in any product copy, drill, evidence statement or marketing material** — the egress boundary is the policy layer and must always be named as such.
- Revisit triggers: a Landlock ABI with network hooks on the pilot host, or any Linux pilot with untrusted egress requirements.

## G3 — worker daemon is root-equivalent on the host

**Finding:** the worker daemon runs as root holding the host's *main* Docker socket (`docker-compose.yml:530`, `worker.Dockerfile:53-56`); whoever controls the daemon controls the host (p04 review §5, High, architectural).

**Decision: RISK-ACCEPT for the pilot, by name, under these conditions:**
- The daemon runs on a **single-purpose host**: no other workloads share the host or the socket.
- It stays recorded as an acknowledged High in the risk register.
- **Production release still requires the separately-managed (rootless or otherwise isolated) daemon** — this acceptance does not weaken the Phase 10.14 broad-release gate or any GO criterion.
- The existing compensating controls remain mandatory: `--cap-drop=ALL`, `no-new-privileges`, never-root container user, read-only rootfs with tmpfs `/tmp`, per-run `--internal` network (`confine.py:442-459`), and shell execution only through the Section 5 gate.

## Effect

- Scorecard G-B: G1 and G3 move from open/undispositioned to dispositioned-by-decision (scorecard updated 2026-11-22; 7 → 10/16).
- Remaining G-B pendings, unchanged: G2 Linux half re-drill; in-image behavioral re-probe of the deployed gate; F-SEC-05 redeploy of the RC set from the current tip.

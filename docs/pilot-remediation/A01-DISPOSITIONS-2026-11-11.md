# A-01 shell-scope dispositions — revised 2026-11-12

P-06a / A-01 engineering handoff for the 90-day plan. Source baseline:
`7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5` plus working-tree changes.
**Disposition proposals only: no risk acceptance or pilot authorization is granted.**
A second read-only review on 2026-11-12 corrected the earlier scope and pilot-policy claims.
No boundary tests were executed in this revision; the separate test agent owns execution.

## 1. Boundary distinctions

- **Host/workspace isolation:** prevent a worker reading unrelated host files or other runs.
- **Within-workspace read scope:** prevent a worker scoped to `src` reading `docs/y.txt`.
- **Write scope:** constrain which files the worker may modify.

These are different requirements. The engine's container mounts the workspace read-only,
then mounts existing scoped paths read-write (`section4/src/harness_orchestrator/confine.py:145–172,465–480`).
It does not hide other readable directories inside the workspace. Image files and session
mounts also remain accessible. Containerization alone does not resolve within-workspace read leaks.
The shell scope scanner is `section5/src/harness_sandbox/shell_gate.py:66–84`, not the binary
allow-list in `policy.py:87–120`. Policy already expands globs for secret/forbidden-path
checks (`policy.py:263–289,367–388`); that is not a complete read-scope guarantee.

## 2. Characterisation rows and proposed dispositions

The existing untracked `section5/tests/unit/test_shell_gate_real_boundary.py` contains
strict expected-failure declarations, not current-run proof. Record actual output and reasons:
a test marked XFAIL can fail for reasons other than the alleged leak. Marker absence alone
also does not prove a file was unreadable.

| Rows | Characterised concern | Source evidence | Proposed next step |
|---|---|---|---|
| R2–R5 | Absolute/relative canary reads and parent-directory listings from a scoped shell | Test rows 154–183; standalone Seatbelt gate permits broad reads at `confinement.py:162–175,516–522` | Reproduce with synthetic canaries; review host-read policy separately from write scope |
| R6–R7 | Tilde and HOME expansion into a synthetic canary directory | Same test rows; fixture sets HOME to canary at 202–203; Landlock grants HOME at `confinement.py:332–337` | Record backend-specific behavior; do not assume Linux denies these paths |
| G2 | Directory-component glob reaches `docs/y.txt` outside `src` | Test G_ROWS and `shell_gate.py:66–84` | Resolve whether scope must also restrict reads; if yes, enforce filesystem visibility rather than rely only on argument scanning |
| G6a/G6b | Recursive grep without a bounded path or over `*` | Same source; strict xfail declarations | Same read-scope decision; container-wide workspace access is not mitigation |
| G7i | Python code string opens a readable out-of-scope file | Same source; interpreter is allow-listed | Same decision; parsing arbitrary code strings is not a security boundary |
| R1/R8 and remaining G rows | Expected refusal/control cases | Existing assertions only | Re-run and retain outputs; do not label them passing without results |

The standalone shell-gate Seatbelt profile is not the engine Seatbelt wrapper: the latter
separately denies credential directories and other runs (`confine.py:229–257`). For the
engine container path, distinguish host-file isolation from within-workspace read scope.
The P-04 G1–G3 concerns remain open: Landlock network enforcement, process-mode read exposure,
and the root-equivalent Docker daemon. See `../briefs/evidence/90day/p04-sandbox-review-2026-11-11.md`.

### Workspace-root coverage

D1's `_covered` handling exists at `section4/src/harness_orchestrator/agent_tree.py:217–227`:
a normalized `.` scope covers relative in-workspace paths, not traversal paths.
State: implemented in the baseline, not independently re-tested here.

### Missing delegated scope paths

`../decisions/2026-11-05-delegated-scope-existing-paths.md` remains PROPOSED.
Missing scope targets are omitted from mounts; the narrower-intent write backstop refuses
writes (`confine.py:145–172`, `engine.py:137–179`). Earlier refusal with
`422 SCOPE_PATH_MISSING` is an option, not an implemented change or approved policy.

### Historical evidence

The risk register and P-04 review contain historical proxy and sandbox drill results.
Do not conflate historical live proxy drills with proof of the complete
`DockerSandboxRunner` deployment path. None certifies this RC build.

## 3. Pilot-policy reconciliation — owner/counsel gate

`PILOT_SOW_DPA_CHECKLIST.md:19–21,50–55` excludes Linux sandboxed execution and names a
macOS Seatbelt host. Docker on macOS runs Linux containers; it is not Seatbelt execution.
The previous wording declaring a container-only pilot approved by that checklist was wrong.

The local accelerator demo is not a customer pilot authorization. Keep the existing checklist
unchanged until the owner, counsel and security reviewer resolve the host/boundary policy.
No risk is accepted by this document, and missing signature lines must not be treated as consent.

## 4. Test-agent commands — not executed by the build agent

From the repository root on macOS:

```sh
./section5/.venv/bin/python -m pytest \
  section5/tests/unit/test_shell_gate_real_boundary.py \
  section5/tests/unit/test_shell_gate.py \
  section5/tests/unit/test_landlock_read_allowlist.py -q -rxXs
./section4/.venv/bin/python -m pytest \
  section4/tests/unit/test_agent_tree.py \
  section4/tests/unit/test_delegation.py -q
```

The real-boundary fixture hard-codes `/private/tmp` at lines 74–82. Do not copy it to Linux
and treat fixture setup failures as confinement evidence. Adapt the scratch path under review
or use the existing Linux-specific tests; record that change. Darwin-only R6/R7 xfail marks
also do not describe Landlock's HOME grant.

On an approved Linux drill host, after reviewing its Docker build/side effects:

```sh
./scripts/native_linux_drill.sh
```

P-04 §7.3 describes additional synthetic egress/credential-canary drills that still need
implementation; it is not an executable test command. Never read real credentials for a drill.
Record date, OS/kernel, active confinement backend, source hash, image ID, outputs, skips,
XFAIL/XPASS reasons and canary results in `../briefs/evidence/90day/week-03.md`.

## 5. Required decisions before A-01 acceptance

- [ ] Define whether `scopePaths` grants writes only or also promises read confidentiality.
- [ ] Decide remediation for each confirmed boundary failure; preserve failing cases until fixed.
- [ ] Rule on earlier refusal for missing delegation targets.
- [ ] Resolve P-04 G1–G3 with independent review; identify any authorized risk acceptor.
- [ ] Reconcile pilot host policy with the legal checklist before accepting customer data.
- [ ] Attach negative-test results for the exact deployed image set.

Engineering inventory and handoff are prepared. A-01 security acceptance remains pending.

# A-01 strict-xfail disposition: the rule-7 expansion forms (2026-11-21)

Day-14 RC exit checklist item 4 requires every remaining strict xfail to be
"either fixed or listed as an accepted risk with the owner's name." This record
lists them, states why a code fix is NOT the right move today, and puts the
acceptance text in front of the owner for signature.

## 1. What the xfails are

`section5/tests/unit/test_shell_gate_real_boundary.py` carries strict xfails on
two READ-leak rows (plus platform-dependent rows outside this record):

| id | command | what happens |
|---|---|---|
| `G6b-grep-recursive-star-policy.py-95` | `grep -rn {DOCS_MARKER} *` | the shell expands `*` (including `docs/`); grep recursively reads `docs/y.txt` — which lies INSIDE the workspace but OUTSIDE the run intent's `scopePaths` (`src`) — and the marker appears in the output |
| `G7i-python-code-string-policy.py-102` | `python3 -c "print(open('docs/y.txt').read())"` | the gate deliberately does not parse code strings; python reads the file and the marker leaks the same way |

Both were reproduced in the 2026-11-21 run of the real boundary suite
(`57 passed, 4 xfailed` — the other two xfails are platform-dependent rows).

## 2. Why this is not a code bug today

`scopePaths` is a **write** boundary: M03 narrows the confined child's write
roots to the intent scope. It has never been a read boundary. The OS layers
(Sep 2026 policy: workspace + system roots + HOME read allow-list) also grant
the whole workspace — so `docs/` is readable exactly like `src/`. The G-series
asks whether READS outside the intent scope leak into tool output; today they
do, by design.

"Fixing" G6b/G7i in the gate would mean denying **reads** outside
`scopePaths` — e.g. `grep -rn todo *` from the workspace root while the intent
scoped writes to `src/`. That is a real product-semantics change (agents
legitimately read sibling directories of a scoped edit), it breaks honest
workflows, and it belongs to the **F-05 owner decision** ("path/read
confidentiality semantics", FUTURE PLAN §4 F-05), not to a test-driven patch.

## 3. Disposition

- **Accepted risk, pending F-05 signature.** Scope of the risk: within one
  workspace, a confined child can read files outside the run's intent scope
  and exfiltrate their content through tool output. **Cross-workspace and
  outside-workspace reads stay denied** (the boundary suite's R-rows and the
  2026-11-21 G1/G2 evidence cover those), and the worker path denies NETWORK,
  so the leak channel is the run transcript the human reviews — which is why
  this is tolerable for a supervised pilot and not for unsupervised use.
- Interim mitigations already in place: run transcripts are reviewed by a
  human; forbidden-path and secret-file patterns deny the sensitive reads
  outright (`.env`, credentials, `/etc/shadow|passwd|sudoers`); installs are
  gated.
- **Text for the owner to sign (F-05 slice):** "I accept that, inside one
  workspace, a run's tool output may include file content from directories
  outside the run's intent scope. Intent scope governs writes. For the
  supervised pilot this is accepted: [NAME], [DATE]. Unsupervised pilots
  require the read-confidentiality decision first."
- The two xfails stay `strict=True` so any accidental closure (or regression)
  surfaces; they convert to passing tests only if F-05 later decides
  reads must be scope-narrowed and the gate implements it.

## 4. Related items closed today (no owner text needed)

- `./..` scope spellings: closed by the argument checks' tests
  (`test_dotdot_argument_leaving_the_workspace_is_denied`,
  `test_dotdot_that_resolves_back_into_scope_still_passes`,
  `_scope_hit("cat docs/../../x")` pins nested traversal) — 8 dotdot/scope
  tests green on 2026-11-21. The Day-14 checklist's "the `./..` scope
  question" is **closed in code**.
- `stack_tags` exit-code defect (week-03 §2 "open defect"): **already
  resolved** at `0e34421` — `main()` returns 2 when `docker ps` fails, the
  strict xfail is gone, 3/3 exit tests pass. The week-03 note predates it.

## 5. Correction (platform+testing Q1, 2026-11-21)

§1 says "the other two xfails are platform-dependent rows". They are not: all four
strict xfails in `test_shell_gate_real_boundary.py` are the same `leak_xfail`
(group G, rule 7), and they xfail on **both** platforms — macOS (Q0 junit, `42f337fc`)
and Linux kernel 6.12.76-linuxkit (Q1, `2fda5bd8`: `542 passed, 10 skipped, 4 xfailed`).
The darwin-only `read_xfail` is applied to no row (`_R_FAIL_OPEN` is empty).

| id | command |
|---|---|
| `G2-cat-glob-dir-component-policy.py-91` | `cat d?cs/y.txt` |
| `G6a-grep-recursive-no-path-policy.py-95` | `grep -r {DOCS_MARKER}` |
| `G6b-grep-recursive-star-policy.py-95` | `grep -rn {DOCS_MARKER} *` |
| `G7i-python-code-string-policy.py-102` | `python3 -c "print(open('docs/y.txt').read())"` |

Same mechanism, same §2 reasoning, same §3 owner text: the signature covers all four.
Ledger: `docs/briefs/evidence/quality/test-ledger.md` §2.

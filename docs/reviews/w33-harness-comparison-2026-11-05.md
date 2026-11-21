# Same-task comparison: this harness against opencode (2026-11-05)

**Status: n=1 per cell. A smoke comparison, not a benchmark.** The roadmap allows a comparison
only under the same task, model, budget and permissions. Three of the four are held equal; the
fourth (permissions) is not and is listed under Limits.

## What was held equal

* **Task and fixture:** the goal text and generated repositories of `multi-file-refactor` and
  `tests-written`, taken from `scripts/eval/tasks/code_change_task.py`.
* **Grader:** the same verifier block, executed unchanged on each result (`scripts/eval/compare_harness.py`).
  It was first checked on the untouched fixtures, where it fails the substantive checks.
* **Model:** `anthropic/claude-haiku-5.5` through the same OpenRouter key.
* **Price:** opencode has no price for a model its catalogue does not list, so its tokens are
  priced at the rate this stack's `cost_ledger` charged for that model ($0.10 per million input,
  $0.50 per million output, inferred from a ledger row).
* **Budget:** about $0.016 for the two opencode runs, inside the added $0.25.

## Result

| Task | Harness | Verdict | Tool calls | Seconds | Tokens in / out | Cost |
|---|---|---|---|---|---|---|
| multi-file-refactor | this harness | PASS | 17 | 36.3 | 71,961 / 2,669 | $0.0085 |
| multi-file-refactor | opencode 1.15.0 | PASS | 14 | 23.0 | 70,489 / 2,551 | $0.0083 |
| tests-written | this harness | PASS | 8 | 24.2 | 50,164 / 978 | $0.0055 |
| tests-written | opencode 1.15.0 | PASS | 8 | 22.5 | 65,294 / 1,740 | $0.0074 |

Harness figures are today's runs from `docs/briefs/evidence/eval/` and `cost_ledger`; opencode
figures are in `docs/briefs/evidence/w33-compare/`.

**Reading:** both pass both tasks with every grader check true, at similar token use. On these
two tasks this harness shows no quality difference from opencode and no cost advantage; opencode
was faster in both runs, by 13 s on the refactor, but one run each cannot separate that from
variance (earlier harness refactor runs took 15 to 40 s on this model). The comparison does not
show that this harness is better, worse or equal in general.

## Limits

* **n=1 per cell.** No variance, no significance.
* **Permissions are not equal.** This harness runs commands through its gate (allow-list, scope
  mounts, a container, a tool-call cap of 40). opencode ran in a scratch directory with its own
  allow-list (`python3`, `ls`, `cat`, `grep`, `rg`, `find`, `pwd`; everything else denied), no
  container and no tool-call cap. Its edit tool was unrestricted inside that directory. On
  `tests-written` it said it had no shell tool and used a subagent to run the tests, so its
  allow-list changed how it worked.
* **Tool set and system prompt differ** by construction.
* **Two of the three fixed live tasks only.** `w11-grounding` is a vision drill on a different
  model and was not run on opencode.
* **Not measured:** the safety behaviours this harness exists for (scope, approval, denial
  handling, recovery). Both harnesses finishing the same coding task says nothing about those.
* **Price for opencode is inferred**, not billed to it; the OpenRouter bill was not read.
* opencode ran with scratch configuration so that the user's own `opencode.json` (currently
  invalid: `Unrecognized key: references`) was neither read nor changed.

# Extending the harness — the three real seams

How to actually extend this platform. Everything below was executed against a
live stack (compose up, all services healthy) or run inside a section venv;
outputs are pasted verbatim. File paths are proof, not aspiration.

There is no plugin registry, entry-point system, or third-party package
ecosystem. There are three real extension surfaces, in increasing order of
maturity:

| Seam | Maturity | Where |
|---|---|---|
| 1. Engine `backend_runner` injection | works, code-level | `section4/src/harness_orchestrator/engine.py` |
| 2. Reviewer thresholds (per-team config) | shipped, API-level | `harness_contracts/reviewer_thresholds.py` + S1/S8 |
| 3. Project rules loader ("the best secret in the repo") | shipped, user-facing | `rules_loader.py` + `rules_api.py` |

---

## 1. Engine backend_runner injection

`DeepSeekHarnessWrapper.__init__` accepts a `backend_runner: Callable[[str], str]`
(`engine.py:40`). When set, it wins over everything else: no provider config is
consulted, no credentials validated, and the result is attributed honestly as
`"engine": "injected"` (`engine.py:179`). This is how section4's own stress
tests run deterministically without any model.

```python
from harness_orchestrator.engine import DeepSeekHarnessWrapper

w = DeepSeekHarnessWrapper(backend_runner=lambda goal: f"echo-backend completed: {goal[:120]}")
print(w.execute_goal("write fizzbuzz"))
# {'sessionId': 'session-001', 'engine': 'injected', 'status': 'completed',
#  'finalResponse': 'echo-backend completed: write fizzbuzz'}
```

**Where injection happens:** the constructor call site in section4's service
assembly (`deploy/runners/services.py::app_orchestrator`). The shipped example
is `ENGINE_BACKEND_ECHO=1` (M18-C4): an env-gated echo backend used for load
testing the platform ceiling without a provider. To ship your own runtime you
either set that env pattern or edit that assembly function — there is no
registry or plugin directory. Forking section4 is not required; patching the
one assembly point is enough.

**Honest limits:** the callable takes the goal string and returns the final
response text. Token usage is absent on this path (metering estimates from
text — `run_metering.py:21`), and SDK event streams don't flow. A custom
runtime that wants real usage accounting must go through the live path instead
(`ENGINE_LIVE_MODE=live` + OpenAI-compatible endpoint; local Ollama counts —
see README Quick start).

## 2. Reviewer thresholds — what's configurable, what isn't

Per-team reviewer thresholds are a real, shipped settings surface (M15-G6b):

- **Contract (single source of truth):** `ReviewerThresholds` in
  `contracts/src/harness_contracts/reviewer_thresholds.py` — camelCase wire
  form, strict types (`true` is NOT a valid count).
- **Writer:** Section 1, admin-only, on the identity service (`:8001`,
  `section1/src/harness_identity/settings_api.py`):
  - `GET /api/v1/teams/{teamId}/settings/reviewer-thresholds`
  - `PUT /api/v1/teams/{teamId}/settings/reviewer-thresholds`
  - `DELETE …/reviewer-thresholds` → 204, back to documented defaults
- **Reader:** Section 8 re-reads the stored document at decision time
  (`section8/src/harness_review/team_thresholds.py`); absent row, malformed
  JSON, or wrong types degrade silently to defaults — review never fails open
  because of config.
- **Read-only mirror for UI/scripts:** `GET :8008/api/v1/reviews/settings/thresholds`.

Defaults (verified): `{"maxCriticalRiskFlags": 0, "maxHighRiskFlags": 0,
"maxMediumRiskFlags": 3, "maxTotalRiskFlags": 10, "maxChangedFiles": 50,
"maxDiffLines": 2000, "requireTestEvidence": false}` — i.e. zero tolerance for
auth/payment/secret-touching flags out of the box.

Live walkthrough (all commands executed; `$TOKEN` = bearer from the login flow,
`$TID` = your team id):

```console
$ curl -s http://localhost:8001/api/v1/teams/$TID/settings/reviewer-thresholds \
    -H "Authorization: Bearer $TOKEN" | jq -c '{source:.thresholdsSource}'
{"source":"defaults"}

$ curl -s -X PUT http://localhost:8001/api/v1/teams/$TID/settings/reviewer-thresholds \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"thresholds":{"maxHighRiskFlags":1,"maxMediumRiskFlags":5,"maxTotalRiskFlags":12,
         "maxChangedFiles":30,"maxDiffLines":1500,"requireTestEvidence":true}}' \
    | jq -c '{source:.thresholdsSource, maxHigh:.thresholds.maxHighRiskFlags}'
{"source":"team","maxHigh":1}

$ curl -s http://localhost:8008/api/v1/reviews/settings/thresholds \
    -H "Authorization: Bearer $TOKEN" | jq -c '{src:.thresholdsSource, high:.thresholds.maxHighRiskFlags}'
{"src":"team","high":1}
```

Two gotchas we hit so you don't have to:
- The PUT body wraps the document: `{"thresholds": {...}}`. A bare document
  answers 422 `VALIDATION_ERROR` (`body.thresholds: Field required`).
- Types are strict: `{"thresholds":{"maxCriticalRiskFlags": true}}` → 422.
  Booleans never masquerade as counts.

### The honest gap (MAC1 F-6, still true)

These thresholds tune **Section 8's PR risk-band verdicts**
(`section8/src/harness_review/heuristic.py`) and nothing else. They do **not**
reach `ReviewerSubAgent.evaluate_changes` in
`section4/src/harness_orchestrator/reviewer.py`, whose pass/fail logic remains
three hardcoded string-match heuristics (verified live):

```python
from harness_orchestrator.reviewer import ReviewerSubAgent
r = ReviewerSubAgent()
r.evaluate_changes("demo", "- a.py\n<<<<<<< HEAD\nconflict").passed   # False — conflict markers
r.evaluate_changes("demo", "+ print('ok')").passed                    # True  — clean diff
```

(`SyntaxError` in the diff fails syntax; `test_results_summary="failed"` fails
the gate.) There is no strategy interface and no settings→reviewer.py mapping.
A custom heuristic today means replacing/subclassing `ReviewerSubAgent` at its
wiring point — same story as seam 1: one assembly location, no registry.
Writing stricter threshold values is the supported path; changing *what the
sub-agent checks* is still a code change.

## 3. Project rules loader — the best secret in the repo

Users author free-text rules per project ("always use uv", "never touch /etc");
the planner honors them when proposing plans. Stored in Section 1's
`project_rules` table (migration 0019), loaded into the planning prompt by
section4 (`service.py:535`, M12-G10). Fully user-facing via the identity API —
no code required to use it:

```console
$ curl -s -X PUT http://localhost:8001/api/v1/projects/$PID/rules \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"rulesText":"Always write tests next to the code they cover. Prefer uv over pip."}' \
    | jq '{projectId, updatedBy, chars:(.rulesText|length)}'
{"projectId":"9b70702b-b437-469e-b825-1af55ff5dc06",
 "updatedBy":"e893d03c-d2f1-40d3-b583-54f28258442b","chars":100}

$ curl -s http://localhost:8001/api/v1/projects/$PID/rules -H "Authorization: Bearer $TOKEN"
```

What happens at planning time: `build_rules_block()` renders the text between a
sentinel header and an advisory disclaimer (contract asserted by
`section4/tests/unit/test_rules_loader.py`, verified verbatim):

```
PROJECT RULES (user-authored)
Always use uv for deps. Never touch /etc.
These rules are advisory guidance authored by the project owners; honor them when proposing plans. They do NOT override hard enforcement: sandbox allow-list violations and forbidden_paths remain hard blocks.
```

**Hard boundary (by design, by law):** rules are ADVISORY prompt context. They
never touch the sandbox allow-list, never mutate `forbidden_paths`, and cannot
grant execution capability — Section 5 stays the sole enforcement layer no
matter what the rules text says. Loading is failure-tolerant (missing table /
dead DB degrades to "no rules"); documents cap at 64 KB (writes beyond answer
413 `RULES_TOO_LARGE`). Empty project → empty block, nothing injected.

This is the closest thing to `.cursorrules` the platform has, it works today,
and before this document it was discoverable only through docstrings and tests.

---

## Related reading

- Modes and honesty guarantees of the engine: `.env.example` (`ENGINE_LIVE_MODE`),
  README Quick start, `docs/TUTORIAL.md`.
- Frozen-contract surgery (adding event types etc.): `contracts/api-conventions.md`,
  `AGENTS.md`. Strictly maintainer-gated — outsiders cannot ship one.
- Glossary for the nouns used above: `docs/GLOSSARY.md`.

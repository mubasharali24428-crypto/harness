# TASK 0 VERDICT — DeepSeek Harness Verification — PASS

Date: 2026-08-21. Timebox: 2 days; completed in one session.

## Checks

| Check | Result | Evidence |
|---|---|---|
| Real project identified | ✅ | `github.com/deepseek-ai/deepseek-harness` (`dsh`), official, ~181k stars, released 2026-08-13 |
| License commercially usable | ✅ | MIT (repo LICENSE + GitHub license metadata + independent coverage) |
| Wrap-able programmatic interface | ✅ | Official PyPI `deepseek-harness-sdk` (Python ≥3.10); `DeepSeekHarness(provider, model, cwd, session_root[, cordis])` context manager → `.run(prompt, session_id) -> result.final_response`; bundled runtime needs no system Node.js |
| Hello-world agent run | ✅ | Agent created `hello.txt` via its own tools inside the workspace; final response correct; file independently verified: content = `task0 ok` |

## Hello-world setup (reproducible)

- Location: `harness/task0/` (venv + `hello.py`)
- Model endpoint: **local Ollama** `qwen3-abliterated-128k:latest` via
  `DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1`, `DEEPSEEK_API_KEY=ollama`
- Proves: harness works with ANY OpenAI-compatible endpoint → Section 3's BYOK/local-model
  requirement is directly supported by the engine itself.

## Corrections to the plan (record in contracts)

1. Core runtime is TypeScript/Node; **Section 4 wraps the Python SDK** (`deepseek-harness-sdk`),
   which bundles the runtime — no Node dependency for our backend.
2. Default example composition runs `danger-full-access` with persistent bash +
   `str_replace_editor`. Section 4 MUST use a restricted Cordis composition and pass
   sandbox policy explicitly — do not ship the default composition anywhere.
3. Project is developer preview (0.1.x) with explicit breaking-change warnings →
   pin the SDK version in the contracts package; re-run this hello-world on every bump.

## Exit rule

Task 0 PASSED → Section 4 proceeds as planned. Sections 2–10 unblocked.
Next step per EXECUTION_PLAN_RESOURCES_V1.md: Section 1 freezes the contracts package.

# harness-demo-textstats

Demo repository for the AI Engineering Harness guided first-run flow.

A tiny pure-Python text-statistics library. It exists so you can run the whole
agent loop — meeting → brief → plan → execution → review → draft PR — against a
repo that is safe by construction: no secrets, no network access, no auth or
payment code, no dependencies beyond the standard library.

## The demo task

`median_word_length` in `src/textstats/stats.py` is not implemented yet, and
`tests/test_stats.py::test_median_word_length` fails. That is deliberate. The
suggested first task for your agent:

> Implement `median_word_length(text)` — the median length of
> whitespace-separated words, `0` for empty input. Make the failing test pass
> without modifying the tests.

## Commands

```sh
python -m pytest -q      # run tests (one fails until the demo task is done)
python -m ruff check .   # lint
```

No third-party packages are required. Machine-readable task metadata lives in
`.harness/demo-task.json` for the onboarding flow to import.

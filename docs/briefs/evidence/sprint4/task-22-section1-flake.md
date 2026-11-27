# Task 22 — section1 notification delivery order flake (G-QUAL-1)

- Date (UTC): 2026-11-24
- Status: **done** — two consecutive full greens at the recorded commit.

## Symptom

`make test-section1` intermittently failed
`test_notifications_delivery.py::test_mail_holds_no_content_and_an_unsubscribe_link_whose_token_is_stored_hashed`
(Sprint 3: 1 failed / 1298 passed once; passed on re-run). The test passes in isolation every time.
Across Sprint 4 investigation the failure did **not** reproduce in 8 full-suite runs, so the fix is
recorded as a shared-state hardening, not as a confirmed root-cause elimination.

## Minimal fix (no assertion weakened)

In `section1/tests/test_notifications_delivery.py`:

1. The sensitive session title is now seeded directly into **this test's own sqlite factory**
   (`collab_sessions` insert) instead of going through the sessions router (`create_session`).
   That removes dependence on session-module state during a full-suite ordering.
2. The unsubscribe-token assertion is scoped to `team_id`/`user_id` of this test's actor instead of
   selecting every row in the table.

Every mail-content policy assertion is untouched: the secret and source id stay out of the mail,
the subject is fixed, the link works exactly once, and the stored value is the hash, never the raw
token.

## Verification

```
targeted test x3                                   → 3x 1 passed
./.venv/bin/python -m pytest tests/ -q  (run 1)      → 1299 passed, 62 skipped
./.venv/bin/python -m pytest tests/ -q  (run 2)      → 1299 passed, 62 skipped
```

Earlier full-suite runs at this code (before the edit, flake hunt): 6x 1299 passed, 62 skipped.
No test was deleted, xfailed or reordered; no assertion was relaxed.

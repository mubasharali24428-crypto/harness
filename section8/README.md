# Section 8 — Review Area

**Purpose:** The human-in-the-loop review and merge gate. Shows the diff, flags
high-risk zones, generates an AI-written PR summary (via Section 3), records
inline comments, and requires human approval before a PR is considered ready to
merge. **The app itself never merges** — merge is always a manual GitHub action.

## Depends on

- **Section 2** (GitHub Integration) — `getPRStatus` for PR metadata (state,
  branch info, PR URL). Diff text is passed in via an injectable `diff_provider`
  callable so Section 8 never calls the GitHub API directly.
- **Section 3** (Model Routing) — `callModel` on the REVIEWER role for AI summary
  generation. Deterministic fallback used when no model router is wired.
- **Section 1** (Data & Identity) — DB schema (`pull_requests`, `review_comments`,
  `agent_events`), auth middleware, and `write_audit` for the audit log.

## Exposes

Implements the `ReviewGate` protocol from the frozen contracts package:

```python
class ReviewGate(Protocol):
    def approve(self, pr_id: str, reviewer_user_id: str) -> None: ...
    def request_changes(self, pr_id: str, comments: list[dict], reviewer_user_id: str) -> None: ...
```

## Must implement

- **Diff viewer** — unified diff parsing via `diff_engine.py`, changed files
  with per-file add/delete counts, new/deleted/renamed detection.
- **Unchanged-areas note** — context lines surrounding changes highlighted so
  reviewers see what was *not* touched.
- **Test results** — read from `agent_events` (test-related event payloads).
- **AI-generated summary** — via Section 3 ModelRouter on REVIEWER role:
  what changed, why, what didn't change, what needs review, what's risky.
- **High-risk zone highlighting** — path/pattern rule engine (see
  `risk_analyzer.py`). Rules: `auth/`, `payment/`, `*migration*`, `.env*`,
  `*secret*`, `*credential*`, `*token*`, `*.pem`, `*id_rsa*`, `routes/`,
  `api/`.
- **Inline comments** — stored in `review_comments` table.
- **Approve / request-changes** — updates PR status, writes audit log.
- **Deep-link to GitHub** — returns the GitHub PR URL; merge is manual.

## Explicitly out of scope

- Auto-triggering agent revisions from review comments (§296-297 — MVP
  requires manual re-invocation).
- Automatic review-acceptance-rate tracking (§298 — metrics fast-follow).

## Running

```sh
cd section8
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check src tests
.venv/bin/python -m pytest tests/ -q
```

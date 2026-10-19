# Session 6, lane AR: artifacts and version control (part 48)

## Verification run

- `cd section4 && .venv/bin/python -m pytest -p no:cacheprovider -q tests/unit/test_artifacts*.py` — 93 passed.
- `section4/.venv/bin/ruff check` on Session 6 implementation and tests — all checks passed.
- Full Section 4 suite was attempted but did not finish; see `session6-summary.md` for the complete
  result and environmental failures.
- PostgreSQL probe was not run because Docker was unavailable and nothing listened on port 5499.

## Coverage

Migration `0049_artifacts.sql`, `artifacts.py`, and `artifact_store.py` implement content-addressed
uploads, request-id replay, size and run quotas, server-derived media type, quarantine, attachment
download headers, text-only preview, retention tombstones, safe diff parsing, run-bound changed
files, stale-base status, git permission answers, separate merge approvals, and lineage. Tests cover
team/run isolation, blob hashing, request retries and races, unsafe paths, generated/binary/large
diff markers, digest-bound change review, branch collision, stale base, merge approval separation,
quarantine access, deletion, and lineage.

## Honest limits

- The blob store is process-local disk. It is not shared between replicas or backed up; configure a
  shared volume or object store before multi-replica use.
- Git base/head values are caller-recorded. Harness does not poll GitHub, enforce its permission
  answer on Section 2 routes, or merge a pull request.
- The content classifier is a static filter, not antivirus; no archive unpacking, secret scan, or
  inline image/PDF preview is provided.
- Per-run byte/count quota checks can overshoot under concurrent uploads. Orphan blob collection
  and expiry scheduling are not wired into deploy.
- The current UI includes artifact upload/list/download/text preview and change review; browser
  keyboard/axe verification was not run.
- PostgreSQL-specific races and DDL are covered by the opt-in Session 6 probe but remain unrun here.

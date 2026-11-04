# Review a PR

The review gate on a small change: one branch-sized diff, a review that must be
recorded, and a merge that only a human may perform.

**Use it in Harness**

1. Create a project that points at this directory.
2. Import `AGENTS.md` into the project's rules (same command as the other
   example, or paste it into the rules field).
3. Open a change that touches `policy.py` — the review should name the boundary
   it crosses (a public function gaining a silent default) rather than restating
   the diff.
4. Merge only after the review is recorded and the test run is green.

```sh
python3 -m pytest -q     # currently green
```

The point of this example is what the review is asked to catch, not the code.

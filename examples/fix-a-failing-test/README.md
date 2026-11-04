# Fix a failing test

The smallest real task: one function, one failing test, one convention file.

```sh
cd examples/fix-a-failing-test
python3 -m pytest -q          # 1 failed — that is the point
```

**Use it in Harness**

1. Create a project that points at this directory (or a repo containing it).
2. Import `AGENTS.md` into the project's rules:

   ```sh
   python3 ../../scripts/migrate_agent_config.py --project-id <id> --root . --apply
   ```

   (or paste its contents into the project's rules field once).
3. Ask for the fix. Expect a plan that names `increment` and the off-by-one
   before anything runs; approve it, let the run finish, review the diff.
4. The run is done when `python3 -m pytest -q` exits 0 and the diff only touches
   `calc.py`.

The test is `test_calc.py`, and `AGENTS.md` says a merge needs both a green test
run and a human approval — the two things Harness enforces anyway.

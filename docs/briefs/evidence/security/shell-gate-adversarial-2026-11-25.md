# Shell-gate adversarial coverage (Session 5, A-01 slice)

Date: 2026-11-25. Base: `master` at `5654b53`. Scope: `section5/` only, no `contracts/` change.
FUTURE PLAN item: A-01 (shell argument/path scope failures, symlink and traversal cases).

## What changed

- **New test file** `section5/tests/unit/test_shell_gate_adversarial.py`: 90 assertions plus 7
  strict xfails, all against `shell_gate.run` with execution stubbed (decision layer only). It
  covers forbidden **paths** (16 spellings of a forbidden directory, each with a `src` scope and a
  whole-workspace scope: `./`, `//`, `..`, absolute, `*`/`?`/`[]` globs, split quotes, backslash,
  an in-scope symlink, `git show HEAD:path`, a literal path in `python3 -c`), forbidden
  **commands** (other shells, `env`, `VAR=` prefixes, `xargs`, `sed`, `git -C/-c/--git-dir`,
  `git push --force`, `find -exec/-execdir`, `/etc/passwd` and `/etc/shadow`, `/sys`, process
  substitution, heredoc, `&`, sensitive env vars, `~/.ssh/id_rsa`, `.env`), and spellings that
  leave the workspace or the scope (`/proc/self/environ`, `/etc//passwd`, `/dev/sda`, `/root`,
  `--rootdir=..`, `--output=../out`, `grep -f /etc/hostname`).
- **Gate fix** in `section5/src/harness_sandbox/shell_gate.py` (`_scope_hit`): an unquoted glob
  word is now expanded the way `/bin/sh` would expand it, and every match must lie inside the
  scope. Before this, `cat d?cs/y.txt` and `grep -rn MARKER *` with scope `["src"]` were allowed
  and read `docs/` (real-boundary rows **G2** and **G6b**, strict xfails since 2026-11-12, see
  `docs/pilot-remediation/A01-DISPOSITIONS-2026-11-11.md`). Quoted or escaped patterns
  (`find src -name '*.py'`, `grep -E "d.*s"`), flag words (`--include=*.py`) and globs that match
  nothing are left alone, as sh leaves them. If the quote scan disagrees with `shlex` on the
  words, every word is treated as a glob (fail closed).
- `section5/tests/unit/test_shell_gate_real_boundary.py`: the strict xfail marks on G2 and G6b are
  removed, because the gate now refuses both with `OUT_OF_SCOPE` (they would XPASS strict).

Policy (`policy.py`) is unchanged; it already expanded globs for the secret and forbidden-path
checks. Only the scope check was missing them.

## What was run (cloud, Linux 6.18, no OS confinement backend, no Docker daemon)

| Command | Result |
|---|---|
| `section5: pytest tests/unit/test_shell_gate_adversarial.py` | 90 passed, 7 xfailed |
| `section5: pytest tests/test_shell_gate_args.py tests/unit/test_shell_gate*.py tests/test_confine_argv.py` | passed; the 39 real-boundary rows **skipped** (no Landlock or Seatbelt here) |
| `section5: pytest tests` (full section) | 550 passed, 62 skipped, 7 xfailed, 14 failed, 18 errors. The same 32 fail/error on `master` without this change: all are `sandbox confinement 'auto' found no usable OS boundary` (cloud host), in `test_tree_api.py`, `test_checkpoints.py`, `test_s1_r01_authz.py`, `test_stream_events.py`. |
| `section4: pytest tests/unit/test_engine_shell_policy.py tests/unit/test_shell_breaker.py tests/unit/test_agent_tree.py` | 161 passed (the plugin over the real gate) |
| `section5: ruff check src tests` | clean |

**Not run here:** the real-boundary rows (G2 and G6b now expected to pass, no marker in output),
any live drill, any container or image. Those need the owner's Mac (Seatbelt) or an approved
Linux drill host. Nothing in this note claims an OS boundary result.

Mac command for the owner:

```sh
./section5/.venv/bin/python -m pytest \
  section5/tests/unit/test_shell_gate_adversarial.py \
  section5/tests/unit/test_shell_gate_real_boundary.py \
  section5/tests/unit/test_shell_gate.py -q -rxXs
```

Expected: no XPASS(strict) failures; G2 and G6b pass.

## Remaining exceptions (strict xfails, owner: security lead under F-05 / A-01)

| Row | Command | Risk | Why it is not fixed here |
|---|---|---|---|
| G6a | `grep -r y` (no path) | reads the workspace root, so `docs/` outside a `src` scope | no argument to check; refusing every path-less command would refuse `git status`, `pytest` |
| ls-R | `ls -R` | lists names outside scope via the implicit cwd | same |
| G7i | `python3 -c "open('do'+'cs/y.txt')"` | reads outside scope through a path built at run time | code strings are not parsed; write side is held by the OS write set (M03) |
| forbidden read (code) | `python3 -c "open('pri'+'vate/p.txt')"` | reads a forbidden path | forbidden paths are read-only mounts: writes stop, reads do not. Needs the F-05 read-confidentiality decision |
| forbidden read (recursion) | `grep -r p .` (whole-workspace scope) | recursion reads a forbidden path | same F-05 decision |
| rg --pre | `rg --pre=id x src` | allow-listed `rg` launches a program not on the allow-list | still inside the same OS confinement, and `python3` is allow-listed anyway; the allow-list is not a code-execution boundary |
| git upload-pack | `git fetch --upload-pack=id origin` | same as above | same |

Not a fail-open on Linux, noted for review: forbidden-path and secret-name matching is
case-sensitive (`cat PRIVATE/p.txt`). On a case-sensitive filesystem that name does not reach
`private/`. On case-insensitive macOS it would, but the standalone gate there refuses every
command while a forbidden path exists and is writable (`FORBIDDEN_PATH_EXPOSED`), so it is not
reachable today. The container path is Linux.

The A-01 disposition doc is unchanged; its G2 and G6a/G6b rows can be updated by the owner after the
Mac run confirms G2/G6b.

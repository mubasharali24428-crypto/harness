# hy4 hand-back — A1

Task: A1 — M01 native-edit drill without a model
Status: DONE
Assumptions: the running worker-daemon's `HARNESS_ENGINE_IMAGE` exists locally and contains a callable `tool-fs` edit path that does not need an LLM; the daemon's run mount layout is reproducible from `confine.py` without reading any secret.

Shorter alternative: no. The diff exceeds 100 lines because it embeds the JS probe that materialises the runtime's single-file Node SEA archive (10,232 JS/JSON files) so the *shipped* `LocalFileSystem.editText` can be imported, plus the host-side mount/hash/verify logic. A shorter drill would have to reimplement the edit's staging write instead of calling the shipped code path, which is exactly what the task forbids.

## What I found about the code path

The native edit tool is not a Python module. It is bundled inside the image's single-file Node SEA runtime
(`/usr/local/lib/python3.12/site-packages/deepseek_harness_runtime/runtime/deepseek-harness-sdk-runtime-linux-arm64`),
whose `__pkg_manifest__` / `__pkg_archive__` SEA assets hold the real `node_modules` tree:

* `@deepseek-ai/dsh-tool-fs/lib/index.js`, `applyEditTool`: the `edit` tool's `execute` body is
  `const target = await ctx.fs.resolve(...)` then `await ctx.fs.editText(target, {oldString,newString,replaceAll}, ...)`.
* `@deepseek-ai/dsh-fs-local/lib/index.js`, `LocalFileSystem.editText` -> `writeFileAtomic`, which does
  `mkdir(join(dirname(absolutePath), '.'+basename+'.'+pid+'.'+uuid+'.tmpdir'))` — a staging directory
  **beside the target**. That is the M01 note in `docs/backlog.md:356-357` ("it stages a temp beside the
  target, in the read-only parent").
* The SDK runtime's JSON-RPC switch (`handleRequest`) has no model-free tool method: only `initialize`,
  `session/*` and `shutdown`. So a direct call to the shipped module is the only no-LLM route.

`docs/backlog.md:375-380` is the caveat being closed ("Write measured by the M03 drill ... Edit not
separately driven"); `confine.py:145-172` (`scope_targets`) and `:465-472` (`wrap_command`) give the mount
set: workspace `readonly=true` when a narrower scope exists, session dir rw, each scope target rw.

## Files changed

* NEW `scripts/e2e/m01_native_edit_drill.py` (executable, root-ruff clean)
* NEW `docs/briefs/evidence/pilot-prep/m01-native-edit.json` (`ok: true`, `verdict: "PASS"`)
* Nothing else. No confinement code, no compose-managed container/volume, no env file, no secret.

## Commands run (verbatim results)

`git branch --show-current && git rev-parse --short HEAD && git status --short`:

```
w28-s1-delegate
f5b298a
?? docs/yc/
```

`docker inspect harness-worker-daemon-1 --format '{{range .Config.Env}}{{if eq (index (split . "=") 0) "HARNESS_ENGINE_IMAGE"}}{{println .}}{{end}}{{end}}'`:

```
HARNESS_ENGINE_IMAGE=harness-engine-worker:vw27s2
```

(Only that one env line is selected; no other env value is read or printed.)

`python3 scripts/e2e/m01_native_edit_drill.py` (final run, `DRILL_RC=0`):

```

PASS - M01 native edit, no model
{
  "date": "2026-11-05",
  "milestone": "M01 (native edit, separate from M03's native write)",
  "model": "none - direct call to tool-fs edit path, no LLM, no provider",
  "spend": "0",
  "engineImage": "harness-engine-worker:vw27s2",
  "scopePaths": [
    "src"
  ],
  "editCodePath": [
    "@deepseek-ai/dsh-tool-fs/lib/index.js: applyEditTool -> ctx.fs.resolve/editText",
    "@deepseek-ai/dsh-fs-local/lib/index.js: LocalFileSystem.editText -> writeFileAtomic"
  ],
  "vfsFilesExtracted": 10232,
  "dockerReturnCode": 0,
  "seconds": 0.68,
  "inside": {
    "path": "src/x.txt",
    "ok": true,
    "contentAfter": "after",
    "detail": {
      "ok": true,
      "errno": null,
      "message": null
    }
  },
  "outside": {
    "path": "docs/y.txt",
    "refusedByOS": true,
    "contentAfter": "before",
    "detail": {
      "ok": false,
      "errno": "EROFS",
      "message": "EROFS: read-only file system, mkdir '/var/folders/6s/6cr386jx5nz7y95nkjxy8t300000gn/T/m01-native-edit-91579/workspace/docs/.y.txt.1.3d5ab212-0faf-4b55-a916-5c6060868f6f.tmpdir'"
    }
  },
  "outsideScopeUnchanged": true,
  "filesHashedOutsideSrc": 2,
  "containerRemoved": true,
  "ok": true,
  "verdict": "PASS"
}
```

`make lint` (`MAKE_LINT_RC=0`), tail:

```
== ruff section4 ==
cd section4 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
All checks passed!
== ruff section5 ==
cd section5 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
All checks passed!
...
== ruff section11 ==
cd section11 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
All checks passed!
```

`section4/.venv/bin/ruff check scripts/e2e/m01_native_edit_drill.py` (`RUFF_RC=0`):

```
All checks passed!
```

`python3 -c "import json;d=json.load(open('docs/briefs/evidence/pilot-prep/m01-native-edit.json'));print('ok=',d['ok'],'verdict=',d['verdict'])"`:

```
ok= True verdict= PASS
```

Negative control — a copy of the drill in `/tmp` with **only** `dst={ws},readonly=true` changed to
`readonly=false` and its evidence redirected to `/tmp` (`CONTROL_RC=1`, `FAIL`):

```
FAIL - M01 native edit, no model
    "ok": true,
    "contentAfter": "after",
      "ok": true,
    "refusedByOS": false,
    "contentAfter": "after",
      "ok": true,
  "outsideScopeUnchanged": false,
  "ok": false,
  "verdict": "FAIL"
```

## Proof the new tests fail without the change

The drill is the test. Its three discriminating assertions are `os_refusal`, `outside_text == OLD`
and `before == after`. The control run above (workspace mounted writable, everything else identical)
shows all three fail: the shipped `editText` succeeds on `docs/y.txt` (`contentAfter: "after"`,
`refusedByOS: false`), the outside hash changes, and `verdict` becomes `FAIL` with exit 1. Independently,
reading `@deepseek-ai/dsh-fs-local/lib/index.js` shows `editText` contains no scope or permission check
at all — the only writer is `writeFileAtomic`, whose staging `mkdir` is the exact syscall the `EROFS`
message names — so removing the OS read-only layer necessarily lets the edit land. No unit test was added:
A1's acceptance is the drill output plus the evidence JSON.

## What is NOT verified

* The drill starts its own throwaway container; it does **not** exercise the daemon's own worker-spawn
  path (`worker_daemon` -> `wrap_command`). It reproduces that path's mounts from `confine.py` by reading.
* No live/stack run and no model call (by design, $0). `make test` and the full suites were not run.
* The evidence hashes 2 files outside `src` (fixture is deliberately minimal: `docs/y.txt`, `README.md`);
  it does not hash directory metadata or symlinks.
* `--network none` is stricter than the daemon's private run network; I did not verify the drill against
  the daemon's real network. No mount differs.

## Unrelated findings (one line each)

* none

## Risks the reviewer should check first

* The probe overrides `process.reallyExit` inside the throwaway container to keep the SEA's Node runtime
  alive while `editText` runs (`dsh --help` otherwise exits before the async calls finish). Drill-only;
  no production code is touched, but confirm this is acceptable as evidence rather than a hack.
* The probe extracts 10,232 JS/JSON files into a `256m` tmpfs. On a host with a smaller tmpfs the drill
  would fail for an environmental reason, not a confinement reason.
* I used the JS/JSON subset of the SEA archive, not the whole 193 MB VFS. If a future image adds a
  native/binary dependency to `dsh-fs-local`, `require()` would fail and the drill would need that
  extension added.

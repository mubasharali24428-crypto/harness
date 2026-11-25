# Opus 5.5 smooth-start checklist (Harness)

Baseline tree: `e4519ae4` on `w28-s1-delegate` (77 commits ahead of `origin`, local).

## Done in repo (engineering prep)

- [x] `docs/briefs/sprint5/MASTER-PROMPT.md` — Sprint 5 Tasks 31–40 (full Opus prompt)
- [x] `docs/briefs/sprint5/CLOUD-THREAD-HEADERS.md` — paste blocks for coordinator + threads
- [x] `docs/briefs/sprint5/PR-27-BODY.md` — PR description draft for Sprints 1–4
- [x] This checklist

## You must do before cloud Opus (Phase 0, $0)

1. **Merge PR 25** (green CI, perf branch) if accepted.
2. **Merge or reject PR 26** — if merged, you get `checkpoint1-redesign` on `master`. **Do not** push 77 commits to `w28-s1-delegate` on origin before deciding: PR 26 uses that branch name.
3. **Land Sprints 1–4 on GitHub** (recommended):
   ```bash
   cd "/Users/user/untitled folder 2/harness"
   git fetch origin master
   git switch -c w28-s2-sprints1-4 e4519ae4   # or current HEAD after prep commit
   git rebase origin/master                     # or merge master if rebase hurts
   make gate
   git push -u origin w28-s2-sprints1-4
   gh pr create --base master --head w28-s2-sprints1-4 \
     --title "feat: Sprints 1–4 — live stack, migrations 0081, multiplayer pilot, gate green" \
     --body-file docs/briefs/sprint5/PR-27-BODY.md
   ```
4. **Push the prep commit** (MASTER-PROMPT + headers) on the same branch you open as PR 27.
5. **GitHub Actions billing** — ensure CI runs on the new PR (not blocked at account level).
6. **Write down PR head SHA** after first CI queue — every cloud thread uses that SHA.

## Cloud rules (reminder)

- No Docker assumed → Tasks 33–36 and truth `make gate` on **Mac**.
- One thread = one outcome; push after **each** task.
- Opus: Tasks 31, 32, 37, 38 + checkpoint-1. Sonnet: docs, Task 39, END report draft.

## Mac tonight (if cloud finishes 31–32)

- Sprint 5 Tasks 33–36 per `MASTER-PROMPT.md`
- `make gate` on final SHA
- Push evidence logs under `docs/briefs/evidence/sprint5/`
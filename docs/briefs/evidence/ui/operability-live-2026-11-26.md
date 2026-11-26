# UI operability, live check on RC (owner Mac): 2026-11-26

- Date: 2026-11-26 local (UTC+5). Runs between about 20:00 and 20:50 UTC on 2026-11-25.
- Stack: `harness-rc` on this Mac, frontend `:3011`, `ENGINE_LIVE_MODE=dry`.
- Verdict: unchanged, **NOT READY**. STATUS was not touched. Nothing was merged or pushed.

## Integration branch

Local branch `local/ui-live-int-2026-11-26` in worktree `~/untitled folder 2/harness-ui-live`. Not pushed.

- Base: `origin/w28-s2-sprints1-4` at `fe2233cd`.
- Tip: **`ad66512327602761ee47061a654a4fb5b108b420`**.

Merged in this order with `git merge --no-ff`. There were no conflicts, and no PR touches `migrations/` or `contracts/`.

| PR | Head branch | Head | Merge commit |
|---|---|---|---|
| 39 frozen prefix | `w28-s3-memory-prefix` | `3b722bb3` | `8b86c637` |
| 40 Needs you | `claude/ui-op-s1-needs-you` | `657770e7` | `d18e950b` |
| 41 run status | `claude/ui-op-s2-run-status` | `ada1d542` | `78b79468` |
| 42 approvals | `claude/ui-op-s3-approvals` | `9c4abdcb` | `3ac9ab33` |
| 43 team settings | `claude/ui-op-s4-team-settings` | `4dcef238` | `ea4e9f71` |
| 44 palette | `claude/ui-op-s5-palette` | `88677d80` | `ad665123` |

## 1. Stack

The worktree's `.env` was copied from `harness-phaseb-b2`, which is where RC was last deployed from. These commands ran from the worktree root:

```
export HARNESS_IMAGE_TAG=v90ui1 ENGINE_LIVE_MODE=dry COMPOSE_PROFILES=worker
rc() { docker compose -p harness-rc --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.rc.yml --profile worker "$@"; }
```

| Command | Exit | Result |
|---|---|---|
| `rc build orchestrator frontend worker-daemon engine-worker` | 0 | `harness-backend:vv90ui1` 852b11d4dffd, `harness-frontend:vv90ui1` b198dc1af574, `harness-worker-daemon:vv90ui1` 122dd49de2a6, `harness-engine-worker:vv90ui1` dcad327c6314 |
| `rc run --rm --no-deps migrate` | 0 | `drift gate: OK (applied migrations match this image's chain)` |
| `rc up -d --no-build --pull never --wait` for the 13 app services | 0 | All healthy on `vv90ui1` |
| `curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:3011/` | 0 | **200** |
| `docker exec harness-rc-db-1 psql … "select count(*)\|\|'\|'\|\|max(version) from schema_migrations"` | 0 | **80\|0082** |
| `python3 scripts/ops/stack_tags.py --project harness-rc` | 0 | **Agreement: AGREE (vv90ui1)**, 11 of 11 backend services, Missing none, worker-daemon `vv90ui1` |

Notes:

- `engine-worker` is a one-shot container that the worker-daemon spawns for each run, so it was not in the `up` list.
- The `db`, `prometheus` and `alertmanager` containers were not recreated.
- `alertmanager` was already in a restart loop before this deploy, and it was left as it was.
- The new frontend bundle contains the slice strings (for example "Nothing waiting") and calls the RC ports (`localhost:18001`, `:18004`, …).

## 2. Automated, against RC

All commands ran from `section7/` after `npm ci` (exit 0).

| Command | Exit | Result |
|---|---|---|
| `npx tsc --noEmit` | 0 | clean |
| `npx vitest run` | 0 | 130 files, 1,334 tests passed |
| `PLAYWRIGHT_APP_BASE_URL=http://localhost:3011 E2E_BASE_URL=http://localhost:3011 E2E_API_URL=http://localhost:18001 E2E_IDENTITY_URL=http://localhost:18001 E2E_ORCHESTRATOR_URL=http://localhost:18004 npx playwright test e2e/r4-since-last-iteration.e2e.ts e2e/invite-revoke-live.e2e.ts --retries=0` | 0 | **4 passed, 1 skipped.** The skip is R4 "switching runs" (`runPaths.length < 2`, "the demo seeded one run only"). See the note below. |
| `node scripts/r4-since-last-check.cjs <dir>` | 0 | 10 of 10 PASS, no page errors. This script hard-codes `BASE=http://localhost:3100` and stubs every backend, so it ran against `next dev -p 3100` built from this tip. It does not test RC. |

Notes on the Playwright run:

- **Base URL.** `playwright.config.ts` reads `E2E_BASE_URL`, not `PLAYWRIGHT_APP_BASE_URL`. The specs also default their API URLs to `:8001` and `:8004`, which belong to the demo stack. Running the command exactly as written in the brief would have hit `:3000` and the demo APIs, so the `E2E_*` variables above were added.
- **First invite-revoke PASS.** This is the first Playwright PASS of `invite-revoke-live` that is not owner-reported. The `test.fail` marker was already gone. The spec still removes the engineer through the admin API, so the UI Remove button is covered by manual row T2 below.
- **R4 spec stubs the sandbox.** The R4 spec stubs the sandbox checkpoint routes with `page.route`, and its own header says so. Its PASS therefore covers the RC frontend, not real checkpoint data. See F1.

Logs: `operability-live-2026-11-26/{tsc,vitest,pw,r4check}.log`.

## 3. Manual, in the browser at :3011

How the checks were set up:

- **Admin.** A throwaway admin `uilive-admin-1126a@harness.dev` signed in through the real login form, using the dev email-only path.
- **Member.** The member sign-in through the form is blocked by F2. The member checks (palette, Home) therefore ran in a Playwright context. That context signed in through `POST /api/v1/auth/login {email, teamId}` and stored the session the same way the app's own `saveSession` does.
- **Fixture.** `operability-live-2026-11-26/fixture.py` uses real RC APIs only. It sets up:
  - an engineer and a viewer joined through real invites;
  - run `run-f8cdb7279f07`, created with `POST /plans` on project `demo-repo`;
  - three approvals requested by the engineer, so the engineer is a member who has acted.
- **Demo run.** `demo-run-66ab6d1241e6` was seeded from `/demo`.

### Home "Needs you"

| # | Check | Result | Note |
|---|---|---|---|
| H1 | Oldest first, with age, reason and Open | **PASS** | Rows: Review 6m ago, then Approval 5s ago, later also Blocked. Each row has the kind, one plain reason, a `<time>` with the full timestamp as a tooltip, and one Open link whose name includes the reason. |
| H2 | Empty state | **PASS** | A fresh team shows "Nothing waiting. See all runs". |
| H3 | Kill the API: the error names the call, and Retry works | **PASS** | `docker stop harness-rc-orchestrator-1`. The alert reads "Couldn’t load the runs list (GET /api/v1/orchestrator/runs). We couldn’t reach the server…" with a Retry button. After `docker start` (healthy in about 8 s), Retry brought back both rows and the alert went away. |
| H4 | The go-to shortcut focuses the list | **PASS** | From `/runs`, `g` `h` goes to `/` with focus on `#needs-you-list`. |
| H5 | Enter opens row 1 | **PASS** | Enter opened the demo run, which is the oldest row. |
| H6 | No shortcut while typing | **PASS** | Typing `g r g h` into the goal textarea stayed on `/` with the text in the field. The field was cleared afterwards; Enter was never pressed. |
| H7 | Member view | **PASS** | As viewer: the same rows, the list takes focus, and axe on `section.ny` is clean. A viewer sees items they cannot act on (note only). |

**U1, from a cold load: open the item that needs you in under 10 s.** The three trials took **371 ms, 366 ms and 443 ms**.

- Each trial was a full page load of `/`, then Tab, Tab to the approval row's Open, then Enter.
- The clock started at the home page's navigation start (`performance.timeOrigin`) and stopped when the run page's `h1` showed the run goal.
- The third trial ran in a brand-new tab. The list had focus 194 ms after navigation started.
- These are machine-speed keyboard timings driven by the agent. They show the app is fast enough, but they are not a person's reading and reaction time. A human U1 run is still open.

### Run page

| # | Check | Result | Note |
|---|---|---|---|
| R1 | Goal, state, spend ("unknown" if missing) and one next action | **PASS** | Fixture run: the h1 is the goal, the state reads "Waiting for someone to approve the plan", spend reads **"Spend unknown"**, and the one action is "Open approval". Demo run: "Pull request opened", "Spend < $0.01", and the one action is "Open diff" (plus the external "Pull request ↗" link). |
| R2 | A failed step is labelled in text | **PASS** | The fixture run was started in dry mode, so no model was called. The step "Engine failed: DeepSeek Harness not configured…" has a visible **"Failed"** label and `data-outcome="failed"`. The state reads "Blocked: a person needs to answer or decide", and Home lists it as Blocked. |
| R3 | Stop the stream: the page shows "Reconnecting" and keeps the last state | **PASS** | With the orchestrator stopped, the header showed "Reconnecting: this is the last state the server reported." The goal, state, spend and all 3 approval cards stayed. This is the page's `stale` path (refresh fails); no SSE socket was killed on its own. |
| R4 | Diff opens on since-last | **FAIL** | On real RC data the pane opens on **Full pull request**. Since-last is disabled, with the line "No iteration snapshots are recorded for this project yet, so this is the full pull request." See F1. |

### R4 rows 6–12 of `r4-since-last-iteration.md`, on real RC data (no stubs)

The script is `operability-live-2026-11-26/r4-real-rc.cjs`, with the 375 px re-check in `r4-375.cjs`. Results are in `r4-real-rc.json`.

| # | Check | Result | Note |
|---|---|---|---|
| 6 | Since-last is the default | **FAIL** | Full PR is pressed and since-last is disabled, because no checkpoints exist (F1). |
| 7 | Keyboard toggle | **FAIL** | The since-last button is disabled, so there is nothing to toggle to. With stubbed checkpoints, Playwright test 2 passes on the RC frontend. |
| 8 | review-decide visible | **PASS** | "Your decision" is visible, but only in the full-PR fallback; since-last is never shown on real data. |
| 9 | No ChangeReview flash | **PASS (vacuous)** | A MutationObserver ran from page start. The history never mounted, because with no checkpoints there is no history to show. |
| 10 | A run switch re-defaults to since-last | **FAIL** | The other run (`run-f8cdb7279f07`) also falls back to full PR. |
| 11 | Hunk revert with body `{ hunks: [{ path, index }] }` | **FAIL** | There are no hunks to revert on real data. The body shape is proven only against stubs: Playwright test 3 on the RC frontend, and the stubbed script. |
| 12 | axe clean at 1440 and 375, light and dark | **PASS** | axe on `.ws-pane` (wcag2a/aa, 21aa, 22aa) found **0 violations** at 1440 and 375, light and dark. At 375 the Diff pane was opened first (22 passes). There was no horizontal page scroll. Screenshots: `r4-real-rc-1440-light.png`, `r4-real-rc-375-dark-diff-open.png`. |

### Approvals

| # | Check | Result | Note |
|---|---|---|---|
| A1 | Plain outcome first, details one click away | **PASS** | The h2 reads "If you approve, the agent changes src/greeting.ts.", followed by one line of why. Files and the diff sit in a closed "Technical details" `<details>`. |
| A2 | similarCases is text only, with no apply button | **PASS (with fixture data)** | Dry mode cannot produce a denial stop. Two cases were therefore written through the product's own `case_memory.recall_and_record`, run inside the RC orchestrator container. The card then showed "Seen before in earlier runs (for your information only): denied: LEARNED FROM DENIALS (run demo-run-…)…". There are 0 buttons, links or inputs inside it, and the card's only buttons are Approve and Deny. |
| A3 | Approve and Deny announce different names | **PASS (accessibility tree)** | "Approve changing src/greeting.ts" and "Deny changing src/greeting.ts", and likewise "… deleting src/old_helper.ts". **VoiceOver itself was NOT RUN**: the names were read from the accessibility tree that VoiceOver consumes. |
| A4 | A success shows a receipt | **PASS** | "Approved by e9716428-… on Nov 26, 2026, 1:30 AM." and "Denied by …". The receipt names the person by raw user id (F3). |
| A5 | A forced failure keeps the error on the card | **PASS** | **Forced:** `fetch` was patched in the page so that one card's decision POST returned 409. The alert "Deny did not go through: Forced test failure… (409)." stayed on that card with both buttons, and was still there after an 8 s refresh. The server never received that call. **Real:** a later Deny got a real server `403 "only the session leader may do this"`, and the error stayed on the card. After leadership was re-claimed, the retry gave a receipt. |

### Team settings

| # | Check | Result | Note |
|---|---|---|---|
| T1 | Invite, then revoke with a confirm | **PASS** | `POST …/invites` returned 201 and the row showed "sent". Revoke opened an in-row "Revoke this invite? The link stops working." Cancel ("Keep the invite for …") kept the invite. "Yes, revoke" sent `DELETE …/invites/0be4b41e…`, which returned **200**, and the row showed "revoked". |
| T2 | Remove a member who has acted | **PASS** | The engineer had requested 3 approvals and was removed by keyboard (K2). The call was `DELETE http://localhost:18001/api/v1/teams/5ca1f31e…/members/9d7959bd-c944-4333-8053-c9682890a6d8`, which returned **204**. That id is the engineer's **userId**; the membership row id `4a87d43e…` is different and never appeared. The page said "Member removed from the team." The engineer's token then failed everywhere: identity `/me` **401**, orchestrator GET approvals **401**, POST approvals **401**, and re-login into the team **403**. The other member's token still returned 200. |
| T3 | Role change goes to PATCH /members/{userId} | **PASS** | `PATCH …/members/7a9e12da-131d-4cc0-8ca7-2b70d5b67173` (the userId; row id `18e7ab18…`) returned **200**. |
| T4 | A forced failure leaves the row and shows the error | **PASS** | A forced 500 on DELETE left the row in its original position with the alert "Forced test failure (500).". A forced 500 on PATCH snapped the role back to its old value and showed the error. The alert sits outside the row and does not name the member (F5). |

### Command palette

| # | Check | Result | Note |
|---|---|---|---|
| P1 | Labels match the nav | **PASS** | The sidebar Menu (All runs, Meeting, Cost, Settings, Get started), "New run", the settings nav and the key tour all match the palette entries word for word. |
| P2 | Shortcuts are shown | **PASS** | Every Go-to item shows its shortcut, for example "All runs, shortcut g then r". Settings pages show none. |
| P3 | The member role doesn't see admin-only actions | **PASS** | Admin sees "Decide pending approvals" and "Invite a teammate". Engineer sees only "Decide pending approvals". Viewer sees neither. axe on the open palette found 0 violations for both member roles. |

### Keyboard only

| # | Check | Result | Note |
|---|---|---|---|
| K1 | One approval without the mouse | **PASS** | Tab ×27 overshot onto "Start run", which was not pressed. Shift+Tab ×6 reached "Approve changing src/greeting.ts", and Enter sent the decision POST, which returned **200** and showed a receipt. After the decision, focus fell back to `<body>` (F4). |
| K2 | One member removal without the mouse | **PASS** | Tab and Shift+Tab reached "Remove uilive-eng-1126a", and Enter showed the confirm, but focus fell back to `<body>` (F4). Tab back down reached "Confirm removal of uilive-eng-1126a", and Enter sent `DELETE …/members/{userId}`, which returned 204 (T2). |

## 4. Cache

**NOT RUN.** The live batch costs money and no budget was given.

## 5. Gate, at `ad665123`

| Command | Exit | Result |
|---|---|---|
| `make bootstrap` (fresh venvs in the worktree) | 0 | |
| `make gate` | 0 | **GATE GREEN.** static-gates, lint, test, mcp, language and frontend all pass. Section suites include 2,479 passed / 60 skipped and 643 passed / 10 skipped / 9 xfailed. Frontend: 130 files, 1,334 tests. |
| `cd section1 && HARNESS_TEST_PG=1 HARNESS_TEST_PG_DSN=postgresql://…@127.0.0.1:55521/harness HARNESS_DATABASE_URL=postgresql+asyncpg://…@127.0.0.1:55521/harness HARNESS_TEST_PG_CONTAINER=uilive-throwaway-pg ./.venv/bin/python -m pytest tests/ -q -p no:randomly` | 0 | **1,370 passed, 0 skipped** |

The section1 run used a throwaway `postgres:18` (`uilive-throwaway-pg`, port 55521). It was migrated to 80|0082 with `deploy/runners/migrate.py` first.

All four overrides are needed, because the defaults point at live databases:

- `HARNESS_TEST_PG_DSN` defaults to `127.0.0.1:5499`, which is the demo DB.
- `HARNESS_TEST_PG_CONTAINER` defaults to `harness-db-1`.
- `test_f1_envelope_idempotency.py` reads `HARNESS_DATABASE_URL`, not the DSN variable.

Logs: `gate.log`, `s1pg.log`.

## Findings

### F1: FAIL. "Since last iteration" has no data source on the compose/RC stack (R4 rows 6, 7, 10, 11)

Evidence:

- **No git in the backend image.** `docker run --rm --entrypoint sh harness-backend:vv90ui1 -c 'command -v git'` finds nothing. The same is true of `vv90r7`. `deploy/backend.Dockerfile` installs no git. The engine-worker image does have `/usr/bin/git`.
- **Checkpoints are disabled at boot.** The RC sandbox logs `git not found on PATH; checkpoint snapshots disabled.` at boot (`checkpoints.py:158`). Calling `CheckpointManager.snapshot()` inside `harness-rc-sandbox-1` raises `CheckpointError: git binary unavailable; checkpoints disabled.`
- **Only one writer exists.** The only code that takes snapshots is `SandboxService.apply_edit` (`POST /api/v1/sandbox/tasks/{taskId}/edits`, `section5/src/harness_sandbox/service.py:139,154`).
- **Engine edits never reach that writer.** Per `section4/src/harness_orchestrator/service.py:2952`, engine-internal edit batches are not routed there ("UNVERIFIED seam … E1-owned").
- **The spec knew.** The R4 spec header says the runs "have no sandbox snapshots, so the sandbox checkpoint routes are stubbed".

Effect: every since-last PASS so far, in CI, on the owner's RC run and here, comes from stubbed sandbox routes. On real data, the UI's fallback (full PR, since-last disabled, one line saying why) behaves as designed.

To reproduce:

1. Open `:3011`, sign in, and seed `/demo`.
2. Open the run's Diff tab.
3. Since-last is disabled, with "No iteration snapshots are recorded for this project yet…".
4. Run `docker logs harness-rc-sandbox-1 | grep 'git not found'`.

Not fixed. A fix needs an owner/E1 decision: git in the backend image, plus engine edit batches routed through the sandbox (or snapshots taken engine-side). It is not a one-line change.

### F2: pre-existing, outside the slices. A user in two or more teams cannot sign in through the login form

Identity `auth.py:78` answers `409 "Select an existing team to sign in"`. The login page has no team picker; it only offers the SSO team-id field. Every invited member has a solo team plus the invited team, so they all hit this.

To reproduce:

1. Redeem an invite.
2. Sign out.
3. Enter the member's email at `/login`.
4. The page shows the red text and no way forward.

`auth.py` predates `fe2233c`; its last change is `61bcf60f`.

### F3: receipts and the activity live region name people by raw user id

Examples: "Approved by e9716428-bf34-44af-9dad-fe6ce91aeb42 on …" and "9d7959bd-…: approval requested". This is not a blocker for "receipt shown", but a non-expert cannot tell who decided.

### F4: keyboard focus is lost after a card or row action

Focus falls back to `<body>` in three places:

- after Approve or Deny replaces the buttons with a receipt;
- after Remove shows the confirm (the confirm does not take focus);
- after a confirmed removal.

A keyboard user has to Tab down from the top of the page again (K1, K2).

### F5: member-row failures are a page-level alert that does not name the member

The row is kept and the role snaps back, as required, but the message sits outside the row.

### F6: tester mis-click

At 20:29:02 UTC a stale click target approved plan v1 of the fixture run (`POST …/plan/versions/1/approve` 200). That is my error, not a product defect. The run was started on purpose later (R2).

### Code check of F3–F5 (2026-11-26, same tip)

- **F3:** `section4/src/harness_orchestrator/collab.py:868` sends only `"decidedBy": r.decided_by` (a user id), and `section7/src/components/workspace/ApprovalCard.tsx:45` prints it as is.
- **F4:** neither `ApprovalCard.tsx` nor `app/settings/team/{page,tables}.tsx` calls `focus()` or holds a ref, so focus is never moved.
- **F5:** `app/settings/team/page.tsx:125` keeps one page-wide `error` state, so a row error cannot show beside its row.

### Fixes for F3–F5 (2026-11-26, local commits, not pushed)

Each fix is committed on the PR branch that introduced the code:

| Branch (PR) | Commit | What changed |
|---|---|---|
| `claude/ui-op-s3-approvals` (42) | `bfe8cd86` | The receipt says "by you" when `decidedBy` is the signed-in user. After a decision, focus moves to the card's receipt, or to its error on failure (both have `tabIndex=-1`). |
| `claude/ui-op-s4-team-settings` (43) | `583e742b` | A failed remove or role change shows "Could not remove <name>: …" in that member's row, and that error takes focus. Opening a remove or revoke confirm focuses "Yes, remove" / "Yes, revoke", and Cancel returns focus to the button. A successful removal names the member and focuses the notice. |

Both fixes were merged into the integration branch: tip **`cbbfc3db`**.

- `npx tsc --noEmit`: exit 0.
- `npx vitest run`: exit 0, 130 files, 1,336 tests (+2 new).
- The RC frontend was rebuilt as `harness-frontend:vv90ui2`; the backend is still `vv90ui1`.

`operability-live-2026-11-26/fix-check.cjs` re-checked the fixes on RC with real APIs. Only the forced 500 uses `page.route`. Exit 0, **6 of 6 PASS**:

- "Denied by you on …";
- focus moves to the receipt;
- the remove confirm takes focus;
- Cancel returns focus to Remove;
- the forced failure shows "Could not remove uilive-viewer-1126a: Forced test failure (500)." in its row, with focus on it;
- the row is kept.

Still open: the activity live region ("<userId>: approval requested") comes from code outside these PRs, and other deciders still show as ids, because the run roster carries no names.

## Data left on RC

All of it is throwaway, scoped to team `5ca1f31e-6e71-4149-ad95-51a8ac78aa70`:

- users `uilive-{admin,eng,viewer}-1126a@harness.dev` (eng removed, viewer now role viewer);
- a revoked invite for `uilive-ghost-1126a`;
- runs `run-f8cdb7279f07` (blocked, engine failed in dry mode), `demo-run-66ab6d1241e6`, and two unscoped plan runs from `POST /plans` without `projectId` (the team has two projects, so a plan has to name one);
- two `case_memory` rows (A2).

The workspace dirs I created in the sandbox were removed. RC now runs `vv90ui1`, built from the integration tip.

# UI operability, cloud evidence: slice 5 (command palette)

- Slice: 5, command palette
- Branch: `claude/ui-op-s5-palette` (off `w28-s2-sprints1-4` at `fe2233c`)
- Code SHA: `07581ab`
- Date: 2026-11-25 (cloud session)

## Commands and exit codes

Run from `section7/` after `npm ci` (exit 0).

| Command | Exit | Result |
|---|---|---|
| `npx tsc --noEmit` | 0 | clean |
| `npx vitest run src/lib/paletteModel.test.tsx` | 0 | 1 file, 11 tests passed |
| `npx vitest run` (full) | 0 | 126 files, 1,285 tests passed (baseline 125 files, 1,274 tests; `src/lib/commands.test.ts` unchanged and green) |

## What the tests lock in (`section7/src/lib/paletteModel.test.tsx`)

- Home, runs, review, cost and settings each have one label ("New run", "All runs", "Review", "Cost", "Settings"), and every sidebar menu entry has the same label as its palette entry; the menu no longer keeps its own copy.
- The key tour's g-shortcuts describe their routes with the palette's labels; team uses the settings nav's label ("Team and invites").
- Every go-to route carries its "g then <key>" shortcut; the rendered item shows the keys and its text names the shortcut ("All runs, shortcut g then r"). Pages without a shortcut show none.
- Selecting an item hands back its entry; the dialog navigates to its `href`.
- Admin sees "Decide pending approvals" and "Invite a teammate"; engineer only the first; viewer and guest neither, and the rendered viewer palette contains neither label.
- An unknown or missing role (null, undefined, "", "superuser") gets no gated action but every nav and settings route.

## Method and limits

- Node-env vitest, no jsdom/RTL (repo protocol). cmdk needs a DOM, so the test renders the palette's own `PaletteGroups`
  with plain wrappers in place of `Command.Group` / `Command.Item`; cmdk's filtering, focus trap and listbox roles are not exercised.
- The palette's keyboard behaviour (Cmd/Ctrl+K, g-then-key, no shortcut inside an input) is unchanged and stays covered by `src/lib/commands.test.ts`.
- Visible label change: the palette's "Runs" is now "All runs", matching the menu and key tour. `e2e/palette.e2e.ts` filters by "spend" and uses `g r`, so it does not depend on the old label.

## Not run

- Live RC / Mac: NOT RUN (cloud session, no stack).
- Playwright e2e (palette journey, axe on the open palette) and screenshots: NOT RUN (they need a running server).

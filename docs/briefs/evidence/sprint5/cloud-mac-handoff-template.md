# Cloud ↔ Mac handoff (fill after each session)

| Field | Value |
|-------|--------|
| Branch | |
| HEAD SHA | |
| Cloud finished | Tasks |
| Mac pending | 33–36, `make gate` |

## Mac commands (truth gate)

```bash
cd "/Users/user/untitled folder 2/harness"
git pull origin <branch>
make gate
HARNESS_TEST_PG=1 make pg-probe   # optional
cd section7 && npx playwright test e2e/approval-rendering-live.e2e.ts e2e/planning-live.e2e.ts
```

## Push hygiene

Do not commit: `p1/browser.junit.xml`, `p1/live-result.json`, `ui/r2-*.png`, `.openclaw/`.
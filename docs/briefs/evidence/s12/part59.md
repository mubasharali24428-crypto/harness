# Part 59 (session 12): image and schema rollback rehearsed end to end

Date: 2026-10-20. Command: `deploy/runners/rollback_rehearsal.sh origin/master`.
N-1 = `origin/master` (`33b6621`, migration head 0056), N = `9db87ac` (head 0060). Both images built
from git (N-1 from a temporary worktree), run on an isolated compose project (`c6rehearsal`: own
network, own PostgreSQL 18 volume, no published ports), torn down on exit.

| Step | Check | Result |
|---|---|---|
| 1. deploy N-1, seed `rb-pre`, back up (`deploy/backup/backup.sh`) | migrate exits 0; identity ready; ledger at 0056; backup written | PASS x4 |
| 2. upgrade to N, insert `rb-post` | migrate exits 0; identity ready; ledger at 0060 | PASS x3 |
| 3. image-only rollback | N-1 migrate refuses the newer schema (exit 37) and names the drift gate | PASS x2 |
| 4. image + schema rollback (`restore.sh`, then N-1) | migrate exits 0; identity ready; ledger back at 0056; `rb-pre` present; `rb-post` gone | PASS x5 |
| 5. roll forward to N | migrate exits 0; identity ready; ledger at 0060 | PASS x3 |

Result: 17 of 17 checks, `REHEARSAL PASSED`.

What it proves: an image-only rollback across a migration is refused, not half-applied; the
documented rollback (restore the pre-upgrade backup, then deploy N-1) brings the service back on the
old schema; rows written after the backup are lost, as `docs/runbooks/rollback.md` section 4 states;
roll forward works again afterwards.

Limits: local Docker, identity service only (the orchestrator shares the same migrate runner and
ledger), `pg_dump` backup rather than the WAL-G path, no production data volume.

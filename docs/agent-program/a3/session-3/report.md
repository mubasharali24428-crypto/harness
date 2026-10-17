# A3-S3 — Fast isolated environments, snapshots and quotas

All new code is in `section5/src/harness_sandbox/` (A3-owned): `quota.py`, `network.py`, `backends.py`, `environments.py`, `snapshots.py`; edits to `local_runner.py` (quota hook), `docker_runner.py` (shim), `service.py` (snapshots), `api.py` (3 routes). Tests: `tests/unit/test_s3_*.py`.

## Behaviour
- **Quotas**: kernel rlimits for CPU seconds and file size; a polling guard kills the whole process group on memory/process-count/disk breach (`ResourceQuotaExceededError`). RLIMIT_AS/NPROC intentionally unused (ignored/ill-defined on macOS).
- **Network**: `process_network_allowed` — only `unrestricted` with an unexpired, task-bound grant naming an approver opens sockets; `allowlisted` means no direct sockets plus `BrokeredFetcher` (allowlist → own DNS → public-address check → every redirect hop re-checked). Command policy and `forbidden_paths` are untouched by network mode.
- **Backends**: `select_backend` never downgrades silently; the previously empty `DockerSandboxRunner` is now a real runner (locked-down `docker run`, kills the container by name) that refuses to create tasks when the daemon is down.
- **Environments**: `EnvironmentManager.create` clones the repo ref and overlays a cached dependency layer from an operator-registered provisioner; key includes team, repo, backend, provisioner and lock-file contents; layer hash manifest verified on every hit; secret files refused; layers copied by clonefile/reflink so a workspace write cannot poison the cache.
- **Snapshots**: see ledger F104.

## Evidence
| Command | Result |
|---|---|
| `cd section5 && .venv/bin/ruff check src tests` | clean |
| `.venv/bin/python -m pytest tests -q` | 206 passed, 1 skipped (pre-existing skip), 1 warning (httpx deprecation, pre-existing) |
| new S3 tests | 11 (quota/net/backend) + 11 (snapshots/envs) + 1 (API lifecycle/isolation) |

Warm-start measurement (single run, macOS arm64, synthetic provisioner: 0.4 s sleep + 300 files): cold 593 ms, warm 129 ms. Illustrates the mechanism; real dependency trees will differ.

## Not run / limits
- **Docker was not exercised live**: `docker info` fails here (daemon not running). Argv and fail-closed behaviour are unit-tested only. Environment-blocked, not a product pass.
- Quotas were tested with `confinement="off"`; combined with seatbelt not separately tested.
- Polling guard interval is 50 ms; fast allocators can overshoot between polls. cgroups v2 is the upgrade path on Linux.
- Fork/escape: process-group kill verified for fan-out via `subprocess`; double-fork `setsid` daemons that leave the group are not tracked on the local backend (Docker `--pids-limit`/container kill covers that, unverified live).
- No cache across hosts; no TTL/eviction for the environment cache beyond `invalidate(team)`.

## Rollback
Delete the five new modules and tests; revert the hunks in `local_runner.py`, `service.py`, `api.py`; restore `docker_runner.py` to the one-line stub.

## Integration proposals
- A5/deploy: wire `EnvironmentManager` + provisioner registry and a `/api/v1/sandbox/environments` route; set `SANDBOX_USE_DOCKER`-style selection through `select_backend`.
- A1/A2: feed the agent-wide network mode into `process_network_allowed` / `BrokeredFetcher` and surface `ResourceQuotaExceededError` as a run event.

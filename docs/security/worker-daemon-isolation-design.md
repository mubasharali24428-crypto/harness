# Worker-daemon isolation design (G3) — DRAFT — for owner decision

Status: DRAFT prepared by the agent (security master prompt P2). Contains the
design the Step-1 gate asked for; implementation happens only on the owner's
approval (it changes how every run's worker container is launched).

## The gap (p04-sandbox-review §5, G3, High)

`harness-worker-daemon` runs as root with the HOST's main Docker socket
(`deploy/docker-compose.yml`: `/var/run/docker.sock:/var/run/docker.sock`).
Whoever controls the daemon controls the host: the daemon builds worker
containers, and the socket grants full Docker API (host mounts, privileged
containers, host network). This is explicitly NOT the "separately managed
rootless or otherwise isolated daemon" the Step-1 gate requires.

## Options (in recommendation order)

### Option 1 — Rootless daemon (dockerd-rootless, isolated socket)
Run a SECOND, dedicated dockerd in rootless mode (or a dockerd in a DinD
sidecar) whose socket is the ONLY one the daemon mounts. The daemon keeps
root inside its container (needed for the chown path) but the socket it holds
belongs to a daemon with:
- its own data-root (no access to the main daemon's images/volumes),
- rootless worker containers (worker uid mapped, no real root on host),
- no `--privileged` default and a pinned allow-list of worker image digests.
Blast radius of a daemon compromise: the runs volume + that daemon — not the
host. Effort: compose service + daemon config + worker image pinning;
re-verify confine._worker_user paths (G5 dependency).

### Option 2 — Socket proxy (allow-listed Docker API)
Keep the main daemon; put a filtering proxy (e.g. Tecnativa socket-proxy or a
small custom HTTP filter) between the daemon and the socket that permits only
the endpoints the daemon actually uses (containers/create, /containers/{id}/
start|kill|wait|exec, /images/{name}/inspect, /networks/create) and denies
mount-creating create params (Binds, Privileged, CapAdd, HostConfig.Network).
Blast radius: much smaller; effort: smallest. Residual: the proxy is custom
code that must be re-audited on every Docker API bump.

### Option 3 — Named risk acceptance (single-purpose host)
Owner signs that the pilot runs the stack ONLY on a single-purpose host where
root-equivalence of the daemon is acceptable (nothing else on the machine, no
other credentials, known blast radius = the pilot data). This is the current
de-facto posture; it needs a signature to become a real disposition.

Recommendation: **Option 2 for the pilot** (one container + config; keeps
every existing path working), **Option 1 for production**. Either way the
daemon must never co-locate with anything else (G11 rule: no other sockets on
the host).

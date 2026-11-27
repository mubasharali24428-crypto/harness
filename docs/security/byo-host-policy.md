# BYO host policy — sandbox read-confidentiality (G2) — DRAFT — for owner decision

Status: DRAFT prepared by the agent (security master prompt P2). The owner
picks an option and signs the choice in `docs/decisions/`; nothing here takes
effect without it.

## What G2 is

On a BYO host, shell-gated code runs on the operator's machine. Before the
2026-11-21 fix, the OS read allow-list granted the whole HOME (so `~/.ssh`,
`~/.aws`, `~/.config/gh`, keychains were readable) and the gate process
inherited the runner's full environment (so the runner token and DB DSN were
readable from `/proc/<gate-pid>/environ` on Linux). Both halves were
reproduced live: macOS canary read (`g2-process-mode-macos-2026-11-21.json`)
and the Linux environ probe (`p2-report-2026-11-21.md`, "BYPASS-LEAKED" →
"gate-env-clean" after the fix).

## What is already fixed at HEAD (no decision needed)

1. **Credential-dir denies in the OS layer** (confinement.py): `~/.ssh`,
   `~/.aws`, `~/.config/gh`, `~/.gnupg`, `~/Library/Keychains`, `/etc/ssh`,
   `/etc/kubernetes`, `/run/secrets`, `/var/run/secrets` are now denied for
   gate-confined processes on BOTH Seatbelt and Landlock paths. Proven in-image
   on Linux: `/etc/ssh/notes.txt` canary → Permission denied; control file
   still readable.
2. **Gate self-sanitization** (shell_gate.py): the gate re-execs itself once
   with `sanitize_environment()` applied, so the runner token/DB DSN never
   exist in the gate process environment at all — regardless of which caller
   spawned it. Proven in-image on Linux with `HARNESS_RUNNER_TOKEN` planted:
   confined child reads `gate-env-clean`.
3. **byo_runner subprocess_gate** spawns the gate with a sanitized env too
   (belt to the suspenders in 2).

## The owner decision: where do BYO gate processes run?

- **Option A (recommended): container-only BYO.** BYO runner workers execute
  inside the confined worker container (HOME=/tmp tmpfs, no host credentials
  mounted). Process-mode (bare-Host) execution stays disabled for BYO hosts.
  Residual: none beyond the container boundary's own review (G3/G14).
- **Option B: dedicated credential-free uid.** Process-mode allowed on hosts
  where the runner runs as a single-purpose uid with no `~/.ssh`, no cloud
  creds, no docker socket group membership. Requires the operator to
  provision that uid; the credential-dir denies above are the compensating
  control for everything the policy layer misses. Residual: same-uid files
  outside the deny list remain readable by the gate's confined children.
- **Option C: named risk acceptance.** Process-mode stays available on
  arbitrary BYO hosts; the owner accepts read-exposure of non-credential
  same-uid files. Signed acceptance (name, date, scope, expiry, compensating
  controls = items 1-3 above) goes in `docs/decisions/`.

Recommendation: **A for the pilot** (it matches where the product already
runs: the hardened worker image), B for self-hosted customers who ask.

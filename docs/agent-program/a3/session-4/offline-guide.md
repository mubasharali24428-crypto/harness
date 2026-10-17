# Offline / air-gapped provisioning (A3-S4)

Validated locally only with a wheel mirror fixture and poisoned proxy/index env vars; **not** validated on a physically isolated host.

1. **Connected host** — collect pinned wheels only (no sdists):
   `python -m pip download --only-binary=:all: --no-deps -d mirror/ pkg==1.2.3 ...`
2. Build the hash index: `python -c "from harness_sandbox.offline import Mirror; Mirror(Path('mirror')).index()"` (writes `mirror-index.json`).
3. Move `mirror/` across the gap (media transfer). Record the index file's sha256 out of band.
4. **Isolated host** — `Mirror(root).verify()` must return `[]`; any name it returns was altered or lost.
5. Register the mirror with the broker (`InstallBroker(..., mirrors={"local": root})`), have a human issue a consent grant per exact install (`InstallRequest(source="mirror:local", sha256=<from index>)`), then `broker.install(...)`. pip runs with `--no-index --find-links --only-binary=:all: --require-hashes`.
6. Models: point `DEEPSEEK_BASE_URL` at a local endpoint (see AGENTS.md). Not covered by this session.

Not covered: npm/yarn mirrors, OS packages, container image mirrors, license/SBOM export, signed mirror indexes.

#!/usr/bin/env bash
# Native-Linux container tool drill.
#
#   ./scripts/native_linux_drill.sh
#
# Reproduces the exact path that failed on Docker Desktop/macOS: the engine
# shell gate applies Landlock inside a container whose workspace is a bind
# mount. On a native Linux kernel (ext4/overlayfs) the write must succeed and
# a sibling directory outside the workspace must stay unreadable.
#
#   PASS 1: python writes a file in the workspace through the shell gate
#   PASS 2: a normal read inside the workspace still works
#   PASS 3: a read of a sibling path is denied (Landlock read allow-list)
#
# Evidence: docs/briefs/evidence/pilot-prep/native-linux-container-drill.json
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE=${DRILL_IMAGE:-harness-backend:drill}
mkdir -p docs/briefs/evidence/pilot-prep
OUT=${DRILL_OUT:-docs/briefs/evidence/pilot-prep/native-linux-container-drill.json}
GATE=/opt/harness/section5/src/harness_sandbox/shell_gate.py
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

fail() { echo "[native-drill] FAIL: $*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || fail "this drill is a native-Linux proof (host is $(uname -s))"
docker info >/dev/null 2>&1 || fail "docker daemon unreachable (kernel $(uname -r))"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[native-drill] building $IMAGE" >&2
    docker build -f deploy/backend.Dockerfile -t "$IMAGE" .
fi

WORK=$(mktemp -d)
# The sibling must live outside every root the allow-list grants. On CI,
# TMPDIR points inside HOME (which is readable), so /tmp would be a bad choice:
# use /var/tmp, which is not on the list.
SIBLING=$(mktemp -d /var/tmp/harness-sibling.XXXXXX)
cleanup() { rm -rf "$WORK" "$SIBLING"; }
trap cleanup EXIT

printf 'engine ok\n' >"$WORK/readme.txt"
printf 'CANARY\n' >"$SIBLING/data.txt"

landlock_abi() {
    docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint python "$IMAGE" \
        -I -c '
import importlib.util
spec = importlib.util.spec_from_file_location(
    "c", "/opt/harness/section5/src/harness_sandbox/confinement.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(m.landlock_supported())
' 2>/dev/null | tail -1
}

run_gate() {
    local command=$1 payload
    payload=$(python3 -c '
import json, sys
print(json.dumps({
    "command": sys.argv[1],
    "cwd": "/workspace",
    "forbiddenPaths": [],
    "scopePaths": [],
    "timeoutSeconds": 60,
}))
' "$command")
    printf '%s' "$payload" | docker run --rm -i --network none \
        --user "$(id -u):$(id -g)" \
        -v "$WORK:/workspace" \
        -v "$SIBLING:/sibling:ro" \
        -w /workspace --entrypoint python "$IMAGE" -I "$GATE"
}

ABI=$(landlock_abi)
[ "$ABI" = "True" ] || fail "Landlock not usable inside the container (probe: $ABI)"
echo "[native-drill] Landlock probe: usable" >&2

WRITE_JSON=$(run_gate "python3 -c \"open('engine_ok.txt','w').write('engine ok')\"")
WRITE_OK=$(printf '%s' "$WRITE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("exitCode") == 0)')
CONTENT=$([ -f "$WORK/engine_ok.txt" ] && cat "$WORK/engine_ok.txt" || true)
[ "$WRITE_OK" = "True" ] && [ "$CONTENT" = "engine ok" ] || fail "workspace write denied: $WRITE_JSON"

READ_JSON=$(run_gate "python3 -c \"print(open('readme.txt').read().strip())\"")
READ_OK=$(printf '%s' "$READ_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("exitCode") == 0 and "engine ok" in d.get("output",""))')
[ "$READ_OK" = "True" ] || fail "normal workspace read broke: $READ_JSON"

SIB_JSON=$(run_gate "python3 -c \"print(open('/sibling/data.txt').read())\"")
SIB_BLOCKED=$(printf '%s' "$SIB_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("exitCode") != 0 and "CANARY" not in d.get("output",""))')
if [ "$SIB_BLOCKED" != "True" ]; then
    echo "[native-drill] diagnostics:" >&2
    docker run --rm --entrypoint sh "$IMAGE" -c \
        'grep -n "read_ruleset\|exec_read_roots" /opt/harness/section5/src/harness_sandbox/confinement.py | head' >&2 || true
    ESCAPE_JSON=$(run_gate "python3 -c \"open('/tmp/native-drill-escape.txt','w').write('x')\"")
    echo "[native-drill] outside write => $ESCAPE_JSON" >&2
    fail "sibling read leaked: $SIB_JSON"
fi

python3 - "$OUT" "$IMAGE" "$ABI" "$STAMP" <<PY
import json
evidence = {
    "drill": "native-linux-container-tool",
    "executedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "kernel": "$(uname -r)",
    "platform": "$(uname -sm)",
    "image": "$IMAGE",
    "landlockUsableInContainer": True,
    "checks": {
        "toolWriteThroughShellGate": {
            "ok": True,
            "detail": "engine_ok.txt written by the confined child, content verified on the host",
        },
        "workspaceReadStillWorks": {"ok": True},
        "siblingReadDenied": {
            "ok": True,
            "detail": "read of /sibling/data.txt failed; CANARY never appeared in output",
        },
    },
    "result": "pass",
    "notes": [
        "Reproduces the Docker Desktop/macOS failing path on a native Linux kernel.",
        "The shell gate fails closed when the Landlock probe is unavailable.",
        "This certifies the sandbox host boundary, not the whole product.",
    ],
}
json.dump(evidence, open("$OUT", "w"), indent=2)
print(json.dumps(evidence, indent=2))
PY

echo "[native-drill] PASS — evidence $OUT" >&2

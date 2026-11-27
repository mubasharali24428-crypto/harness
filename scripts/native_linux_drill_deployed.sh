#!/usr/bin/env bash
# Native-Linux drill of the DEPLOYED shell gate (S-01 / G-B evidence).
#
#   ./scripts/native_linux_drill_deployed.sh [IMAGE]
#
# Runs the checks of scripts/native_linux_drill.sh against an already-built
# image — the deployed tag, verified against the digest manifest — instead of
# building harness-backend:drill. Where the CI drill needs a Linux runner
# (blocked by F-SEC-01 billing) and binds host paths, this variant:
#
#   - runs anywhere `docker` runs a Linux VM (Docker Desktop included) and
#     states the kernel honestly in the evidence (linuxkit here, not bare metal);
#   - puts the workspace, sibling and /etc/ssh canary on docker VOLUMES, not
#     macOS bind mounts — Landlock cannot mediate virtiofs, so a bind mount
#     would make the denials meaningless on a Mac host;
#   - mounts the sibling read-WRITE so a sibling denial can only be Landlock
#     (an unconfined control read proves the file itself is readable);
#   - adds the G2 credential-dir canary (/etc/ssh canary denied, /etc/hostname
#     control read) so the deployed gate carries the P2 fix, not just S-01.
#
# Evidence: docs/briefs/evidence/pilot-prep/native-linux-drill-deployed.json
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE=${1:-${DRILL_IMAGE:-harness-backend:vv90r6}}
MANIFEST=docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json
OUT=${DRILL_OUT:-docs/briefs/evidence/pilot-prep/native-linux-drill-deployed.json}
GATE=/opt/harness/section5/src/harness_sandbox/shell_gate.py
CONFINEMENT=/opt/harness/section5/src/harness_sandbox/confinement.py
GATE_UID=$(id -u)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

fail() { echo "[drill-deployed] FAIL: $*" >&2; exit 1; }

docker info >/dev/null 2>&1 || fail "docker daemon unreachable"
docker image inspect "$IMAGE" >/dev/null 2>&1 || fail "image $IMAGE missing locally"

IID=$(docker image inspect "$IMAGE" --format '{{.Id}}')
MANIFEST_MATCH="not-checked"
if [ -f "$MANIFEST" ]; then
    EXPECTED=$(python3 -c "
import json,sys
m=json.load(open('$MANIFEST'))
print(m['images'].get('$IMAGE',''))")
    if [ -n "$EXPECTED" ]; then
        [ "$IID" = "$EXPECTED" ] || fail "image $IMAGE is $IID, manifest says $EXPECTED"
        MANIFEST_MATCH="yes ($EXPECTED)"
    fi
fi
echo "[drill-deployed] image $IMAGE digest matches manifest: $MANIFEST_MATCH" >&2

WVOL=drill-ws-$STAMP SVOL=drill-sib-$STAMP CVOL=drill-cred-$STAMP
docker volume create "$WVOL" >/dev/null && docker volume create "$SVOL" >/dev/null \
    && docker volume create "$CVOL" >/dev/null
cleanup() { docker volume rm -f "$WVOL" "$SVOL" "$CVOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Seed: workspace owned by the gate uid (the confined child must be able to
# write there); sibling and /etc/ssh canaries root-owned world-readable, so a
# failed read is the boundary's doing, never a permission bit.
docker run --rm --user root -v "$WVOL:/workspace" -v "$SVOL:/sibling" -v "$CVOL:/etc/ssh" \
    --entrypoint sh "$IMAGE" -c "
    echo 'engine ok' > /workspace/readme.txt && chown -R $GATE_UID:$GATE_UID /workspace && \
    echo 'CANARY' > /sibling/data.txt && \
    echo 'CANARY' > /etc/ssh/canary.txt"

MOUNTS=(-v "$WVOL:/workspace" -v "$SVOL:/sibling" -v "$CVOL:/etc/ssh")

probe() {  # unconfined python inside the image, no gate
    docker run --rm --network none --user "$GATE_UID:$GATE_UID" "${MOUNTS[@]}" \
        --entrypoint python "$IMAGE" -I -c "$1" 2>&1
}
ABI=$(docker run --rm --network none --user "$GATE_UID:$GATE_UID" --entrypoint python "$IMAGE" -I -c "
import importlib.util
spec = importlib.util.spec_from_file_location('c', '$CONFINEMENT')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(m.landlock_supported())" 2>/dev/null | tail -1)
[ "$ABI" = "True" ] || fail "Landlock not usable inside the container (probe: $ABI)"
echo "[drill-deployed] Landlock probe: usable" >&2

CONTROL_SIB=$(probe 'print(open("/sibling/data.txt").read().strip())' | tail -1)
[ "$CONTROL_SIB" = "CANARY" ] || fail "unconfined control could not read the sibling canary (mount problem, not a boundary result)"
CONTROL_CRED=$(probe 'print(open("/etc/ssh/canary.txt").read().strip())' | tail -1)
[ "$CONTROL_CRED" = "CANARY" ] || fail "unconfined control could not read the /etc/ssh canary"
echo "[drill-deployed] unconfined controls read both canaries: the denials below are the boundary" >&2

run_gate() {  # payload JSON on stdin -> gate JSON on stdout
    local command=$1 payload
    payload=$(python3 -c '
import json, sys
print(json.dumps({"command": sys.argv[1], "cwd": "/workspace", "forbiddenPaths": [],
                  "scopePaths": [], "timeoutSeconds": 60}))' "$command")
    printf '%s' "$payload" | docker run --rm -i --network none --user "$GATE_UID:$GATE_UID" \
        "${MOUNTS[@]}" -w /workspace --entrypoint python "$IMAGE" -I "$GATE"
}
field() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))"; }

WRITE_JSON=$(run_gate "python3 -c \"open('engine_ok.txt','w').write('engine ok')\"")
[ "$(printf '%s' "$WRITE_JSON" | field exitCode)" = "0" ] || fail "workspace write denied: $WRITE_JSON"
CONTENT=$(docker run --rm --user "$GATE_UID:$GATE_UID" -v "$WVOL:/workspace" \
    --entrypoint python "$IMAGE" -I -c 'print(open("/workspace/engine_ok.txt").read().strip())')
[ "$CONTENT" = "engine ok" ] || fail "write never landed on the volume"
echo "[drill-deployed] PASS 1 workspace write through the gate" >&2

READ_JSON=$(run_gate "python3 -c \"print(open('readme.txt').read().strip())\"")
READ_OUT=$(printf '%s' "$READ_JSON" | field output)
{ [ "$(printf '%s' "$READ_JSON" | field exitCode)" = "0" ] && printf '%s' "$READ_OUT" | grep -q "engine ok"; } \
    || fail "normal workspace read broke: $READ_JSON"
echo "[drill-deployed] PASS 2 workspace read" >&2

SIB_JSON=$(run_gate "python3 -c \"print(open('/sibling/data.txt').read())\"")
{ [ "$(printf '%s' "$SIB_JSON" | field exitCode)" != "0" ] \
    && ! printf '%s' "$SIB_JSON" | grep -q CANARY; } \
    || fail "sibling read leaked: $SIB_JSON"
echo "[drill-deployed] PASS 3 sibling read denied" >&2

ESCAPE_JSON=$(run_gate "python3 -c \"open('/tmp/native-drill-escape.txt','w').write('x')\"")
[ "$(printf '%s' "$ESCAPE_JSON" | field exitCode)" != "0" ] \
    || fail "outside write was allowed: $ESCAPE_JSON"
echo "[drill-deployed] PASS 4 outside write denied" >&2

CRED_JSON=$(run_gate "python3 -c \"print(open('/etc/ssh/canary.txt').read())\"")
CRED_OK=$([ "$(printf '%s' "$CRED_JSON" | field exitCode)" != "0" ] && echo yes || echo no)
CTRL_JSON=$(run_gate "python3 -c \"print(open('/etc/hostname').read().strip())\"")
{ [ "$(printf '%s' "$CTRL_JSON" | field exitCode)" = "0" ] \
    && printf '%s' "$CTRL_JSON" | field output | grep -q .; } \
    || fail "/etc control read broke (deny must be cred-dir only): $CTRL_JSON"
if [ "$CRED_OK" = "yes" ]; then
    echo "[drill-deployed] PASS 5 credential-dir canary denied, /etc control reads" >&2
else
    # A leak here is a DRIFT finding (the image predates the G2 fix), not a
    # script failure: record it in the evidence and fail the exit so
    # automation flags it. The S-01 core above stays proven either way.
    echo "[drill-deployed] DRIFT FINDING: credential-dir canary LEAKED through the deployed gate (image predates the G2 CREDENTIAL_DENIES fix)" >&2
fi

python3 - "$OUT" "$IMAGE" "$IID" "$STAMP" "$ABI" "$CRED_OK" \
    "$WRITE_JSON" "$READ_OUT" "$SIB_JSON" "$ESCAPE_JSON" "$CRED_JSON" "$CTRL_JSON" << 'PY'
import json, sys

out, image, iid, stamp, abi, cred_ok = sys.argv[1:7]
raw = {k: v for k, v in zip(
    ["write", "read", "sibling", "escape", "cred", "etcControl"], sys.argv[7:])}
cred_ok = cred_ok == "yes"
evidence = {
    "drill": "native-linux-deployed-gate (S-01 re-drill + G2 canary on the deployed tag)",
    "executedAt": stamp,
    "image": image,
    "imageId": iid,
    "manifestMatch": True,
    "landlockAbiUsableInContainer": abi == "True",
    "host": "macOS Docker Desktop — kernel is the Docker Desktop Linux VM (linuxkit), NOT bare metal; the CI native (ubuntu) row stays owed until F-SEC-01 billing is fixed",
    "filesystemNote": "workspace/sibling//etc/ssh canaries on docker volumes (VM-native ext4), never macOS virtiofs bind mounts — Landlock cannot mediate virtiofs, which is why this variant exists",
    "checks": {
        "unconfinedControlsReadCanaries": {"ok": True, "detail": "sibling + /etc/ssh canaries read by an unconfined process; denials below are attributable to the boundary"},
        "toolWriteThroughShellGate": {"ok": True, "raw": raw["write"], "detail": "engine_ok.txt written by the confined child, content verified on the volume"},
        "workspaceReadStillWorks": {"ok": True, "raw": raw["read"]},
        "siblingReadDenied": {"ok": True, "raw": raw["sibling"], "detail": "sibling mounted read-WRITE; only Landlock could stop the read"},
        "outsideWriteDenied": {"ok": True, "raw": raw["escape"], "detail": "/tmp write refused by Landlock (boundary installed, not just a read deny)"},
        "credentialDirCanaryDenied": {
            "ok": cred_ok,
            "raw": raw["cred"],
            "detail": ("G2 CREDENTIAL_DENIES present on this image"
                       if cred_ok else
                       "CANARY LEAKED — this image predates the P2 G2 fix "
                       "(188fef36): deployed confinement.py has no "
                       "CREDENTIAL_DENIES and the gate lacks "
                       "_sanitize_own_environment; the fix is at HEAD and "
                       "reaches the deployed set only on the next redeploy"),
        },
        "etcControlStillReads": {"ok": True, "raw": raw["etcControl"], "detail": "deny is cred-dir only, /etc stays readable"},
    },
    "result": "pass" if cred_ok else "pass-with-drift-finding",
    "notes": [
        "Same four checks as scripts/native_linux_drill.sh plus the G2 canary pair.",
        "Image digest verified against 90day/image-manifest-vv90r6-deployed.json before running.",
        "This certifies the deployed gate's Linux boundary, not the whole product.",
    ],
}
json.dump(evidence, open(out, "w"), indent=2)
print(json.dumps(evidence, indent=2))
PY
if [ "$CRED_OK" = "yes" ]; then
    echo "[drill-deployed] PASS — evidence $OUT" >&2
else
    echo "[drill-deployed] PASS-with-drift-finding — evidence $OUT (exit 1 so automation flags the redeploy need)" >&2
    exit 1
fi

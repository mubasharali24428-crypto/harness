#!/bin/sh
# store-backup.sh — snapshot the artifact/diff store volume (compose `harness_store`).
#
# The artifacts table is restored by PITR but only holds a sha256 + storage_ref; the BYTES live
# in this volume and were covered by no backup. Writes <BACKUP_DIR>/store-<UTC>.tgz (+ .sha256),
# read-only mount, then prunes to the newest STORE_KEEP. Objects are content-addressed and
# write-once, so take it right after the DB base backup (nightly.sh does) and a restore holds
# every blob the restored rows point at that existed at backup time.
#
#   STORE_VOLUME  (harness_harness_store)   BACKUP_DIR (~/harness-backups/store)   STORE_KEEP (14)
set -eu
STORE_VOLUME=${STORE_VOLUME:-harness_harness_store}
BACKUP_DIR=${BACKUP_DIR:-"$HOME/harness-backups/store"}
STORE_KEEP=${STORE_KEEP:-14}
IMAGE=${STORE_BACKUP_IMAGE:-alpine}
# log() writes to STDERR: stdout carries exactly one line, the snapshot path
# (nightly.sh assigns OUT=$(...), so a failure can never smuggle a log line
# into the verifier as if it were a file name).
log() { printf '[store-backup %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
docker volume inspect "$STORE_VOLUME" >/dev/null 2>&1 || { log "ERROR: volume $STORE_VOLUME not found"; exit 1; }
mkdir -p "$BACKUP_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="store-$TS.tgz"
docker run --rm -v "$STORE_VOLUME:/s:ro" -v "$BACKUP_DIR:/out" "$IMAGE" \
    sh -c "tar czf /out/$OUT.part -C /s . && mv /out/$OUT.part /out/$OUT"
( cd "$BACKUP_DIR" && shasum -a 256 "$OUT" > "$OUT.sha256" )
FILES=$(tar tzf "$BACKUP_DIR/$OUT" | grep -vc '/$' || true)
log "wrote $BACKUP_DIR/$OUT ($(du -h "$BACKUP_DIR/$OUT" | cut -f1), $FILES files)"
if [ "$STORE_KEEP" -gt 0 ]; then
    ls -1t "$BACKUP_DIR"/store-*.tgz | tail -n +"$((STORE_KEEP + 1))" | while read -r old; do
        rm -f "$old" "$old.sha256"; log "pruned $old"
    done
fi
echo "$BACKUP_DIR/$OUT"

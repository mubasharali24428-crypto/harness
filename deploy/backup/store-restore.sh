#!/bin/sh
# store-restore.sh <store-*.tgz> <NEW-volume-name> — unpack a store snapshot into a fresh volume.
# Refuses an existing volume (never overwrites). Cutover onto the live volume is a manual,
# stop-the-stack decision, same as the database (see docs/runbooks/backup-and-pitr.md).
set -eu
TGZ=${1:?usage: store-restore.sh <store-*.tgz> <new-volume>}
VOL=${2:?usage: store-restore.sh <store-*.tgz> <new-volume>}
[ -f "$TGZ" ] || { echo "no such file: $TGZ" >&2; exit 1; }
[ ! -f "$TGZ.sha256" ] || ( cd "$(dirname "$TGZ")" && shasum -a 256 -c "$(basename "$TGZ").sha256" )
if docker volume inspect "$VOL" >/dev/null 2>&1; then echo "refusing: volume $VOL already exists" >&2; exit 1; fi
docker volume create "$VOL" >/dev/null
docker run --rm -v "$VOL:/s" -v "$(cd "$(dirname "$TGZ")" && pwd):/in:ro" "${STORE_BACKUP_IMAGE:-alpine}" \
    sh -c "tar xzf /in/$(basename "$TGZ") -C /s && chown -R 1000:1000 /s 2>/dev/null; true"
echo "restored $TGZ into volume $VOL"

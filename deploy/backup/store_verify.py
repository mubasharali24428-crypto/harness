#!/usr/bin/env python3
"""Verify a store snapshot against the database: every non-deleted artifact row created before
the snapshot must have its file in the tarball with a matching sha256.

    store_verify.py <store-YYYYmmddTHHMMSSZ.tgz> [--db-container harness-db-1]

Exit 0 only when every such row is present and intact. Rows created after the snapshot are
reported, not failed (they cannot be in it).
"""
import argparse, hashlib, re, subprocess, sys, tarfile
from datetime import datetime, timezone
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("tgz")
ap.add_argument("--db-container", default="harness-db-1")
a = ap.parse_args()
tgz = Path(a.tgz)
m = re.search(r"store-(\d{8}T\d{6}Z)", tgz.name)
if not m:
    sys.exit(f"argument is not a store-YYYYmmddTHHMMSSZ.tgz path: {a.tgz!r} "
             "(did store-backup.sh fail? its stdout must be the path, nothing else)")
if not tgz.is_file():
    sys.exit(f"snapshot file not found: {tgz}")
snap = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

sidecar = Path(str(tgz) + ".sha256")
if sidecar.exists():
    want = sidecar.read_text().split()[0]
    got = hashlib.sha256(tgz.read_bytes()).hexdigest()
    if want != got:
        sys.exit(f"FAIL: tarball checksum mismatch ({tgz.name})")

rows = subprocess.run(
    ["docker", "exec", a.db_container, "psql", "-U", "harness", "-d", "harness", "-tAF", "|", "-c",
     "select storage_ref, sha256, extract(epoch from created_at) from artifacts where state <> 'deleted'"],
    capture_output=True, text=True, check=True).stdout.split()

ok = missing = bad = later = 0
problems = []
with tarfile.open(tgz) as tf:
    members = {m.name.removeprefix("./"): m for m in tf.getmembers()}
    for row in rows:
        ref, sha, created = row.split("|")
        if float(created) > snap.timestamp():
            later += 1
            continue
        member = f"artifacts/{ref}"
        if member not in members:
            missing += 1; problems.append(f"missing {member}"); continue
        data = tf.extractfile(members[member]).read()
        if hashlib.sha256(data).hexdigest() != sha:
            bad += 1; problems.append(f"sha mismatch {member}"); continue
        ok += 1
print(f"store verify {tgz.name}: ok={ok} missing={missing} corrupt={bad} created_after_snapshot={later}")
for p in problems[:10]:
    print("  " + p)
sys.exit(0 if not (missing or bad) else 1)

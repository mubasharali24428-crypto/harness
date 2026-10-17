#!/usr/bin/env python3
"""Disposable-cluster restore drill: initdb -> seed -> pg_dump -> pg_restore into a second
database -> compare content checksums -> corrupt the dump and prove the drill notices -> tear down.
Never touches an existing database: it creates its own cluster in a temp dir on a free port.
Real object-store backups / PITR on the production cluster are external validation, not covered.
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time  # noqa: E401


def sh(*a, **k):
    k.setdefault("env", {**os.environ, "LC_ALL": "C"})
    return subprocess.run(a, check=True, capture_output=True, text=True, **k)


def main() -> int:
    work = tempfile.mkdtemp(prefix="a3-pgdrill-")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()  # noqa: E702
    data = os.path.join(work, "data")
    env = {**os.environ, "LC_ALL": "C", "PGHOST": "127.0.0.1", "PGPORT": str(port), "PGUSER": "drill"}
    report = {"port": port}
    try:
        sh("initdb", "-D", data, "-U", "drill", "--auth=trust", "-E", "UTF8", "--locale=C")
        sh("pg_ctl", "-D", data, "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=",
           "-l", os.path.join(work, "log"), "-w", "start")
        q = lambda db, sql: sh("psql", "-d", db, "-Atc", sql, env=env).stdout.strip()  # noqa: E731
        q("postgres", "CREATE DATABASE src")
        q("src", "CREATE TABLE t(id int primary key, v text)")
        q("src", "INSERT INTO t SELECT i, md5(i::text) FROM generate_series(1,5000) i")
        checksum = "SELECT md5(string_agg(id||v, ',' ORDER BY id)) FROM t"
        want = q("src", checksum)
        dump = os.path.join(work, "src.dump")
        sh("pg_dump", "-Fc", "-d", "src", "-f", dump, env=env)
        t0 = time.perf_counter()
        q("postgres", "CREATE DATABASE restored")
        sh("pg_restore", "-d", "restored", dump, env=env)
        report["restore_seconds"] = round(time.perf_counter() - t0, 3)
        report["rows_restored"] = int(q("restored", "SELECT count(*) FROM t"))
        report["checksum_match"] = q("restored", checksum) == want
        bad = bytearray(open(dump, "rb").read()); bad[len(bad) // 2] ^= 0xFF  # noqa: E702
        open(dump + ".bad", "wb").write(bad)
        q("postgres", "CREATE DATABASE corrupt")
        r = subprocess.run(["pg_restore", "-d", "corrupt", dump + ".bad"], env=env,
                           capture_output=True, text=True)
        try:
            same = q("corrupt", checksum) == want
        except subprocess.CalledProcessError:
            same = False
        report["corruption_detected"] = r.returncode != 0 or not same
        report["pg_version"] = q("postgres", "SHOW server_version")
        report["ok"] = report["checksum_match"] and report["corruption_detected"]
    finally:
        subprocess.run(["pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

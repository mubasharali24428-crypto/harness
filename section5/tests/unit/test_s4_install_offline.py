"""S4: install consent, offline mirror, command-policy regression."""
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from harness_sandbox.install_consent import (
    ConsentError,
    InstallBroker,
    InstallConsentStore,
    InstallRequest,
)
from harness_sandbox.offline import Mirror, MirrorError
from harness_sandbox.policy import SecurityPolicyViolationError, validate_command


def make_wheel(dest: Path, name="fixturepkg", version="1.0", body="VALUE = 42\n") -> Path:
    dist = f"{name}-{version}.dist-info"
    whl = dest / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(f"{name}/__init__.py", body)
        z.writestr(f"{dist}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        z.writestr(f"{dist}/WHEEL", "Wheel-Version: 1.0\nGenerator: t\nRoot-Is-Purelib: true\n"
                                    "Tag: py3-none-any\n")
        z.writestr(f"{dist}/RECORD", "")
    return whl


@pytest.fixture
def mirror(tmp_path):
    root = tmp_path / "mirror"
    root.mkdir()
    whl = make_wheel(root)
    Mirror(root).index()
    return root, hashlib.sha256(whl.read_bytes()).hexdigest()


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def broker(tmp_path, mirror_root, clock=None, executor=None):
    kwargs = {"executor": executor} if executor else {}
    return (InstallBroker(InstallConsentStore(clock or Clock()), tmp_path / "audit.jsonl",
                          mirrors={"local": mirror_root}, trusted_hosts=["pypi.org"], **kwargs))


def req(sha=None, **over):
    base = dict(ecosystem="pip", package="fixturepkg", version="1.0", source="mirror:local",
                team_id="t1", run_id="r1", task_id="k1", sha256=sha)
    return InstallRequest(**{**base, **over})


def test_consent_gated_install_from_mirror_actually_works(tmp_path, mirror):
    root, sha = mirror
    seen = []

    def spy(argv, cwd):
        seen.append(argv)
        from harness_sandbox.install_consent import _default_exec
        return _default_exec(argv, cwd)

    b = broker(tmp_path, root, executor=spy)
    ws = tmp_path / "ws"
    ws.mkdir()
    r = req(sha)
    assert b.request(r)["status"] == "needsConsent"
    with pytest.raises(ConsentError) as e:
        b.install("cns_nope", r, ws)
    assert e.value.code == "CONSENT_INVALID"
    assert not (ws / ".deps").exists()                      # nothing installed without consent

    gid = b.store.issue(r, approver="alice@example.com")
    out = b.install(gid, r, ws)
    assert out["status"] == "succeeded", out
    argv = " ".join(seen[0])
    assert "--no-index" in argv and "--only-binary=:all:" in argv and "--require-hashes" in argv
    assert subprocess.run([sys.executable, "-c", "import fixturepkg;print(fixturepkg.VALUE)"],
                          env={"PYTHONPATH": str(ws / ".deps")}, capture_output=True,
                          text=True).stdout.strip() == "42"
    assert not list(ws.glob(".install-*.txt"))               # temp requirements file cleaned up
    log = (tmp_path / "audit.jsonl").read_text()
    assert "consent_requested" in log and "installed" in log and "alice@example.com" in log


def test_replay_expiry_and_scope_mismatch_denied(tmp_path, mirror):
    root, sha = mirror
    clock = Clock()
    b = broker(tmp_path, root, clock)
    ws = tmp_path / "ws"
    ws.mkdir()
    r = req(sha)
    gid = b.store.issue(r, "alice", ttl_seconds=60)
    assert b.install(gid, r, ws)["status"] == "succeeded"
    with pytest.raises(ConsentError) as e:
        b.install(gid, r, ws)
    assert e.value.code == "CONSENT_REPLAYED"

    gid2 = b.store.issue(r, "alice", ttl_seconds=60)
    clock.t += 61
    with pytest.raises(ConsentError) as e:
        b.install(gid2, r, ws)
    assert e.value.code == "CONSENT_EXPIRED"

    gid3 = b.store.issue(r, "alice", ttl_seconds=600)
    for other in (req(sha, version="2.0"), req(sha, package="otherpkg"), req(sha, run_id="r2"),
                  req(sha, team_id="t2"), req(None)):
        with pytest.raises(ConsentError) as e:
            b.install(gid3, other, ws)
        assert e.value.code == "CONSENT_MISMATCH"
    assert b.install(gid3, r, ws)["status"] == "succeeded"   # still unused after the failed tries


def test_untrusted_sources_and_malformed_requests_rejected(tmp_path, mirror):
    root, _ = mirror
    b = broker(tmp_path, root)
    for bad, code in [
        (req(source="index:evil.example"), "UNTRUSTED_SOURCE"),
        (req(source="mirror:unregistered"), "UNTRUSTED_SOURCE"),
        (req(source="https://pypi.org/simple"), "BAD_SOURCE"),
        (req(version=">=1.0"), "BAD_VERSION"),
        (req(version="1.0 --index-url http://evil"), "BAD_VERSION"),
        (req(package="git+https://evil/x.git"), "BAD_PACKAGE"),
        (req(package="../../x"), "BAD_PACKAGE"),
        (req(ecosystem="npm"), "UNSUPPORTED_ECOSYSTEM"),
        (req(sha256="abc"), "BAD_HASH"),
    ]:
        with pytest.raises(ConsentError) as e:
            b.request(bad)
        assert e.value.code == code, bad
    with pytest.raises(ConsentError):
        b.store.issue(req(), approver="  ")                 # consent needs a named approver


def test_wrong_hash_fails_install(tmp_path, mirror):
    root, _ = mirror
    b = broker(tmp_path, root)
    ws = tmp_path / "ws"
    ws.mkdir()
    r = req("0" * 64)
    out = b.install(b.store.issue(r, "alice"), r, ws)
    assert out["status"] == "failed" and not (ws / ".deps" / "fixturepkg").exists()


def test_sandbox_command_policy_still_blocks_direct_installs():
    for cmd in ("pip install fixturepkg", "python3 -m pip install x", "npm install left-pad"):
        with pytest.raises(SecurityPolicyViolationError):
            validate_command(cmd)


def test_offline_mirror_detects_tampering_and_unknown_packages(mirror):
    root, sha = mirror
    m = Mirror(root)
    assert m.lookup("FixturePkg", "1.0")["sha256"] == sha
    assert m.verify() == []
    with pytest.raises(MirrorError):
        m.lookup("fixturepkg", "9.9")
    (root / "fixturepkg-1.0-py3-none-any.whl").write_bytes(b"tampered")
    assert m.verify() == ["fixturepkg-1.0-py3-none-any.whl"]


def test_offline_install_makes_no_network_attempt(tmp_path, mirror, monkeypatch):
    root, sha = mirror
    # Poison every index/proxy path: if pip tried the network the install would fail or hang.
    monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:9/simple")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    b = broker(tmp_path, root)
    ws = tmp_path / "ws"
    ws.mkdir()
    r = req(sha)
    assert b.install(b.store.issue(r, "alice"), r, ws)["status"] == "succeeded"


def test_network_index_install_requires_a_hash_but_mirror_does_not(tmp_path, mirror):
    root, sha = mirror
    b = broker(tmp_path, root)
    b.trusted_hosts.add("pypi.example")
    with pytest.raises(ConsentError) as e:
        b.request(req(source="index:pypi.example"))
    assert e.value.code == "HASH_REQUIRED"
    assert b.request(req(sha=sha, source="index:pypi.example"))["status"] == "needsConsent"
    assert b.request(req())["status"] == "needsConsent"  # operator mirror may be unhashed


def test_pip_runs_without_user_config_or_inherited_pip_env(monkeypatch):
    import subprocess

    from harness_sandbox import install_consent as ic

    seen = {}
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://evil.example/simple")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.update(kw["env"]) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    ic._default_exec(["pip"], Path("."))
    assert "PIP_EXTRA_INDEX_URL" not in seen and seen["PIP_CONFIG_FILE"] == os.devnull

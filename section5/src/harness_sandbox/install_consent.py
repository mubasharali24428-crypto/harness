"""S4: dependency-install consent workflow.

Package installs stay blocked in the command policy. The ONLY install path is
``InstallBroker.install``, which needs a one-time consent grant bound to the exact
(ecosystem, package, exact version, source, team, run, task[, sha256]) tuple, from a
named approver, unexpired and unused. Installs run argv-only (no shell), wheels only
(no setup.py execution), into a task-local target directory, from a TRUSTED source:
a pinned registry host or an operator-registered local mirror. Every decision is
appended to an audit log. Consent is a human decision; this module cannot approve.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from harness_sandbox.config import config
from harness_sandbox.policy import SecurityPolicyViolationError, sanitize_environment

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_VERSION = re.compile(r"^[0-9]+(\.[0-9A-Za-z]+){0,5}([.+-][0-9A-Za-z.]+)?$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ConsentError(SecurityPolicyViolationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InstallRequest:
    ecosystem: str          # "pip" (npm needs a lockfile-aware path: see limits)
    package: str
    version: str            # exact pin only
    source: str             # "index:<host>" or "mirror:<name>"
    team_id: str
    run_id: str
    task_id: str
    sha256: str | None = None   # artifact hash; strongly recommended

    def validate(self) -> None:
        if self.ecosystem != "pip":
            raise ConsentError(
                "UNSUPPORTED_ECOSYSTEM", f"ecosystem '{self.ecosystem}' not supported"
            )
        if not _NAME.match(self.package):
            raise ConsentError("BAD_PACKAGE", "package name must be a plain distribution name")
        if not _VERSION.match(self.version):
            raise ConsentError("BAD_VERSION", "version must be an exact pin (no ranges/urls)")
        if self.sha256 is not None and not _SHA.match(self.sha256):
            raise ConsentError("BAD_HASH", "sha256 must be 64 lowercase hex chars")
        if not self.source.startswith(("index:", "mirror:")):
            raise ConsentError("BAD_SOURCE", "source must be index:<host> or mirror:<name>")

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass
class _Grant:
    grant_id: str
    digest: str
    approver: str
    expires_at: float
    used: bool = False


class InstallConsentStore:
    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._grants: dict[str, _Grant] = {}
        self._now = now

    def issue(self, req: InstallRequest, approver: str, ttl_seconds: int = 300) -> str:
        req.validate()
        if not approver.strip():
            raise ConsentError("NO_APPROVER", "a named human approver is required")
        gid = f"cns_{secrets.token_urlsafe(16)}"
        self._grants[gid] = _Grant(gid, req.digest(), approver, self._now() + ttl_seconds)
        return gid

    def consume(self, grant_id: str, req: InstallRequest) -> str:
        g = self._grants.get(grant_id)
        if g is None:
            raise ConsentError("CONSENT_INVALID", "unknown consent grant")
        if g.used:
            raise ConsentError("CONSENT_REPLAYED", "consent grant already used")
        if self._now() >= g.expires_at:
            raise ConsentError("CONSENT_EXPIRED", "consent grant expired")
        if g.digest != req.digest():
            raise ConsentError("CONSENT_MISMATCH", "grant does not cover this exact install")
        g.used = True
        return g.approver


Executor = Callable[[list[str], Path], subprocess.CompletedProcess]


def _default_exec(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in sanitize_environment().items() if not k.upper().startswith("PIP_")}
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_CONFIG_FILE"] = os.devnull  # no user/site pip.conf adding extra indexes
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


class InstallBroker:
    def __init__(
        self,
        store: InstallConsentStore,
        audit_log: Path,
        mirrors: dict[str, Path] | None = None,
        trusted_hosts: list[str] | None = None,
        executor: Executor = _default_exec,
    ) -> None:
        self.store = store
        self.audit_log = audit_log
        self.mirrors = {k: v.resolve() for k, v in (mirrors or {}).items()}
        self.trusted_hosts = set(
            config.pinned_registries if trusted_hosts is None else trusted_hosts
        )
        self.executor = executor

    def _audit(self, outcome: str, req: InstallRequest, **extra) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "outcome": outcome, "request": asdict(req), **extra}
        with open(self.audit_log, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _source_args(self, source: str) -> list[str]:
        kind, _, name = source.partition(":")
        if kind == "mirror":
            if name not in self.mirrors:
                raise ConsentError("UNTRUSTED_SOURCE", f"mirror '{name}' is not registered")
            return ["--no-index", "--find-links", str(self.mirrors[name])]
        if name not in self.trusted_hosts:
            raise ConsentError("UNTRUSTED_SOURCE", f"index host '{name}' is not a pinned registry")
        return ["--index-url", f"https://{name}/simple"]

    @staticmethod
    def _require_hash(req: InstallRequest) -> None:
        # A network index can serve different bytes tomorrow; only the operator's
        # own mirror may be installed unhashed.
        if req.source.startswith("index:") and req.sha256 is None:
            raise ConsentError("HASH_REQUIRED", "installs from a network index need a sha256")

    def request(self, req: InstallRequest) -> dict:
        """Agent-side: validate and describe what a human would be approving."""
        req.validate()
        self._source_args(req.source)       # reject untrusted sources before asking a human
        self._require_hash(req)
        self._audit("consent_requested", req)
        return {"status": "needsConsent", "requestDigest": req.digest(), "request": asdict(req)}

    def install(self, grant_id: str, req: InstallRequest, workspace: Path) -> dict:
        try:
            req.validate()
            src = self._source_args(req.source)
            self._require_hash(req)
            approver = self.store.consume(grant_id, req)
        except ConsentError as e:
            self._audit("denied", req, code=e.code)
            raise
        target = workspace.resolve() / ".deps"
        spec = f"{req.package}=={req.version}"
        argv = [sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--no-deps",
                "--no-input", "--target", str(target), *src]
        req_file = None
        if req.sha256:
            req_file = target.parent / f".install-{secrets.token_hex(4)}.txt"
            req_file.write_text(f"{spec} --hash=sha256:{req.sha256}\n")
            argv += ["--require-hashes", "-r", str(req_file)]
        else:
            argv.append(spec)
        try:
            proc = self.executor(argv, workspace)
        finally:
            if req_file is not None:
                req_file.unlink(missing_ok=True)
        ok = proc.returncode == 0
        self._audit("installed" if ok else "failed", req, approver=approver, exit=proc.returncode)
        return {"status": "succeeded" if ok else "failed", "exitCode": proc.returncode,
                "target": ".deps", "stderrTail": (proc.stderr or "")[-400:]}

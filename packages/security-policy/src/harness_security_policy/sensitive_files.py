"""F093 — sensitive-file policy: a standalone, authenticated-free evaluator.

Section 5 owns execution-time path enforcement (forbidden_paths, secret-file
blocking inside the sandbox). This module is the A1 lane's independent
decision point for the question "may this file be read into an agent's
context or transmitted to a model/tool?" — usable by any lane through the
HTTP adapter or by import inside packages/security-policy consumers.

Path handling is POSIX-style (repository-relative or absolute). Default
posture is fail-closed for secret-shaped files: deny, with exact-path
allowance possible via ``allow_paths`` on a custom policy.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field

from .decisions import Decision, Outcome
from .secret_scan import DEFAULT_SECRET_POLICY, SecretScanPolicy, scan_outbound

DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*id_rsa*",
    "*id_ed25519*",
    "*id_ecdsa*",
    ".netrc",
    "_netrc",
    ".npmrc",
    ".pypirc",
    ".dockercfg",
    "credentials",
    "credentials.json",
    "secrets.*",
    "kubeconfig",
)

# Substring/suffix markers that a basename glob cannot express cleanly.
DEFAULT_DENY_SUFFIXES: tuple[str, ...] = (
    "/.ssh/",
    "/.aws/credentials",
    "/.docker/config.json",
    "/.kube/config",
)

DEFAULT_DENY_BASENAMES: tuple[str, ...] = (
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    ".env",
)


def _normalize(path: str) -> str:
    path = path.strip()
    if "\\" in path:
        path = path.replace("\\", "/")
    return posixpath.normpath(path)


@dataclass(frozen=True)
class SensitiveFilePolicy:
    deny_globs: tuple[str, ...] = DEFAULT_DENY_GLOBS
    deny_suffixes: tuple[str, ...] = DEFAULT_DENY_SUFFIXES
    deny_basenames: tuple[str, ...] = (*DEFAULT_DENY_BASENAMES,)
    allow_paths: frozenset[str] = field(default_factory=frozenset)

    def is_denied(self, path: str) -> bool:
        normalized = _normalize(path)
        if not normalized or normalized in {".", "/"}:
            return False
        if normalized in self.allow_paths:
            return False
        basename = posixpath.basename(normalized)
        lowered = normalized.lower()
        if basename.lower() in {b.lower() for b in self.deny_basenames}:
            return True
        for suffix in self.deny_suffixes:
            if suffix in f"/{lowered}" or lowered.endswith(suffix.rstrip("/")):
                return True
        for glob in self.deny_globs:
            if fnmatch.fnmatch(basename.lower(), glob.lower()):
                return True
            if fnmatch.fnmatch(lowered, glob.lower()):
                return True
        return False


DEFAULT_SENSITIVE_FILE_POLICY = SensitiveFilePolicy()

_TRAVERSAL = re.compile(r"(?:^|/)\.\.(?:/|$)")


@dataclass(frozen=True)
class TransmissionDecision:
    """Combined path + content gate for one file."""

    decision: Decision
    path: str
    operation: str


def evaluate_path(
    path: str,
    *,
    operation: str = "read",
    policy: SensitiveFilePolicy = DEFAULT_SENSITIVE_FILE_POLICY,
) -> Decision:
    """Allow/deny reading, writing or transmitting ``path``.

    Traversal segments are denied outright for transmission; secret-shaped
    paths are denied for every operation (a write could exfiltrate by
    overwrite). Paths are matched by basename AND by full path so both
    ``id_rsa`` and ``deploy/secrets/prod.yaml`` style rules work.
    """
    if not isinstance(path, str) or not path.strip():
        return Decision(Outcome.DENY, "file.invalid_path", details={"path": path})
    if _TRAVERSAL.search(_normalize(path)):
        return Decision(Outcome.DENY, "file.traversal_denied", details={"path": path})
    if policy.is_denied(path):
        return Decision(
            Outcome.DENY,
            "file.sensitive_denied",
            details={"path": _normalize(path), "operation": operation},
        )
    return Decision(
        Outcome.ALLOW,
        "file.allowed",
        details={"path": _normalize(path), "operation": operation},
    )


def evaluate_transmission(
    path: str,
    content: str,
    *,
    policy: SensitiveFilePolicy = DEFAULT_SENSITIVE_FILE_POLICY,
    secret_policy: SecretScanPolicy = DEFAULT_SECRET_POLICY,
) -> TransmissionDecision:
    """Path gate first, then secret scan on the content being transmitted."""
    path_decision = evaluate_path(path, operation="transmit", policy=policy)
    if path_decision.outcome is Outcome.DENY:
        return TransmissionDecision(path_decision, path, "transmit")
    content_decision = scan_outbound(content, policy=secret_policy)
    return TransmissionDecision(content_decision, path, "transmit")

"""High-risk zone highlighting for the Review Area.

Simple, auditable path/pattern rules — NOT a learned model. Rules are
evaluated against changed file paths from the diff. This mirrors Section
5's secret-pattern approach: fnmatch-based, easy to audit, no ML.
"""

import fnmatch
from dataclasses import dataclass, field

from .models import RiskFlag, RiskSeverity


@dataclass
class RiskRule:
    name: str
    pattern: str
    severity: RiskSeverity
    reason: str


DEFAULT_RISK_RULES: list[RiskRule] = [
    RiskRule(
        name="auth_directory",
        pattern="**/auth/**",
        severity=RiskSeverity.CRITICAL,
        reason="Changes to authentication logic can break security guarantees.",
    ),
    RiskRule(
        name="auth_files",
        pattern="*auth*.py",
        severity=RiskSeverity.CRITICAL,
        reason="An authentication-related source file changed.",
    ),
    RiskRule(
        name="payment_directory",
        pattern="**/payment*/**",
        severity=RiskSeverity.CRITICAL,
        reason="Payment code changes carry financial and compliance risk.",
    ),
    RiskRule(
        name="migration_files",
        pattern="**/*migration*",
        severity=RiskSeverity.HIGH,
        reason="Database migrations can cause data loss or downtime if incorrect.",
    ),
    RiskRule(
        name="env_files",
        pattern=".env*",
        severity=RiskSeverity.CRITICAL,
        reason=(
            "Environment files may contain secrets or configuration "
            "that must not be altered by agents."
        ),
    ),
    RiskRule(
        name="secret_files",
        pattern="**/*secret*",
        severity=RiskSeverity.CRITICAL,
        reason="Files with 'secret' in the name may contain credentials.",
    ),
    RiskRule(
        name="credential_files",
        pattern="**/*credential*",
        severity=RiskSeverity.CRITICAL,
        reason="Files with 'credential' in the name may contain credentials.",
    ),
    RiskRule(
        name="token_files",
        pattern="**/*token*",
        severity=RiskSeverity.HIGH,
        reason="Files with 'token' in the name may contain access tokens.",
    ),
    RiskRule(
        name="private_keys",
        pattern="*.pem",
        severity=RiskSeverity.CRITICAL,
        reason="Private key files must never be modified by agents.",
    ),
    RiskRule(
        name="private_key_rsa",
        pattern="*id_rsa*",
        severity=RiskSeverity.CRITICAL,
        reason="SSH private key files must never be modified by agents.",
    ),
    RiskRule(
        name="route_files",
        pattern="**/routes/**",
        severity=RiskSeverity.MEDIUM,
        reason="Route definitions may introduce breaking API changes.",
    ),
    RiskRule(
        name="api_files",
        pattern="**/api/**",
        severity=RiskSeverity.MEDIUM,
        reason="API layer changes can break client contracts.",
    ),
]


@dataclass
class RiskReport:
    flags: list[RiskFlag] = field(default_factory=list)


class RiskAnalyzer:
    """Evaluates path/pattern rules against changed file paths."""

    def __init__(self, rules: list[RiskRule] | None = None) -> None:
        self.rules = rules if rules is not None else list(DEFAULT_RISK_RULES)

    def analyze(self, changed_files: list[str]) -> list[RiskFlag]:
        """Return risk flags for files matching any rule.

        A single file can match multiple rules.
        """
        flags: list[RiskFlag] = []
        seen: set[tuple[str, str]] = set()

        for file_path in changed_files:
            basename = file_path.rsplit("/", 1)[-1]
            for rule in self.rules:
                matched = _path_matches(file_path, basename, rule.pattern)
                if matched:
                    key = (file_path, rule.name)
                    if key not in seen:
                        seen.add(key)
                        flags.append(
                            RiskFlag(
                                path=file_path,
                                severity=rule.severity,
                                rule=rule.name,
                                reason=rule.reason,
                            )
                        )

        return flags


def _path_matches(file_path: str, basename: str, pattern: str) -> bool:
    """Check if a file path matches a glob pattern, handling path components."""
    if fnmatch.fnmatch(file_path, pattern):
        return True
    if fnmatch.fnmatch(basename, pattern):
        return True
    # Try matching against each directory segment
    pattern_clean = pattern.replace("**/", "").replace("/**", "").rstrip("/")
    parts = file_path.split("/")
    for part in parts:
        if fnmatch.fnmatch(part, pattern_clean):
            return True
    # Try matching the directory portion
    dir_path = "/".join(parts[:-1]) if len(parts) > 1 else ""
    if dir_path and fnmatch.fnmatch(dir_path, pattern.replace("**/", "").replace("/**", "")):
        return True
    return False


def get_risk_summary(flags: list[RiskFlag]) -> RiskSeverity:
    """Return the highest severity among the flags, or LOW if none."""
    if not flags:
        return RiskSeverity.LOW
    order = {
        RiskSeverity.CRITICAL: 4,
        RiskSeverity.HIGH: 3,
        RiskSeverity.MEDIUM: 2,
        RiskSeverity.LOW: 1,
    }
    return max(flags, key=lambda f: order[f.severity]).severity

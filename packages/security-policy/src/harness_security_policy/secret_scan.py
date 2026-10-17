"""F091 — pre-transmission secret scanning with redact/deny outcomes.

Callers run :func:`scan_outbound` immediately before text leaves the trust
boundary (model prompt, tool arguments, connector payload). Two safe
outcomes exist:

* ``REDACT`` — the payload is returned with each secret span replaced by a
  typed ``[REDACTED:<ruleId>]`` marker. Safe to transmit.
* ``DENY`` — the payload must not be transmitted at all (``text`` is None).

Default policy denies ``critical`` findings (private keys, live payment
secrets) and redacts everything else. A policy that disables redaction fails
CLOSED on any finding: silently transmitting a detected secret is never an
option.

Scanning is regex-based and intentionally conservative (specific token
prefixes, PEM blocks, labelled credential assignments). It is a transmission
gate, not a secret inventory: unknown formats can pass, so the egress and
DLP evaluators and provider-side key handling remain part of the boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .decisions import Decision, Finding, Outcome, redact_spans, strongest

# Scans are bounded by the HTTP layer (256 KiB max payload); this guard keeps
# the pure engine from doing unbounded work when used directly.
MAX_SCAN_CHARS = 1_000_000


@dataclass(frozen=True)
class SecretRule:
    rule_id: str
    severity: str
    pattern: re.Pattern[str]
    group: int = 0
    description: str = ""


_RULES: tuple[SecretRule, ...] = (
    SecretRule(
        "secret.private_key",
        "critical",
        re.compile(
            r"-----BEGIN[ A-Z]*PRIVATE KEY(?: BLOCK)?-----"
            # END marker optional: a truncated/chunked key must still be denied.
            r"(?:[\s\S]*?-----END[ A-Z]*PRIVATE KEY(?: BLOCK)?-----|[\s\S]*)"
        ),
        description="PEM private key block",
    ),
    SecretRule(
        "secret.stripe_live_key",
        "critical",
        re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"),
        description="Stripe live secret/restricted key",
    ),
    SecretRule(
        "secret.aws_access_key_id",
        "high",
        re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
        description="AWS access key id",
    ),
    SecretRule(
        "secret.github_token",
        "high",
        re.compile(
            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"
            r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
        ),
        description="GitHub token",
    ),
    SecretRule(
        "secret.slack_token",
        "high",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        description="Slack token",
    ),
    SecretRule(
        "secret.provider_api_key",
        "high",
        re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"),
        description="Provider-style secret API key",
    ),
    SecretRule(
        "secret.google_api_key",
        "high",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        description="Google API key",
    ),
    SecretRule(
        "secret.bearer_token",
        "high",
        re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+([A-Za-z0-9._~+/=-]{12,})"),
        group=1,
        description="Authorization bearer credential",
    ),
    SecretRule(
        "secret.jwt",
        "medium",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        description="JWT-shaped credential",
    ),
    SecretRule(
        "secret.credential_assignment",
        "medium",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|client[_-]?secret"
            r"|access[_-]?token|auth[_-]?token|refresh[_-]?token"
            r"|password|passwd|private[_-]?key)\b\s*[:=]\s*[\"']?"
            r"([A-Za-z0-9_\-./+=!@#$%^&*]{12,})"
        ),
        group=1,
        description="Labelled credential assignment",
    ),
)


@dataclass(frozen=True)
class SecretScanPolicy:
    """``deny_severities`` forces DENY; other findings are redacted."""

    deny_severities: frozenset[str] = frozenset({"critical"})
    redact: bool = True


DEFAULT_SECRET_POLICY = SecretScanPolicy()


def scan_text(text: str) -> tuple[Finding, ...]:
    """All rule hits, in document order. Findings never include the match."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    if len(text) > MAX_SCAN_CHARS:
        raise ValueError(f"text exceeds {MAX_SCAN_CHARS} characters")
    findings: list[Finding] = []
    for rule in _RULES:
        action = (
            Outcome.DENY
            if rule.severity in DEFAULT_SECRET_POLICY.deny_severities
            else Outcome.REDACT
        )  # noqa: E501
        for match in rule.pattern.finditer(text):
            start, end = match.span(rule.group)
            if start < 0 or start == end:
                continue
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    start=start,
                    end=end,
                    message=f"{rule.description} detected",
                    action=action,
                )
            )
    findings.sort(key=lambda f: (f.start, f.end))
    return tuple(findings)


def scan_outbound(
    text: str,
    *,
    policy: SecretScanPolicy = DEFAULT_SECRET_POLICY,
) -> Decision:
    """Decide whether ``text`` may be transmitted, redacting when possible."""
    findings = scan_text(text)
    if not findings:
        return Decision(Outcome.ALLOW, "secret.clean", details={"findingCount": 0})

    hits = tuple(
        Finding(
            rule_id=f.rule_id,
            severity=f.severity,
            start=f.start,
            end=f.end,
            message=f.message,
            action=Outcome.DENY if f.severity in policy.deny_severities else Outcome.REDACT,
        )
        for f in findings
    )
    if any(f.severity in policy.deny_severities for f in hits):
        return Decision(
            Outcome.DENY,
            "secret.denied",
            findings=hits,
            text=None,
            details={"findingCount": len(hits)},
        )
    if not policy.redact:
        # A detected secret with redaction disabled cannot be made safe.
        return Decision(
            Outcome.DENY,
            "secret.denied_no_redaction",
            findings=hits,
            text=None,
            details={"findingCount": len(hits)},
        )
    redacted = redact_spans(text, [(f.start, f.end, f.rule_id) for f in hits])
    return Decision(
        Outcome.REDACT,
        "secret.redacted",
        findings=hits,
        text=redacted,
        details={"findingCount": len(hits)},
    )


def combine(outcomes: tuple[Decision, ...]) -> Outcome:
    """Strongest outcome across several independent scanners."""
    return strongest(decision.outcome for decision in outcomes)

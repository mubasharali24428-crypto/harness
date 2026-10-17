"""F094 — outbound content DLP (data-loss prevention) rules.

Rule classes: validated payment cards (Luhn), validated IBANs (mod-97),
SSN-shaped identifiers, credentialed connection URIs (DENY by default),
plus informational-only signals (emails, internal hostnames, phone shapes)
that are reported in findings but do not alter the outcome.

Validation matters more than pattern breadth here: identifiers that fail
their checksum (Luhn, mod-97, SSN ranges) produce NO finding, so ordinary
numbers are not shredded and decisions stay explainable. Redaction replaces
validated spans with ``[REDACTED:<ruleId>]`` tokens.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .decisions import Decision, Finding, Outcome, redact_spans, strongest


def luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def iban_valid(iban: str) -> bool:
    compact = re.sub(r"\s+", "", iban).upper()
    if not 15 <= len(compact) <= 34 or not compact.isalnum():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    remainder = 0
    for char in digits:
        remainder = (remainder * 10 + int(char)) % 97
    return remainder == 1


def ssn_valid(ssn: str) -> bool:
    area, group, serial = ssn.split("-")
    if area in {"000", "666"} or area.startswith("9"):
        return False
    if group == "00" or serial == "0000":
        return False
    return True


def _confirmed(pattern: re.Pattern[str], checker: Callable[[str], bool], group: int = 0):
    def finder(text: str) -> list[tuple[int, int]]:
        hits: list[tuple[int, int]] = []
        for match in pattern.finditer(text):
            if checker(match.group(group)):
                hits.append(match.span(group))
        return hits

    return finder


@dataclass(frozen=True)
class DLPRule:
    rule_id: str
    severity: str
    action: Outcome
    finder: Callable[[str], list[tuple[int, int]]]
    message: str


def _card_spans(text: str) -> list[tuple[int, int]]:
    hits = []
    for match in re.finditer(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", text):
        digits = re.sub(r"\D", "", match.group())
        if luhn_valid(digits):
            hits.append(match.span())
    return hits


def _iban_spans(text: str) -> list[tuple[int, int]]:
    hits = []
    for match in re.finditer(
        r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}(?![A-Za-z0-9])", text
    ):  # noqa: E501
        if iban_valid(match.group()):
            hits.append(match.span())
    return hits


DEFAULT_RULES: tuple[DLPRule, ...] = (
    DLPRule(
        "dlp.credentialed_connection_string",
        "critical",
        Outcome.DENY,
        _confirmed(
            re.compile(
                r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss"
                r"|amqp|mssql|clickhouse)://[^\s:/@]+:[^\s@/]+@[^\s\"']+"
            ),
            lambda _: True,
        ),
        "Connection string carrying embedded credentials",
    ),
    DLPRule(
        "dlp.payment_card", "high", Outcome.REDACT, _card_spans, "Luhn-valid payment card number"
    ),  # noqa: E501
    DLPRule("dlp.iban", "high", Outcome.REDACT, _iban_spans, "mod-97-valid IBAN"),
    DLPRule(
        "dlp.ssn",
        "high",
        Outcome.REDACT,
        _confirmed(re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)\b"), ssn_valid),
        "SSN-shaped identifier",
    ),
    DLPRule(
        "dlp.email_address",
        "low",
        Outcome.ALLOW,
        lambda text: [
            m.span()
            for m in re.finditer(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
        ],  # noqa: E501
        "Email address (informational)",
    ),
    DLPRule(
        "dlp.internal_hostname",
        "low",
        Outcome.ALLOW,
        lambda text: [
            m.span()
            for m in re.finditer(
                r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|corp|local)\b", text
            )
        ],
        "Internal hostname (informational)",
    ),
)


@dataclass(frozen=True)
class DLPPolicy:
    rules: tuple[DLPRule, ...] = DEFAULT_RULES


DEFAULT_DLP_POLICY = DLPPolicy()


def inspect_outbound(text: str, *, policy: DLPPolicy = DEFAULT_DLP_POLICY) -> Decision:
    """Aggregate DLP findings; strongest action wins. DENY returns no text."""
    findings: list[Finding] = []
    for rule in policy.rules:
        for start, end in rule.finder(text):
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    start=start,
                    end=end,
                    message=rule.message,
                    action=rule.action,
                )
            )
    findings.sort(key=lambda f: (f.start, f.end))
    outcome = strongest(f.action for f in findings) if findings else Outcome.ALLOW
    if outcome is Outcome.DENY:
        return Decision(
            Outcome.DENY,
            "dlp.denied",
            findings=tuple(findings),
            text=None,
            details={"findingCount": len(findings)},
        )
    if outcome is Outcome.REDACT:
        redacted = redact_spans(
            text, [(f.start, f.end, f.rule_id) for f in findings if f.action is Outcome.REDACT]
        )
        return Decision(
            Outcome.REDACT,
            "dlp.redacted",
            findings=tuple(findings),
            text=redacted,
            details={"findingCount": len(findings)},
        )
    return Decision(
        Outcome.ALLOW,
        "dlp.clean" if not findings else "dlp.informational_only",
        findings=tuple(findings),
        text=text,
        details={"findingCount": len(findings)},
    )

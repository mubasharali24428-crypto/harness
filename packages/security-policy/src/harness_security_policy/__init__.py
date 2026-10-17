"""A1 outbound-security policy engine (pure, stdlib-only).

Modules map to the six S1 feature rows:

- :mod:`.secret_scan`      F091 pre-transmission secret scanning
- :mod:`.injection`        F092 prompt-injection suspicion + quarantine
- :mod:`.sensitive_files`  F093 sensitive-file policy evaluator
- :mod:`.dlp`              F094 outbound DLP rules
- :mod:`.capabilities`     F095 short-lived capability grants
- :mod:`.egress`           F096 per-run network egress policy

The section1 HTTP adapter (``/api/v1/policy``) is the supported way for other
lanes to consume these decisions; direct import is intended for tests and for
code inside this repository's A1-owned sections.
"""

from .capabilities import (
    UNSET,
    CapabilitySigner,
    InMemoryReplayStore,
    IssuedCapability,
    RequiredBinding,
    args_digest,
    derive_capability_key,
)
from .decisions import Decision, Finding, Outcome, redact_spans, strongest
from .dlp import DEFAULT_DLP_POLICY, DLPPolicy, inspect_outbound
from .egress import EgressPolicy, classify_ip, evaluate_redirect_chain, evaluate_url, host_matches
from .injection import (
    DEFAULT_INJECTION_POLICY,
    InjectionPolicy,
    analyze,
    frame_untrusted,
    inspect,
    suspicion_score,
)
from .secret_scan import DEFAULT_SECRET_POLICY, SecretScanPolicy, scan_outbound, scan_text
from .sensitive_files import (
    DEFAULT_SENSITIVE_FILE_POLICY,
    SensitiveFilePolicy,
    evaluate_path,
    evaluate_transmission,
)

__all__ = [
    "UNSET",
    "CapabilitySigner",
    "DEFAULT_DLP_POLICY",
    "DEFAULT_INJECTION_POLICY",
    "DEFAULT_SECRET_POLICY",
    "DEFAULT_SENSITIVE_FILE_POLICY",
    "DLPPolicy",
    "Decision",
    "EgressPolicy",
    "Finding",
    "InMemoryReplayStore",
    "InjectionPolicy",
    "IssuedCapability",
    "Outcome",
    "RequiredBinding",
    "SecretScanPolicy",
    "SensitiveFilePolicy",
    "analyze",
    "args_digest",
    "classify_ip",
    "derive_capability_key",
    "evaluate_path",
    "evaluate_redirect_chain",
    "evaluate_transmission",
    "evaluate_url",
    "frame_untrusted",
    "host_matches",
    "inspect",
    "inspect_outbound",
    "redact_spans",
    "scan_outbound",
    "scan_text",
    "strongest",
    "suspicion_score",
]

"""The host half of Hx: check, authorise, run, record.

The checker (``harness_hx``) never executes, and that stays true: ``hxc`` only
checks. This package is the other half of the product claim — a *workflow
driver* for the construct set the checked language allows, with the host in
charge of authority, limits and evidence.

It is deliberately not a general-purpose runtime:

* it cannot name an operation outside ``harness_hx.capabilities.HOST_OPERATIONS``;
* it reaches the outside world only through an injected :class:`HostBridge`;
* it asks the host's :class:`~harness_hx_host.authority.Decision` at every call;
* it stops on the declared budget and records every crossing.

Public surface::

    from harness_hx_host import FakeHostBridge, authorize, run_source

    outcome = run_source(source, bridge=FakeHostBridge(), granted=["repo.read"])
    outcome.status, outcome.output, outcome.evidence()
"""

from __future__ import annotations

from .authority import (
    Decision,
    Denial,
    RevisableDecision,
    Revision,
    as_revisable,
    authority_problems,
    authorize,
    source_digest,
)
from .boundary import SchemaValue, validate
from .bridge import (
    RECORDING_KIND,
    BridgeCall,
    BridgeExchange,
    ConfinedCommandRunner,
    ConfinedReader,
    FakeHostBridge,
    HostBridge,
    ReadOnlyRepoBridge,
    RecordingBridge,
    ReplayBridge,
    SandboxHostBridge,
    recording_from_json,
    recording_to_json,
)
from .contract_runner import ContractRunner
from .errors import (
    STATUSES,
    BadPriceTable,
    BudgetExhausted,
    Cancelled,
    CapabilityDenied,
    CheckRefused,
    CostUnpriceable,
    DigestMismatch,
    HostUnavailable,
    HxHostError,
    InvalidHostResult,
    NotRunnable,
    RecordingMismatch,
    ScriptError,
    StaleAuthority,
)
from .model_relay import AgentPolicy, ModelRelay
from .pricing import Pricing, PricingError
from .run import RunResult, digest_of, inputs_digest, run_source
from .runtime import Budget, Runtime
from .secrets import REDACTED, Tally, find, scrub, scrub_text, scrub_value

__all__ = [
    "REDACTED",
    "STATUSES",
    "AgentPolicy",
    "BadPriceTable",
    "BridgeCall",
    "BridgeExchange",
    "Budget",
    "BudgetExhausted",
    "Cancelled",
    "CapabilityDenied",
    "CheckRefused",
    "ConfinedCommandRunner",
    "ConfinedReader",
    "ContractRunner",
    "CostUnpriceable",
    "Decision",
    "Denial",
    "DigestMismatch",
    "FakeHostBridge",
    "HostBridge",
    "HostUnavailable",
    "HxHostError",
    "InvalidHostResult",
    "ModelRelay",
    "NotRunnable",
    "Pricing",
    "PricingError",
    "RECORDING_KIND",
    "ReadOnlyRepoBridge",
    "RecordingBridge",
    "RecordingMismatch",
    "ReplayBridge",
    "RevisableDecision",
    "Revision",
    "RunResult",
    "Runtime",
    "SandboxHostBridge",
    "SchemaValue",
    "ScriptError",
    "StaleAuthority",
    "Tally",
    "as_revisable",
    "authority_problems",
    "authorize",
    "digest_of",
    "find",
    "inputs_digest",
    "run_source",
    "scrub",
    "scrub_text",
    "scrub_value",
    "recording_from_json",
    "recording_to_json",
    "source_digest",
    "validate",
]

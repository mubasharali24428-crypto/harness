"""Mission 11 wave 1 node A3 — team-attributed cost_ledger writer (S1's slice).

M17-B3: this module is now a thin re-export shim over the canonical
implementation in ``harness_contracts.cost_ledger`` (promoted verbatim so
Section 4's metering hook stops importing harness_identity via runtime
sys.path injection). Kept for Section 1's internal callers and tests.
"""

from __future__ import annotations

from harness_contracts.cost_ledger import (
    CostOutOfRangeError,
    UsageRecord,
    _to_money,
    record_usage,
    usage_bind_params,
)

__all__ = ["CostOutOfRangeError", "UsageRecord", "_to_money", "record_usage", "usage_bind_params"]

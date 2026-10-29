"""Compatibility shim: the rate card now lives in the contract package.

W15 promoted it so engine metering (Section 4) and the router's own quoting
(Section 3) resolve a model's price through ONE implementation — the ledger had
been booking operator-card models at the catalog fallback. Sections may not
import each other, so the canonical copy lives in ``harness_contracts`` and this
module re-exports it; Section 3's call sites and tests stay unchanged. The
active-card singleton lives in the contract module, so ``set_active`` here
affects both sections (one process, one card).
"""

from __future__ import annotations

from harness_contracts.rate_card import (
    RATE_CARD_ENV,
    RateCard,
    RateCardError,
    active,
    cost_of,
    load_overlay,
    overlay_pricing,
    parse_overlay,
    set_active,
)

__all__ = [
    "RATE_CARD_ENV",
    "RateCard",
    "RateCardError",
    "active",
    "cost_of",
    "load_overlay",
    "overlay_pricing",
    "parse_overlay",
    "set_active",
]

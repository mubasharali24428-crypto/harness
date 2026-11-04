"""What a crossing costs: the host-supplied price table behind ``budget.cost``.

``budget.cost`` is a *ceiling the program declares*; it is not a price list, and
a program may not name prices — that would let the source decide what its own
effects cost, which is the failure mode the whole authority model exists to
prevent. So prices arrive from the host, exactly like grants do:
``run_source(..., prices={...})``.

Three states, and they mean different things to a reviewer:

* **no table supplied** — cost stays what it was before M6: declared, reported,
  not enforced. The evidence says so in a field a reader cannot miss
  (``enforcement: "declared-not-enforced"``), so an unpriced run is never
  mistaken for a bounded one.
* **table supplied** — every effect crossing is priced or the crossing is
  refused (``HXH502``). A declared budget with a hole in the price table is not
  a budget; running the unpriced part for free is how a ceiling becomes fiction.
* **table supplied and the run exceeds it** — ``HXH501`` at the call site that
  would have crossed, before the effect happens.

Currency is USD, fixed: the checker already refuses any other currency
(``HX5004``), so accepting another here would be a second, weaker rule.

Amounts are ``Decimal`` because this is money: ``2.00`` must not become
``1.9999999999999998`` on its way through a binary float and a budget check.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

CURRENCY = "USD"
#: Prices are compared against a money literal the checker bounded to 2 decimals;
#: 4 places lets a per-call price be a fraction of a cent without float drift.
_QUANTUM = Decimal("0.0001")


class PricingError(ValueError):
    """The table itself is malformed: a bad key, a bad amount, a bad currency."""


@dataclass(frozen=True)
class Pricing:
    """Operation name -> price in USD. Frozen: a run's price list cannot drift."""

    prices: dict[str, Decimal]

    @classmethod
    def from_mapping(cls, table: dict[str, Any] | None) -> Pricing:
        """Accept JSON-shaped input (strings preferred) and make it exact.

        Strings are the honest spelling for money in JSON; numbers are allowed so
        a host can write ``0.001`` without thinking about float representation —
        the value is re-quantised from its decimal *text*, which is what keeps
        ``0.1 + 0.2`` from ever being compared as a float.
        """
        if not table:
            raise PricingError("a price table needs at least one operation")
        prices: dict[str, Decimal] = {}
        for key, value in table.items():
            if not isinstance(key, str) or not key.strip():
                raise PricingError(f"price keys must be non-empty strings, got {key!r}")
            prices[key] = cls._amount(key, value)
        return cls(prices=prices)

    @staticmethod
    def _amount(name: str, value: Any) -> Decimal:
        if isinstance(value, bool) or value is None:
            raise PricingError(f"'{name}' price must be a number or a numeric string")
        try:
            amount = Decimal(str(value))
        except InvalidOperation as error:  # pragma: no cover - defensive
            raise PricingError(f"'{name}' price is not a number: {value!r}") from error
        if not amount.is_finite():
            raise PricingError(f"'{name}' price is not finite: {value!r}")
        if amount < 0:
            raise PricingError(f"'{name}' price is negative: {value!r}")
        return amount.quantize(_QUANTUM)

    def price_for(self, operation: str, capability: str | None = None) -> Decimal | None:
        """The price of a crossing: its own name first, then its capability.

        A table may price ``developer.plan`` (one agent operation) or
        ``model.invoke`` (every model crossing). Both are honest keys: the first
        says "this call", the second says "this kind of effect". An exact name
        always wins, so a host can set a capability floor and still price one
        operation differently without a second mechanism.
        """
        price = self.prices.get(operation)
        if price is None and capability is not None:
            price = self.prices.get(capability)
        return price

    def digest(self) -> str:
        """A stable fingerprint of the table, recorded so a replay can prove it."""
        canonical = json.dumps(
            {name: str(price) for name, price in sorted(self.prices.items())},
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {
            "currency": CURRENCY,
            "sha256": self.digest(),
            "operations": {name: str(price) for name, price in sorted(self.prices.items())},
        }


class CostAccount:
    """The running spend against a declared ceiling, in USD.

    Kept separate from ``Budget`` because the two fail differently: steps, tool
    calls, tasks and duration are counters that a run outgrows, while cost is a
    *reservation* — it is checked against the next price before the effect, so a
    run that cannot pay stops before it produces an unbillable side effect.
    """

    def __init__(self, limit: Decimal | None, pricing: Pricing | None) -> None:
        self.limit = limit
        self.pricing = pricing
        self.used = Decimal("0")
        # Spawned effects charge from worker threads, so "is this crossing
        # affordable?" and "what has it cost?" must be one decision — otherwise two
        # parallel calls can each pass the check and together break the ceiling.
        self._lock = threading.Lock()

    @property
    def enforced(self) -> bool:
        """Enforcement needs both halves: a declared ceiling and a price list."""
        return self.limit is not None and self.pricing is not None

    @property
    def mode(self) -> str:
        if self.limit is None:
            return "undeclared"
        return "enforced" if self.pricing is not None else "declared-not-enforced"

    def reserve(self, operation: str, capability: str | None = None) -> Decimal:
        """The price of this crossing, having proved the run can still afford it.

        Raises ``CostUnpriceable`` when a budget exists but the table has a hole,
        and ``BudgetExhausted`` when the crossing would cross the ceiling — both
        before any effect, so a stopped run never owes money it cannot show.
        """
        from .errors import BudgetExhausted, CostUnpriceable

        if self.limit is None:
            return Decimal("0")
        if self.pricing is None:  # pragma: no cover - callers gate on `mode`
            return Decimal("0")
        with self._lock:
            price = self.pricing.price_for(operation, capability)
            if price is None:
                keys = [operation] + ([capability] if capability else [])
                raise CostUnpriceable(
                    f"{'/'.join(keys)} has no price in the host's price table, so "
                    f"budget.cost ({self.limit} {CURRENCY}) cannot be honoured",
                    detail={
                        "limit": "cost",
                        "operation": operation,
                        "capability": capability,
                        "tableSha256": self.pricing.digest(),
                        "priced": sorted(self.pricing.prices),
                    },
                )
            if self.used + price > self.limit:
                raise BudgetExhausted(
                    f"budget.cost ({self.limit} {CURRENCY}) exhausted: {self.used} used, "
                    f"'{operation}' would cost {price}",
                    detail={
                        "limit": "cost",
                        "operation": operation,
                        "used": str(self.used),
                        "price": str(price),
                        "declared": str(self.limit),
                        "currency": CURRENCY,
                    },
                )
            self.used += price
            return price

    def settle(self, price: Decimal) -> None:
        """No-op under the reservation model: ``reserve`` already took the money.

        Kept as a named call so a later actual-based model (Section 3's ledger)
        has an obvious place to reconcile a difference without re-opening the
        accounting to callers.
        """

    def to_json(self) -> dict[str, Any]:
        with self._lock:
            used = self.used
        return {
            "mode": self.mode,
            "currency": CURRENCY,
            "declared": str(self.limit) if self.limit is not None else None,
            "used": str(used),
            "enforced": self.enforced,
        }

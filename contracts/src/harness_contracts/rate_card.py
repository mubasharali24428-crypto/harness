"""Operator rate card — the built-in price list plus a deployment overlay.

W15: this lived in Section 3, which meant the engine-metering path in Section 4
priced a run from the frozen catalog only. A model priced solely by an
operator card was therefore quoted one rate by the router and booked at the
invented catalog fallback by the ledger. It lives in the contract package so
ONE resolution order answers for both (Section 3 keeps a re-export shim).

The frozen contract catalog (`harness_contracts.pricing.MODEL_PRICING_CATALOG`)
covers the models the harness ships with. A deployment that routes to anything
else can add prices without editing contracts:

    HARNESS_RATE_CARD_JSON='{"models": {"openai/gpt-5": {"input": 1.25, "output": 10}}}'
    HARNESS_RATE_CARD_JSON=/etc/harness/rate-card.json

Semantics (W14):

* ``source`` is ``catalog``, ``operator`` or ``unpriced``. An unpriced model
  still bills zero (never an invented rate), but the caller can SEE that the
  zero means "unknown", not "free" — ``/estimate-cost`` reports ``priced`` and
  ``pricingSource`` and the call path logs a warning.
* A malformed card fails loudly at load (``RateCardError``): a typo must not
  silently turn into free inference.
* Zero-priced catalog entries (``local/*``) stay ``catalog``: they are priced at
  zero on purpose, which is different from unpriced.
* Money math is the frozen formula itself: ``PricingEngine.calculate_cost``
  resolves through this card, and a test pins parity for every catalog model.
* An unpriced model is still two different answers on purpose: the ROUTER bills
  0 (never an invented rate) while engine metering falls back to
  ``PricingEngine``'s bounded estimate, because a runaway agent run with no row
  is worse than a rough one. The fallback is logged, naming the model.
"""

from __future__ import annotations

import json
import logging
import math
import os
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from .pricing import MODEL_PRICING_CATALOG, ModelPricing

logger = logging.getLogger(__name__)

RATE_CARD_ENV = "HARNESS_RATE_CARD_JSON"
_MONEY_Q = Decimal("0.000001")  # NUMERIC(12,6), same as the contract engine


class RateCardError(ValueError):
    """The operator-supplied rate card is malformed."""


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value))


def cost_of(pricing: ModelPricing, input_tokens: int, output_tokens: int) -> Decimal:
    """Exact NUMERIC(12,6) cost for one call at the given pricing.

    Byte-for-byte the frozen ``PricingEngine.calculate_cost`` formula; the
    parity test enumerates the whole catalog to keep it that way.
    """
    input_cost = _dec(pricing.input_cost_per_million) * int(input_tokens)
    output_cost = _dec(pricing.output_cost_per_million) * int(output_tokens)
    total = (input_cost + output_cost) / Decimal(1_000_000)
    return total.quantize(_MONEY_Q, rounding=ROUND_HALF_EVEN)


def _parse_entry(model: str, entry: Any) -> ModelPricing:
    if not isinstance(entry, dict):
        raise RateCardError(f"{model!r}: pricing entry must be an object")
    missing = {"input", "output"} - set(entry)
    if missing:
        raise RateCardError(f"{model!r}: missing {sorted(missing)}")
    values: dict[str, float] = {}
    for key in ("input", "output"):
        raw = entry[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise RateCardError(f"{model!r}: {key} must be a number")
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise RateCardError(f"{model!r}: {key} must be a finite, non-negative number")
        values[key] = value
    return ModelPricing(
        input_cost_per_million=values["input"],
        output_cost_per_million=values["output"],
    )


def parse_overlay(data: Any) -> dict[str, ModelPricing]:
    """Validate the decoded card. Accepts ``{"models": {...}}`` or a flat map."""
    if not isinstance(data, dict):
        raise RateCardError("rate card must be a JSON object")
    body = data.get("models", data)
    if not isinstance(body, dict):
        raise RateCardError("rate card 'models' must be an object")
    overlay: dict[str, ModelPricing] = {}
    for model, entry in body.items():
        if not isinstance(model, str) or not model.strip():
            raise RateCardError("rate card model ids must be non-empty strings")
        overlay[model.strip()] = _parse_entry(model, entry)
    return overlay


def load_overlay(raw: str) -> dict[str, ModelPricing]:
    """Load from inline JSON or a file path; empty/unset means no overlay."""
    if not raw or not raw.strip():
        return {}
    text = raw.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise RateCardError(f"inline rate card is not valid JSON: {exc}") from exc
    else:
        path = Path(text)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RateCardError(f"rate card file {text!r} unreadable: {exc}") from exc
        except ValueError as exc:
            raise RateCardError(f"rate card file {text!r} is not valid JSON: {exc}") from exc
    return parse_overlay(data)


class RateCard:
    """Catalog + overlay lookups with an explicit source for every answer."""

    def __init__(self, overlay: dict[str, ModelPricing] | None = None) -> None:
        self._overlay = dict(overlay or {})

    @property
    def overlay_size(self) -> int:
        return len(self._overlay)

    def pricing(self, model: str) -> ModelPricing | None:
        if model in self._overlay:
            return self._overlay[model]
        return MODEL_PRICING_CATALOG.get(model)

    def overlay_pricing(self, model: str) -> ModelPricing | None:
        """The OPERATOR's price for a model, or None.

        Deliberately overlay-only: ``PricingEngine`` calls this before the
        catalog so a card entry wins, exactly like :meth:`pricing` — but the
        catalog branch stays the catalog's own answer, without a second lookup.
        """
        return self._overlay.get(model)

    def source(self, model: str) -> str:
        if model in self._overlay:
            return "operator"
        if model in MODEL_PRICING_CATALOG:
            return "catalog"
        return "unpriced"

    def priced(self, model: str) -> bool:
        return self.source(model) != "unpriced"

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        pricing = self.pricing(model)
        if pricing is None:
            logger.warning(
                "model %r has no rate (catalog or operator card); billing 0 for this call",
                model,
            )
            return Decimal("0")
        return cost_of(pricing, input_tokens, output_tokens)


_ACTIVE: RateCard | None = None


def active() -> RateCard:
    """The deployment's card, built once from the environment."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = RateCard(load_overlay(os.environ.get(RATE_CARD_ENV, "")))
        if _ACTIVE.overlay_size:
            logger.info(
                "operator rate card loaded: %d model(s) priced", _ACTIVE.overlay_size
            )
    return _ACTIVE


def overlay_pricing(model: str) -> ModelPricing | None:
    """The deployment's operator price for ``model``, or None (catalog/fallback)."""
    try:
        return active().overlay_pricing(model)
    except RateCardError:
        # A malformed card must stay loud at load (/estimate-cost raises). The
        # pricing engine's own call path is not the place to fail a run, so it
        # degrades to "no overlay" here and the error is re-raised by active().
        logger.warning("operator rate card is malformed; pricing from the catalog only")
        return None


def set_active(card: RateCard | None) -> None:
    """Test/deployment seam: replace the active card (None reloads from env)."""
    global _ACTIVE
    _ACTIVE = card

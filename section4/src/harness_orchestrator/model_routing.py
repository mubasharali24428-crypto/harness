"""M2-W14: opt-in model routing, resolved when a run's execution model is chosen.

The engine takes ONE model per session, so routing here happens at run
granularity: a plan is classified from its shape before execution starts.

Rules (all testable, none silent):

* **Opt-in.** A team's ``model_routing`` document must say ``enabled: true``;
  anything missing, disabled, corrupt or empty resolves to the team's configured
  primary model — the status quo, never a surprise switch.
* **No forced downgrade.** If routing is enabled but the model for the chosen
  class is absent or blank, the resolver returns the configured primary model.
  It never falls back to the *other* class (a missing reasoning model must not
  silently run the cheap one).
* **Conservative classification.** Few steps and few files is mechanical;
  everything else (including an empty or unparseable plan) is reasoning.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MECHANICAL = "mechanical"
REASONING = "reasoning"
MECHANICAL_MAX_STEPS = 3
MECHANICAL_MAX_FILES = 3


def classify_plan(step_count: int, file_count: int) -> str:
    """Mechanical only when the plan is genuinely small; everything else is reasoning."""
    steps = max(int(step_count), 0)
    files = max(int(file_count), 0)
    if steps <= MECHANICAL_MAX_STEPS and files <= MECHANICAL_MAX_FILES:
        return MECHANICAL
    return REASONING


def parse_policy(raw: Any) -> dict[str, str] | None:
    """The enabled policy's model map, or None when routing is off/unusable.

    Lenient by design (the reader must never break execution): a corrupt row, a
    missing ``enabled`` flag, a disabled policy or a policy with no usable model
    all mean "route nothing".
    """
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            document = json.loads(raw)
        except ValueError:
            return None
    else:
        document = raw
    if not isinstance(document, dict) or document.get("enabled") is not True:
        return None
    models: dict[str, str] = {}
    for key in ("mechanicalModel", "reasoningModel"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            models[key] = value.strip()
    return models or None


def resolve(
    primary: str | None, policy: dict[str, str] | None, plan_class: str
) -> tuple[str | None, dict[str, Any]]:
    """The model to run and the decision record that explains it."""
    decision: dict[str, Any] = {
        "enabled": policy is not None,
        "class": plan_class,
        "model": primary,
        "source": "primary",
    }
    if policy is None:
        return primary, decision
    key = "mechanicalModel" if plan_class == MECHANICAL else "reasoningModel"
    chosen = policy.get(key)
    if not chosen:
        decision["source"] = "policy-fallback"
        decision["reason"] = f"no {key} configured; keeping the configured model"
        return primary, decision
    decision["model"] = chosen
    decision["source"] = "policy"
    return chosen, decision

"""Explicit engine live-mode configuration — M11 Wave 1, lane W1-B1.

Replaces ``live_mode = bool(api_key and model)`` (finding C-004): an engine
mode is now an explicit, validated choice, never inferred from whatever
happens to sit in the environment. Two modes exist and nothing else:

* ``live`` — every run goes through the DeepSeek Harness SDK using exactly
  the configured model. Requires a real ``DEEPSEEK_API_KEY``, a
  ``DSH_MODEL``, and a well-formed ``DEEPSEEK_BASE_URL`` (all three; a
  missing one is a loud ``EngineConfigError`` naming the variable).
* ``dry``  — no model credentials are consulted; runs answer with the
  honest "not configured" stub. Requires nothing.

Selection order (first match wins):

1. explicit ``live_mode=`` argument (programmatic construction),
2. ``ENGINE_LIVE_MODE`` environment variable (deployment config),
3. ``dry`` — the absence of an explicit decision is a dry engine, not a
   live one. Deploy-time boot (``deploy/runners/engine_boot.py``) is
   stricter: it passes ``require_explicit_mode=True`` so an UNSET
   ``ENGINE_LIVE_MODE`` fails loudly instead of guessing dry.

Placeholder credentials (the historic compose default literal, plus
``changeme``, blank strings, …) NEVER count as live configuration:
:func:`derive_live_mode` maps them to ``DRY`` (never ``LIVE``), while an
EXPLICIT ``ENGINE_LIVE_MODE=live`` request with a placeholder raises
:class:`EngineConfigError` naming the offending variable. Error messages
carry environment variable NAMES, never secret VALUES.

The forced-downgrade ladder this module supersedes was removed per
AGENTS.md law ("No forced model downgrade"): the configured model is the
only model that runs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

ENGINE_LIVE_MODE_ENV = "ENGINE_LIVE_MODE"
API_KEY_ENV = "DEEPSEEK_API_KEY"
BASE_URL_ENV = "DEEPSEEK_BASE_URL"
MODEL_ENV = "DSH_MODEL"

#: Environment variables required (non-empty, non-placeholder) for live mode.
REQUIRED_LIVE_ENV_VARS = (API_KEY_ENV, BASE_URL_ENV, MODEL_ENV)


class LiveMode(StrEnum):
    """The complete set of engine modes. There is no third option."""

    LIVE = "live"
    DRY = "dry"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class EngineConfigError(RuntimeError):
    """Raised when engine configuration is missing, degenerate, or invalid.

    Messages name the offending environment VARIABLES; they never echo
    credential values (boot-log secrecy rule, brief W1-B1).
    """


# Literal placeholder values that must never authenticate an engine. The
# historic compose default "ollama" is the reason this list exists.
_PLACEHOLDER_VALUES = frozenset(
    {
        "ollama",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "your-api-key",
        "your_api_key",
        "your-key",
        "your_key",
        "dev-master-key-change-me-in-prod!",
        "todo",
        "fixme",
        "dummy",
        "fake",
        "sample",
        "example",
        "test",
        "none",
        "null",
        "unset",
        "undefined",
        "xxx",
        "xxx.xxx.xxx",
        "<api_key>",
        "${deepseek_api_key}",
    }
)

# Substrings that mark a value as a template rather than a credential.
_PLACEHOLDER_SUBSTRINGS = (
    "changeme",
    "change-me",
    "change_me",
    "your-api-key",
    "your_api_key",
    "<",
    ">",
    "${",
)


def is_placeholder(value: str | None) -> bool:
    """True when *value* looks like a placeholder rather than a real secret.

    Blank/whitespace counts as a placeholder. Detection is deliberately
    conservative: a value is only rejected on an exact placeholder-list hit
    or an obvious template marker, so real credentials never trip it.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if stripped.lower() in _PLACEHOLDER_VALUES:
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_SUBSTRINGS)


def parse_live_mode(raw: str | None) -> LiveMode:
    """Parse an explicit ENGINE_LIVE_MODE value; None/blank means dry.

    Raises EngineConfigError on anything that is not {live, dry}
    (case-insensitive, surrounding whitespace tolerated) — a typoed mode
    must fail loudly, not silently become dry.
    """
    if raw is None or not raw.strip():
        return LiveMode.DRY
    normalized = raw.strip().lower()
    try:
        return LiveMode(normalized)
    except ValueError:
        allowed = ", ".join(m.value for m in LiveMode)
        raise EngineConfigError(
            f"{ENGINE_LIVE_MODE_ENV}={raw.strip()!r} is invalid; "
            f"allowed values: {allowed}. "
            f"Set it via the {ENGINE_LIVE_MODE_ENV} environment variable "
            f"(deployment) or the live_mode= argument (programmatic)."
        ) from None


def resolve_live_mode(environ: Mapping[str, str] | None = None) -> LiveMode:
    """Resolve the engine mode from the environment (default: os.environ)."""
    env = os.environ if environ is None else environ
    raw = env.get(ENGINE_LIVE_MODE_ENV)
    # An empty compose default ("${VAR:-}") behaves like an unset variable.
    if raw is not None and not raw.strip():
        raw = None
    return parse_live_mode(raw)


def _require_present(name: str, value: str | None) -> str:
    """Return the validated non-placeholder value or raise naming *name*."""
    if is_placeholder(value):
        raise EngineConfigError(
            f"{name} is missing, blank, or a placeholder value; "
            f"set it to a real credential/configuration before running "
            f"with {ENGINE_LIVE_MODE_ENV}=live"
        )
    assert value is not None  # narrowed by is_placeholder
    return value.strip()


def _validate_base_url(value: str | None) -> str | None:
    """Require and validate DEEPSEEK_BASE_URL for live mode.

    A missing URL is a loud failure naming the VARIABLE (boot integrity:
    never guess an endpoint); anything set must be a real http(s) URL —
    the historic placeholder literal or a scheme-less host is exactly
    the degenerate config C-004 describes.
    """
    if is_placeholder(value):
        raise EngineConfigError(
            f"{BASE_URL_ENV} is required for {ENGINE_LIVE_MODE_ENV}=live; "
            f"set it to an http(s):// endpoint URL (or run with "
            f"{ENGINE_LIVE_MODE_ENV}=dry)"
        )
    assert value is not None  # narrowed by is_placeholder
    stripped = value.strip()
    lowered = stripped.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise EngineConfigError(
            f"{BASE_URL_ENV} must be an http(s):// URL for OpenAI-compatible "
            f"endpoints (got a non-URL value); fix it or run with "
            f"{ENGINE_LIVE_MODE_ENV}=dry"
        )
    return stripped


def validate_live_credentials(
    *,
    api_key: str | None,
    model: str | None,
    base_url: str | None = None,
) -> dict[str, str | None]:
    """Validate a live configuration supplied as explicit values.

    All three variables are required (key, model, base URL). Returns a
    sanitized description (variable NAMES and the non-secret
    model/base-url); raises EngineConfigError naming the first offender.
    Never includes the API key in the return value or any error message.
    """
    _require_present(API_KEY_ENV, api_key)
    model_value = _require_present(MODEL_ENV, model)
    url_value = _validate_base_url(base_url)
    return {"model": model_value, "base_url": url_value}


def derive_live_mode(
    *,
    api_key: str | None,
    model: str | None,
    base_url: str | None = None,
) -> LiveMode:
    """Derive the explicit mode from configuration VALUES (C-004 law).

    A placeholder credential (the historic compose default literal,
    ``changeme``, blank/None, …) derives ``DRY`` — NEVER ``LIVE``. Any
    non-placeholder configuration must fully validate (real key + model
    + http(s) base URL) or the failure names the offending VARIABLE.
    """
    if is_placeholder(api_key):
        return LiveMode.DRY
    validate_live_credentials(api_key=api_key, model=model, base_url=base_url)
    return LiveMode.LIVE


def validate_engine_env(
    environ: Mapping[str, str] | None = None,
    *,
    require_explicit_mode: bool = False,
) -> dict[str, object]:
    """Validate the engine environment; raise or return a sanitized summary.

    This is the single source of truth shared by the engine wrapper
    (construction-time validation) and ``deploy/runners/engine_boot.py``
    (deploy-time validate-or-exit). The returned summary carries the mode,
    the configured model name, and WHICH variables were provided — never
    any secret value.

    With ``require_explicit_mode=True`` (boot policy) an UNSET or blank
    ``ENGINE_LIVE_MODE`` is a loud EngineConfigError naming the variable
    — a deploy must DECIDE, never drift into a guessed mode.
    """
    env = os.environ if environ is None else environ
    raw_mode = env.get(ENGINE_LIVE_MODE_ENV)
    if require_explicit_mode and (raw_mode is None or not raw_mode.strip()):
        degenerate = [
            name for name in REQUIRED_LIVE_ENV_VARS if is_placeholder(env.get(name))
        ]
        detail = (
            " Variables not usable for live mode: " + ", ".join(degenerate) + "."
            if degenerate
            else ""
        )
        raise EngineConfigError(
            f"{ENGINE_LIVE_MODE_ENV} is not set; the engine refuses to guess a "
            f"mode. Set {ENGINE_LIVE_MODE_ENV}=dry to run without model "
            f"credentials, or {ENGINE_LIVE_MODE_ENV}=live together with the "
            f"required variables."
            + detail
        )
    mode = resolve_live_mode(env)
    summary: dict[str, object] = {
        "mode": mode,
        "provided": [name for name in (API_KEY_ENV, BASE_URL_ENV, MODEL_ENV) if env.get(name)],
    }
    if mode is LiveMode.LIVE:
        validated = validate_live_credentials(
            api_key=env.get(API_KEY_ENV),
            model=env.get(MODEL_ENV),
            base_url=env.get(BASE_URL_ENV),
        )
        summary["model"] = validated["model"]
        summary["base_url"] = validated["base_url"]
    return summary

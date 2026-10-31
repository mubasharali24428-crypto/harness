"""W23: first-run readiness — one checked entry per thing a run needs.

``/ready`` answers "may this instance take traffic" (database and schema). This
answers "will a run started now work, and if not, which part is wrong":
provider, model, worker (reachable *and* accepting our token), revision (the
daemon runs the same orchestrator code as this process), repository and the
caller's permissions. The W20 outage — ``/ready`` green while the daemon
answered ``401`` to every dispatch (ops runbook §3.11) — is the case it exists for.

Every entry is ``ok``, ``fail`` or ``unknown``, names its component and never
carries a secret: variables are named, tokens and keys are never echoed. The
provider is checked from configuration only, including the request format the
engine will send (W23 F2); nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from harness_contracts.roles import Principal, can

from harness_orchestrator.live_mode import (
    API_KEY_ENV,
    BASE_URL_ENV,
    MODEL_ENV,
    EngineConfigError,
    LiveMode,
    resolve_live_mode,
    validate_live_credentials,
)

OK, FAIL, UNKNOWN = "ok", "fail", "unknown"
_PACKAGE = Path(__file__).resolve().parent
PATCH_ENV = "DSH_PATCH_FILES"


def _entry(check: str, status: str, component: str, reason: str, detail: str) -> dict[str, str]:
    return {"check": check, "status": status, "component": component, "reason": reason,
            "detail": detail}


@lru_cache(maxsize=1)
def code_digest() -> str:
    """SHA-256 over this package's files (path and bytes), bytecode excluded.

    The orchestrator and the worker daemon both install this package, so equal
    digests mean equal code. Measured equal across both images at one revision.
    """
    digest = hashlib.sha256()
    for path in sorted(_PACKAGE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        digest.update(path.relative_to(_PACKAGE).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def _wire_format(env: Mapping[str, str], base_url: str,
                 exists: Callable[[str], bool]) -> tuple[str | None, str]:
    """W23 F2: (failure reason or None, detail) for the request format the engine sends.

    Unpatched, the engine's only route speaks the Anthropic Messages API
    (engine.DEFAULT_DSH_PROVIDER); an OpenAI-compatible gateway rejects it with
    "Invalid Anthropic Messages API request" on the first run. A reviewed patch
    file re-points it, and the engine refuses to start when one is missing.
    """
    patches = [p for p in (env.get(PATCH_ENV) or "").split(os.pathsep) if p]
    missing = [p for p in patches if not exists(p)]
    if missing:
        return "PATCH_FILE_MISSING", (f"{PATCH_ENV} names {missing[0]}, which does not exist; "
                                      "the engine refuses to start")
    if patches:
        return None, f"request format set by {PATCH_ENV}"
    parts = urllib.parse.urlsplit(base_url)
    # ponytail: path heuristic. OpenAI-compatible gateways (OpenRouter, Ollama, LiteLLM, vLLM)
    # serve under /v1, Anthropic-format hosts do not. Only a model call proves it (paid, not run).
    if parts.path.rstrip("/").endswith("/v1"):
        return "PROVIDER_FORMAT_MISMATCH", (
            f"unpatched, the engine sends Anthropic Messages API requests, but {parts.hostname}"
            f"{parts.path} looks OpenAI-compatible; set {PATCH_ENV} "
            "(deploy/patches/openrouter.patch.yml)")
    return None, "Anthropic Messages API (no patch)"


def check_engine(env: Mapping[str, str],
                 exists: Callable[[str], bool] = os.path.exists) -> list[dict[str, str]]:
    """``provider`` and ``model`` from the engine's validated configuration."""
    try:
        mode = resolve_live_mode(env)
        if mode is LiveMode.DRY:
            return [
                _entry("provider", FAIL, "provider", "ENGINE_DRY",
                       "ENGINE_LIVE_MODE is dry: runs answer 'not configured' and call no model"),
                _entry("model", UNKNOWN, "model", "NOT_USED", "no model is used in dry mode"),
            ]
        live = validate_live_credentials(api_key=env.get(API_KEY_ENV), model=env.get(MODEL_ENV),
                                         base_url=env.get(BASE_URL_ENV))
    except EngineConfigError as exc:  # messages name variables, never values
        on_model = str(exc).startswith(MODEL_ENV)
        return [
            _entry("provider", OK if on_model else FAIL, "provider",
                   "CONFIGURED" if on_model else "PROVIDER_MISCONFIGURED",
                   "configured; reachability not probed" if on_model else str(exc)),
            _entry("model", FAIL if on_model else UNKNOWN, "model",
                   "MODEL_MISCONFIGURED" if on_model else "NOT_CHECKED",
                   str(exc) if on_model else "provider configuration is invalid"),
        ]
    host = urllib.parse.urlsplit(live["base_url"] or "").hostname or "unknown host"
    mismatch, wire = _wire_format(env, live["base_url"] or "", exists)
    if mismatch:
        return [
            _entry("provider", FAIL, "provider", mismatch, wire),
            _entry("model", UNKNOWN, "model", "NOT_CHECKED",
                   "the provider cannot be reached as configured"),
        ]
    return [
        _entry("provider", OK, "provider", "CONFIGURED",
               f"{host}; {wire}; configured, reachability not probed"),
        _entry("model", OK, "model", "CONFIGURED", f"runs use {live['model']} only"),
    ]


def check_worker(
    daemon_url: str, token: str, local_digest: str, timeout: float = 5.0
) -> list[dict[str, str]]:
    """``worker`` (the daemon answers and accepts our token) and ``revision``."""
    if not daemon_url:
        return [
            _entry("worker", OK, "orchestrator", "IN_PROCESS", "no daemon configured; "
                   "workers start from this process"),
            _entry("revision", OK, "orchestrator", "IN_PROCESS", "workers run this process's code"),
        ]
    not_checked = _entry("revision", UNKNOWN, "worker-daemon", "NOT_CHECKED",
                         "the daemon did not answer")
    if not token:
        return [_entry("worker", FAIL, "worker-daemon", "WORKER_TOKEN_UNSET",
                       "HARNESS_WORKER_DAEMON_TOKEN is not set; the daemon refuses every call"),
                not_checked]
    request = urllib.request.Request(daemon_url.rstrip("/") + "/version",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            remote = json.loads(response.read() or b"{}").get("codeDigest", "")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return [_entry("worker", FAIL, "worker-daemon", "WORKER_UNAUTHORIZED",
                           "the daemon rejected the orchestrator's token; recreate worker-daemon "
                           "from the same env file (ops runbook §3.11)"), not_checked]
        if exc.code == 404:  # authorised, but no /version: an image from before W23
            return [
                _entry("worker", OK, "worker-daemon", "REACHABLE", "answers and accepts the token"),
                _entry("revision", FAIL, "worker-daemon", "WORKER_PREDATES_READINESS",
                       "the daemon has no /version route, so its image is older than this "
                       "orchestrator; rebuild and recreate worker-daemon"),
            ]
        return [_entry("worker", FAIL, "worker-daemon", "WORKER_ERROR",
                       f"the daemon answered HTTP {exc.code}"), not_checked]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return [_entry("worker", FAIL, "worker-daemon", "WORKER_UNREACHABLE",
                       f"cannot reach the daemon: {reason}"), not_checked]
    worker = _entry("worker", OK, "worker-daemon", "REACHABLE", "answers and accepts the token")
    if remote == local_digest:
        return [worker, _entry("revision", OK, "worker-daemon", "MATCH",
                               f"same code as the orchestrator ({local_digest[:12]})")]
    return [worker, _entry("revision", FAIL, "worker-daemon", "WORKER_CODE_DRIFT",
                           f"the daemon runs different code ({str(remote)[:12] or 'none'}) than "
                           f"the orchestrator ({local_digest[:12]}); rebuild and recreate "
                           "worker-daemon from the same revision")]


def check_repository(repo_full_name: str | None, *, known: bool) -> dict[str, str]:
    if not known:
        return _entry("repository", UNKNOWN, "repository", "NOT_CHECKED",
                      "no database: the project's repository cannot be read")
    if not repo_full_name:
        return _entry("repository", FAIL, "repository", "PROJECT_REPO_NOT_CONNECTED",
                      "this project has no connected repository")
    return _entry("repository", OK, "repository", "CONNECTED", repo_full_name)


def check_permissions(principal: Principal) -> dict[str, str]:
    if can(principal, "write") and can(principal, "approve"):
        return _entry("permissions", OK, "identity", "CAN_RUN",
                      f"role {principal.role} may start, stop and approve runs")
    return _entry("permissions", FAIL, "identity", "ROLE_READ_ONLY",
                  f"role {principal.role} may watch runs but not start or approve them")


def report(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """``ok`` only when every entry is ok: ``unknown`` is not a pass."""
    return {"ok": all(e["status"] == OK for e in entries), "checks": entries}

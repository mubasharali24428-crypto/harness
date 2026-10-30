"""Execution engine: real DeepSeek Harness (Python SDK) with explicit modes.

Modes (exactly one, from VALIDATED config — see ``live_mode.py``):
1. backend_runner callable  — injected (tests / custom runtimes)
2. live                     — ENGINE_LIVE_MODE=live plus a validated
                              DEEPSEEK_API_KEY / DSH_MODEL; every run goes
                              through deepseek-harness-sdk
3. dry                      — explicit "not-configured" response, never
                              faked success

M11 Wave 1 (W1-B1): the mode is an explicit :class:`LiveMode` enum
resolved from validated configuration — never ``bool(api_key and model)``,
which counted compose placeholders like ``DEEPSEEK_API_KEY=ollama`` as a
live engine (finding C-004). The forced model-downgrade ladder is DELETED
per AGENTS.md law ("No forced model downgrade"; raw-02 finding H1): the
configured model is the only model that runs, and its failure surfaces
honestly as ``status="error"`` instead of being retried against whatever
model the environment or a catalog happens to offer. Result attribution
stays honest (raw-02 L1): the ``engine`` field always names the model
that actually answered.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness_orchestrator import confine, domain_metrics
from harness_orchestrator import mcp as mcp_config
from harness_orchestrator.container_runtime import ContainerBoundary
from harness_orchestrator.context_window import configured_context_window
from harness_orchestrator.live_mode import (
    LiveMode,
    parse_live_mode,
    resolve_live_mode,
    validate_live_credentials,
)
from harness_orchestrator.redact import scrub_secrets
from harness_orchestrator.repo_context import build_repo_context

logger = logging.getLogger(__name__)

# Restricted shipped composition; the default "sdk" profile is danger-full-access.
DEFAULT_DSH_PROFILE = "sdk-minimal"
# The only LLM route the shipped profile registers. It is a ROUTE NAME, not a
# wire format: `deploy/patches/openrouter.patch.yml` keeps this same id and
# re-points it at the openai-completions API, which is how an OpenAI-compatible
# gateway (OpenRouter, LiteLLM, vLLM) is reached. Unpatched, the route speaks
# the Anthropic Messages API and such a gateway answers
# "Invalid Anthropic Messages API request". There is deliberately no
# HARNESS_DSH_PROVIDER env knob: a provider that the profile has not registered
# fails with NO_ADAPTER, so the switch worth having is the reviewed patch file.
DEFAULT_DSH_PROVIDER = "deepseek-official"
REAL_WORKER_MODULE = "harness_orchestrator.engine_worker"
DAEMON_URL_ENV = "HARNESS_WORKER_DAEMON_URL"
DAEMON_TOKEN_ENV = "HARNESS_WORKER_DAEMON_TOKEN"
def real_worker_confinement_error(module: str, real_module: str = REAL_WORKER_MODULE) -> str | None:
    """Why the REAL engine worker may not run here, or None when it may.

    The real runtime needs a PTY shell, which macOS seatbelt breaks even with
    allow-default, so a seatbelt-confined real worker is refused rather than
    silently degraded. On a host with no OS-level backend at all (Linux without
    the container boundary) the honest answer is that no boundary exists — the
    old message named seatbelt on every platform, which pointed Linux operators
    at a sandbox they do not have.
    """
    if module != real_module:
        return None
    requested = os.environ.get(confine.CONFINEMENT_ENV, "auto").strip().lower()
    if requested in {"off", "container"}:
        return None
    mode = confine.resolve_mode()  # raises for an explicitly unavailable strict mode
    if mode == "seatbelt":
        return (
            "OS confinement is unavailable for the real DeepSeek runtime: seatbelt "
            f"breaks its PTY shell (resolved mode {mode!r}); set "
            f"{confine.CONFINEMENT_ENV}=off explicitly to accept an unconfined worker, "
            "or run it inside a container/VM boundary"
        )
    return (
        "OS confinement is unavailable for the real DeepSeek runtime: this host resolved "
        f"{confine.CONFINEMENT_ENV} to mode {mode!r}, which provides no OS-level boundary; "
        f"set {confine.CONFINEMENT_ENV}=container to run inside the container boundary, or "
        f"{confine.CONFINEMENT_ENV}=off explicitly to accept an unconfined worker"
    )


_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHELL_PLUGIN = Path(__file__).with_name("dsh_plugins") / "harness-shell.js"
# Section 5's gate script; the worker image sets HARNESS_SHELL_GATE (section4/worker.Dockerfile).
_REPO_SHELL_GATE = (
    Path(__file__).resolve().parents[3] / "section5" / "src" / "harness_sandbox" / "shell_gate.py"
)
# W4: web_fetch is our own plugin, not the runtime's web tool, so the egress
# decision (_REPO_WEB_GATE -> egress.py + the generated allow-list) is the only
# way out. Both files are copied beside shell_gate.py in the worker image.
_WEB_PLUGIN = Path(__file__).with_name("dsh_plugins") / "harness-web.js"
_REPO_WEB_GATE = (
    Path(__file__).resolve().parents[3] / "section5" / "src" / "harness_sandbox" / "web_gate.py"
)
# W10: the browser renders inside the run's own container. Its gate confines the
# output to the workspace and sends remote URLs through the same web-purpose
# proxy; the Chromium binary is baked into the worker image (worker.Dockerfile).
_BROWSER_PLUGIN = Path(__file__).with_name("dsh_plugins") / "harness-browser.js"
_REPO_BROWSER_GATE = (
    Path(__file__).resolve().parents[3] / "section5" / "src" / "harness_sandbox" / "browser_gate.py"
)
# W11: image comparison and pixel sampling. The gate reads files only — it spawns
# nothing and dials nothing — but it is mounted the same way, over the same
# workspace root the browser writes its screenshots into.
_IMAGES_PLUGIN = Path(__file__).with_name("dsh_plugins") / "harness-images.js"
_REPO_IMAGE_GATE = (
    Path(__file__).resolve().parents[3] / "section5" / "src" / "harness_sandbox" / "image_gate.py"
)


def _read_only(path: str) -> bool:
    """Is ``path`` on a read-only mount (the container boundary's own, observable signal)?"""
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except OSError:
        return False


def _fs_write_capability(
    shell_policy: dict[str, Any] | None, workspace_dir: str
) -> tuple[bool, str]:
    """Can this run's filesystem tool be allowed to WRITE?

    The runtime's ``workspaceRoot`` confines nothing (W21 measured reads and
    writes escaping it in ``off`` mode); only the container mounts confine
    tool-fs, which is why the patch turns it off elsewhere. Two things the
    approved policy names live *inside* the workspace and the tool does not
    know them:

    * ``forbiddenPaths`` — frozen for the whole worker by the container's
      nested read-only binds and by Seatbelt's ``deny file-write*``; Landlock
      cannot express "grant the tree, deny a subtree", and ``none`` enforces
      nothing.
    * ``scopePaths`` — an approved intent narrower than the workspace. M03: the
      container boundary mounts the workspace read-only and binds only the scope
      back writable (``confine.scope_targets``), so the kernel refuses a native
      write outside the intent. Anywhere else nothing would.

    So writes stay off unless they are covered: forbidden paths need
    container/Seatbelt, and a narrower intent scope needs the workspace to be
    read-only around it, which is observed here rather than assumed.
    Returns ``(allowed, reason)``; the reason is written into the patch so an
    operator reading a session can see why writes were off.
    """
    policy = shell_policy or {}
    forbidden = list(policy.get("forbiddenPaths") or [])
    scope = [str(p) for p in (policy.get("scopePaths") or []) if str(p).strip()]
    root = os.path.realpath(workspace_dir)
    narrower = [p for p in scope if os.path.realpath(os.path.join(root, p)) != root]
    if narrower and not _read_only(root):
        return False, (
            f"write/edit off: intent scope names {len(narrower)} path(s) inside the "
            "workspace and a native file tool cannot enforce a narrower scope unless the "
            "workspace is mounted read-only around it (container boundary)"
        )
    if not forbidden:
        return True, ""
    mode = confine.resolve_mode()
    if mode in ("container", "seatbelt"):
        return True, ""
    return False, (
        f"write/edit off: {len(forbidden)} forbidden path(s) and confinement mode "
        f"{mode!r} cannot freeze them (needs container or seatbelt)"
    )


def _execution_policy_patch(
    session_dir: str, workspace_dir: str, shell_policy: dict[str, Any] | None = None
) -> str:
    """Write the mandatory final SDK overlay that confines writes to this run.

    sdk-minimal's shipped sandbox-policy defaults to danger-full-access. This
    per-run patch is appended after operator transport patches so none can
    loosen the runtime policy for the selected SDK profile.

    It also swaps the runtime's PTY bash tool for ``dsh_plugins/harness-shell.js``, which sends
    every command through Section 5's shell gate with this run's ``shell_policy``
    (``forbiddenPaths``, intent ``scopePaths``, ``maxToolCalls``). A missing gate fails the run.

    W4 adds ``dsh_plugins/harness-web.js`` when Section 5's web gate is present: ``web_fetch``
    asks that gate before dialing, so the allow-list (derived from the provider registry) is the
    only reachable set. A missing web gate disables the tool rather than opening an unfiltered one.

    W11 adds ``dsh_plugins/harness-images.js`` (``image``: pixel diff and pixel sample) beside the
    W10 browser tool, over the same workspace root: what the browser renders, the image tool can
    measure. A missing gate means the tool is absent, never half-mounted.
    """
    gate = os.environ.get("HARNESS_SHELL_GATE") or str(_REPO_SHELL_GATE)
    if not os.path.isfile(gate):  # fail closed: never fall back to an unmediated shell
        raise FileNotFoundError(f"Section 5 shell gate not found: {gate}")
    policy = shell_policy or {}
    shell = {
        "gate": [sys.executable, "-I", gate],
        "maxToolCalls": policy.get("maxToolCalls"),
        "forbiddenPaths": list(policy.get("forbiddenPaths") or []),
        "scopePaths": list(policy.get("scopePaths") or []),
    }
    patch = Path(session_dir) / "harness-execution-policy.yml"
    may_write, write_note = _fs_write_capability(shell_policy, workspace_dir)
    # W21/M01: the native file tools are confined by the container's mounts and by nothing
    # else; outside it they reach any path by absolute path, `../`, symlink or cd.
    contained = confine.resolve_mode() == "container"
    if not contained:
        may_write, write_note = False, (
            "read/write/edit off: native file tools are confined only by the container "
            "boundary; the shell gate remains"
        )
    fs_read = "true" if contained else "false"
    if write_note:
        logger.warning("run %s: %s", Path(session_dir).name, write_note)
    write_flags = (
        "    write: true\n    edit: true\n" if may_write else "    write: false\n    edit: false\n"
    )
    note = f"    # {write_note}\n" if write_note else ""
    web = ""
    web_gate = os.environ.get("HARNESS_WEB_GATE") or str(_REPO_WEB_GATE)
    if os.path.isfile(web_gate) and _WEB_PLUGIN.is_file():
        web_config = {
            "gate": [sys.executable, "-I", web_gate],
            "timeoutSeconds": 15,
            "maxBytes": 2_000_000,
            # The worker's run network has no gateway; ContainerBoundary attaches the
            # allow-list proxy to it under this alias (web-purpose port 3129).
            "proxyUrl": os.environ.get("HARNESS_WEB_PROXY_URL")
            or "http://harness-egress:3129",
        }
        web = (
            "- insert:\n"
            "    - id: harness-web\n"
            f"      name: {json.dumps(str(_WEB_PLUGIN))}\n"
            f"      config: {json.dumps(web_config)}\n"
        )
    else:
        logger.warning(
            "web gate or plugin missing (%s); web_fetch is not mounted for this run", web_gate
        )
    # W10: browser rendering is mounted the same way — a missing gate or plugin
    # means the tool is absent, never a half-mounted one.
    browser = ""
    browser_gate = os.environ.get("HARNESS_BROWSER_GATE") or str(_REPO_BROWSER_GATE)
    if os.path.isfile(browser_gate) and _BROWSER_PLUGIN.is_file():
        browser_config = {
            "gate": [sys.executable, "-I", browser_gate],
            # The gate confines every write to this tree; the agent opens the PNG
            # with the fs read tool, so it must be the same root.
            "workspace": os.path.realpath(workspace_dir),
            "timeoutSeconds": 60,
            "maxBytes": 4_000_000,
            "proxyUrl": os.environ.get("HARNESS_WEB_PROXY_URL")
            or "http://harness-egress:3129",
        }
        browser = (
            "- insert:\n"
            "    - id: harness-browser\n"
            f"      name: {json.dumps(str(_BROWSER_PLUGIN))}\n"
            f"      config: {json.dumps(browser_config)}\n"
        )
    else:
        logger.warning(
            "browser gate or plugin missing (%s); the browser tool is not mounted for this run",
            browser_gate,
        )
    # W11: the image tool is mounted beside the browser it compares — same
    # workspace root, same fail-closed rule.
    images = ""
    image_gate = os.environ.get("HARNESS_IMAGE_GATE") or str(_REPO_IMAGE_GATE)
    if os.path.isfile(image_gate) and _IMAGES_PLUGIN.is_file():
        image_config = {
            "gate": [sys.executable, "-I", image_gate],
            "workspace": os.path.realpath(workspace_dir),
            "maxBytes": 10_000_000,  # the per-artifact cap: a diff image is an artifact
            "maxPixels": 4_000_000,  # the comparison is O(pixels) in pure Python
        }
        images = (
            "- insert:\n"
            "    - id: harness-images\n"
            f"      name: {json.dumps(str(_IMAGES_PLUGIN))}\n"
            f"      config: {json.dumps(image_config)}\n"
        )
    else:
        logger.warning(
            "image gate or plugin missing (%s); the image tool is not mounted for this run",
            image_gate,
        )
    # W4 slice 3: MCP servers come only from the operator-reviewed DSH_MCP_SERVERS
    # list — never from the plan, the intent, or the model. A declared-but-broken
    # list raises, because a half-applied boundary is worse than a failed run.
    mcp_block = ""
    servers = mcp_config.reviewed_servers()
    if servers:
        lines = ["- insert:\n"]
        for config in servers:
            lines.append(f"    - id: mcp-{config['serverName']}\n")
            lines.append(f"      name: '{mcp_config.PLUGIN}'\n")
            lines.append(f"      config: {json.dumps(config)}\n")
        mcp_block = "".join(lines)
        logger.info("run %s: mounting %d reviewed MCP server(s)",
                    Path(session_dir).name, len(servers))
    patch.write_text(
        "- id: sandbox-policy\n"
        "  name: '@deepseek-ai/dsh-sandbox-policy'\n"
        "  disabled: false\n"
        "  config:\n"
        "    mode: workspace-write\n"
        f"    workspaceRoot: {json.dumps(os.path.realpath(workspace_dir))}\n"
        # W1: the shipped sdk-minimal profile turns the filesystem tool off
        # (read/write/edit all false, only readImage on), so until now the agent
        # could not open a file without going through the shell gate. W21 measured
        # that `workspaceRoot` above does not confine this tool, so it is on only
        # inside the container, whose mounts are the boundary (outside paths are
        # not mounted, secret files are masked). Writes additionally depend on the
        # plan's forbidden paths and scope being frozen — see _fs_write_capability.
        "- id: tool-fs\n"
        "  name: '@deepseek-ai/dsh-tool-fs'\n"
        "  disabled: false\n"
        "  config:\n"
        f"    read: {fs_read}\n"
        f"    readImage: {fs_read}\n" + note + write_flags +
        # glob/grep as first-class tools: they were only reachable through
        # bash before, which meant every file search paid a shell round-trip.
        "- id: tool-fs-search\n"
        "  name: '@deepseek-ai/dsh-tool-fs-search'\n"
        f"  disabled: {'false' if contained else 'true'}\n"
        "  config:\n"
        "    sampleOverCapGlobResults: false\n"
        "- id: persistent-bash\n"
        "  disabled: true\n"
        "- insert:\n"
        "    - id: harness-shell\n"
        f"      name: {json.dumps(str(_SHELL_PLUGIN))}\n"
        f"      config: {json.dumps(shell)}\n" + web + browser + images + mcp_block,
        encoding="utf-8",
    )
    return str(patch)


def _content_preview(content: Any, limit: int = 4000) -> str:
    """Flatten a ToolResultMessage's content blocks into a short preview."""
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                for key in ("text", "value", "content"):
                    value = block.get(key)
                    if isinstance(value, str):
                        parts.append(value)
                        break
    return "\n".join(parts)[:limit]


def _prompt_input(
    goal: str, images: list[dict[str, Any]] | None
) -> str | list[dict[str, Any]]:
    """The SDK's ``run`` input for one goal.

    Text-only stays a bare string — the byte-for-byte historical shape. With
    images the runtime wants the ACP block list, and the text block must come
    first so the instruction is read before the picture it refers to.
    """
    if not images:
        return goal
    return [{"type": "text", "text": goal}, *images]


def _notification_events(payload: Any) -> list[dict[str, Any]]:
    """Every plausible session-event shape inside one SDK notification payload.

    The runtime's notification envelope is not part of the SDK's typed surface,
    so the three documented shapes are accepted — a bare event, ``{event: …}``
    (``SessionEventEntry``), and a nested ``data`` — instead of inventing one.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ("event", "events"):
        value = payload.get(key)
        if isinstance(value, dict):
            out.append(value)
        elif isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
    if payload.get("type") in ("tool/call", "tool/result"):
        out.append(payload)
    data = payload.get("data")
    if isinstance(data, dict) and data.get("type") in ("tool/call", "tool/result"):
        out.append(data)
    return out


def _iter_usage_objects(node: Any):
    """Yield every ``{inputTokens, outputTokens}`` dict nested in a record."""
    if isinstance(node, dict):
        if "inputTokens" in node and "outputTokens" in node:
            yield node
            return
        for value in node.values():
            yield from _iter_usage_objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_usage_objects(value)


def _context_meta(events: list[Any]) -> dict[str, Any]:
    """What the runtime's context window is, and how much it has compacted.

    The window is the operator's configured value or ``None`` — never this
    module's own default, which is what made a 1M figure appear on runs whose
    model declared 65536. Token counts come from ``compaction/summary`` events
    (``shadowedTokenCount``), summed because a long run can compact more than
    once.
    """
    window = configured_context_window()
    compactions = 0
    tokens = 0
    for event in events or []:
        if not isinstance(event, dict) or event.get("type") != "compaction/summary":
            continue
        compactions += 1
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        try:
            tokens += int(data.get("shadowedTokenCount") or 0)
        except (TypeError, ValueError):
            pass
    return {"contextWindow": window, "compactions": compactions, "compactedTokens": tokens}


def _sdk_max_tokens() -> int | None:
    """Output ceiling for one turn: operator's choice, otherwise the provider's.

    The wrapper used to hardcode ``max_tokens=4096``, which silently truncated
    long edits and multi-file answers regardless of the model's real limit.
    An unset value is the honest default (let the provider decide); an operator
    that needs a cap for cost control sets ``HARNESS_SDK_MAX_TOKENS``.
    """
    raw = os.environ.get("HARNESS_SDK_MAX_TOKENS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"HARNESS_SDK_MAX_TOKENS must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError("HARNESS_SDK_MAX_TOKENS must be positive")
    return value


_GIT_CONFIG_DENY = (
    "helper",       # credential helpers execute commands
    "extraheader",  # tokens pasted into request headers
    "fsmonitor",
    "hookspath",
    "sshcommand",
    "proxy",
)


def _prepare_seeded_git(workspace: str) -> None:
    """Keep a seed's history, drop what must not travel with it.

    Copying ``.git`` blindly hands the agent three things it should never have:
    executable hooks (they run on our checkpoint commits), a config that can
    carry credentials or point git at an external command, and gitdir pointers
    (a worktree/submodule ``.git`` file or ``modules/`` entry) that escape the
    workspace root. History, refs, objects and index are kept; everything that
    runs code or names a path outside the run is removed.
    """
    git_dir = Path(workspace) / ".git"
    if not git_dir.exists():
        return
    if git_dir.is_symlink() or git_dir.is_file():
        # A worktree/gitdir pointer: never follow it out of the workspace.
        git_dir.unlink(missing_ok=True)
        return
    for name in ("hooks", "worktrees", "modules"):
        target = git_dir / name
        if target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    config = git_dir / "config"
    if not config.is_file():
        return
    original = config.read_text(encoding="utf-8", errors="replace")
    kept: list[str] = []
    for line in original.splitlines():
        stripped = line.strip().lower()
        if any(key in stripped for key in _GIT_CONFIG_DENY):
            continue
        if stripped.startswith("url =") and "@" in stripped:
            continue  # credentials embedded in a remote URL
        kept.append(line)
    if kept != original.splitlines():  # only touch a file we actually had to clean
        suffix = "\n" if original.endswith("\n") else ""
        config.write_text("\n".join(kept) + suffix, encoding="utf-8")


class DeepSeekHarnessWrapper:
    """Wraps DeepSeek Harness behind one method: execute_goal(goal)."""

    def __init__(
        self,
        backend_runner: Callable[[str], str] | None = None,
        workspace_dir: str | None = None,
        session_root: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        live_mode: LiveMode | str | None = None,
        isolated: bool | None = None,
    ) -> None:
        self.backend_runner = backend_runner
        # B2/B4: live SDK calls run in an OS-confined, killable worker process
        # unless disabled (HARNESS_ENGINE_ISOLATED=0, tests / the worker itself).
        self.isolated = (
            isolated
            if isolated is not None
            else os.environ.get("HARNESS_ENGINE_ISOLATED", "1") != "0"
        )
        self._procs: dict[str, subprocess.Popen] = {}
        self._containers: dict[str, ContainerBoundary] = {}
        self._cancel_requested: set[str] = set()
        self._shell_policies: dict[str, dict[str, Any]] = {}
        # N02: which sessions already carry the repo baseline. The SDK session
        # continues across a resume, so a second copy is a duplicate the model
        # pays to read again, not extra context.
        self._repo_context_sent: set[str] = set()
        self._proc_lock = threading.Lock()
        # M07: where confined workers come from when this process holds no Docker socket.
        self.daemon_url = os.environ.get(DAEMON_URL_ENV, "").strip().rstrip("/")
        self._daemon_runs: set[str] = set()
        # Allowlisted (DSH_*) values a worker daemon received from the orchestrator; the
        # daemon's own environment is not the deployment's runtime configuration.
        self.worker_env: dict[str, str] = {}
        self.workspace = workspace_dir or os.environ.get(
            "DSH_WORKSPACE", "/tmp/harness-dsh-workspace"
        )
        self.session_root = session_root or os.environ.get(
            "DSH_SESSION_ROOT", "/tmp/harness-dsh-sessions"
        )
        # Explicit constructor values win over the environment; blank env
        # entries behave like unset ones (compose "${VAR:-}" emptiness).
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (
            base_url if base_url is not None else os.environ.get("DEEPSEEK_BASE_URL", "")
        )
        self.model = model or os.environ.get("DSH_MODEL", "deepseek-chat")

        # M11-W1-B1: mode is an explicit, validated choice. A string arg is
        # parsed strictly (a typo fails HERE, loudly); None falls back to
        # ENGINE_LIVE_MODE, whose absence means dry — never inferred live.
        if isinstance(live_mode, str):
            self.live_config: LiveMode = parse_live_mode(live_mode)
        elif isinstance(live_mode, LiveMode):
            self.live_config = live_mode
        else:
            self.live_config = resolve_live_mode()

        # Fail fast at construction when LIVE is requested without a real
        # credential/model — placeholder keys are rejected by name here,
        # before any run can silently degrade (C-004).
        if self.backend_runner is None and self.live_config is LiveMode.LIVE:
            validate_live_credentials(
                api_key=self.api_key or None,
                model=self.model or None,
                base_url=self.base_url or None,
            )

    def set_shell_policy(self, session_id: str, policy: dict[str, Any]) -> None:
        """The next run of ``session_id`` enforces ``policy`` in its bash tool (shell gate)."""
        self._shell_policies[session_id] = policy

    def run_workspace(self, session_id: str) -> str:
        """Per-run working directory: ``<workspace root>/<session_id>``.

        session_id is the run id. Rejects anything but a plain id and any path
        whose real location (symlinks resolved) escapes the root, so two runs
        can never share or reach into each other's files.
        """
        if not _SESSION_ID_RE.fullmatch(session_id or ""):
            domain_metrics.inc("harness_isolation_violation_total", "bad_session_id")
            raise ValueError(f"invalid session id for workspace: {session_id!r}")
        root = os.path.realpath(self.workspace)
        path = os.path.realpath(os.path.join(root, session_id))
        if os.path.dirname(path) != root:
            domain_metrics.inc("harness_isolation_violation_total", "workspace_escape")
            raise ValueError(f"workspace for {session_id!r} escapes the workspace root")
        return path

    def provision_workspace(self, session_id: str) -> str:
        """Create the run workspace once; seed it from ``HARNESS_WORKSPACE_SEED`` if set.

        Resumes keep whatever is already there. The seed is a local directory (a checkout
        the operator prepared); its ``.git`` is copied too, because the checkpoint and
        diff machinery reads real history — stripping it left every run starting from a
        tree git could not describe. Unset = an empty workspace.
        """
        path = self.run_workspace(session_id)
        if os.path.isdir(path):
            return path
        seed = os.environ.get("HARNESS_WORKSPACE_SEED", "").strip()
        if seed:
            if not os.path.isdir(seed):
                raise ValueError("HARNESS_WORKSPACE_SEED is not a directory")
            shutil.copytree(seed, path, symlinks=True)
            _prepare_seeded_git(path)
        else:
            os.makedirs(path)
        return path

    def run_session_dir(self, session_id: str) -> str:
        """Per-run SDK session/home dir (validated like the workspace)."""
        self.run_workspace(session_id)  # same id validation
        return os.path.join(os.path.realpath(self.session_root), session_id)

    def run_tmp_dir(self, session_id: str) -> str:
        self.run_workspace(session_id)
        return os.path.join(os.path.realpath(self.workspace), ".run-tmp", session_id)

    def workspace_identity(self, session_id: str) -> dict[str, Any]:
        """Run-metadata identity of this run's workspace: stable, non-secret, no host path.

        ``id`` is the run-scoped directory name, ``fingerprint`` a short hash of the
        resolved location (distinguishes two hosts/roots without revealing the path),
        ``isolation`` the confinement the worker would run under. Never includes env,
        keys, or the absolute path.
        """
        path = self.run_workspace(session_id)
        return {
            "id": session_id,
            "fingerprint": hashlib.sha256(path.encode()).hexdigest()[:16],
            "isolated": bool(self.isolated),
            "isolation": confine.resolve_mode() if self.isolated else "in-process",
        }

    def cleanup_scratch(self, session_id: str) -> bool:
        """Delete the per-run scratch dir (``.run-tmp/<id>``: TMPDIR/HOME of the worker).

        Only for a finished run: refuses while a worker is alive, refuses symlinks and any
        path that resolves outside the workspace root. Outputs (the run workspace) and the
        staging baseline are NOT touched here - retention owns those.
        """
        if self.is_worker_running(session_id):
            return False
        path = self.run_tmp_dir(session_id)
        root = os.path.realpath(self.workspace)
        if os.path.islink(path):
            domain_metrics.inc("harness_isolation_violation_total", "symlink_escape")
            return False
        if not os.path.isdir(path) or os.path.commonpath([root, os.path.realpath(path)]) != root:
            return False
        shutil.rmtree(path, ignore_errors=True)
        return True

    def live_boundary_run_ids(self) -> list[str]:
        """Runs whose per-run network THIS process still holds.

        The boundary sweeper's second "live" source, alongside the DB's running
        claims: a run executing right now may not have landed its claim row yet
        (or, in local mode, never has one), and its network must not be removed.
        """
        with self._proc_lock:
            return list(self._containers)

    def cancel(self, session_id: str) -> bool:
        """Kill the container (or process group) and confirm it is gone.

        Docker daemon loss is not confirmation. Idempotent for stopped workers.
        """
        with self._proc_lock:
            self._cancel_requested.add(session_id)
            proc = self._procs.get(session_id)
            boundary = self._containers.get(session_id)
        if self.daemon_url:
            try:
                with self._daemon_call("POST", f"/runs/{session_id}/cancel") as resp:
                    return bool(json.load(resp).get("confirmed"))
            except (OSError, ValueError):
                return False  # an unreachable daemon is not confirmation
        if boundary is not None:
            return boundary.kill(proc)
        if proc is None:
            return True
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # pgid == pid (start_new_session)
        except (ProcessLookupError, PermissionError):
            pass  # macOS returns EPERM for a group whose leader is already reaped
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.killpg(proc.pid, 0)
            except (ProcessLookupError, PermissionError):
                return True  # EPERM = group gone/zombie, not ours any more
            time.sleep(0.05)
        return False

    def reset_cancel(self, session_id: str) -> None:
        """A fresh start/resume clears the sticky cancel flag."""
        with self._proc_lock:
            self._cancel_requested.discard(session_id)

    def is_worker_running(self, session_id: str) -> bool:
        if self.daemon_url:
            try:
                with self._daemon_call("GET", f"/runs/{session_id}") as resp:
                    return bool(json.load(resp).get("running"))
            except (OSError, ValueError):
                return True  # unknown is treated as alive, so nothing cleans up under it
        with self._proc_lock:
            proc = self._procs.get(session_id)
            boundary = self._containers.get(session_id)
        return (proc is not None and proc.poll() is None) or (
            boundary is not None and not boundary.confirmed_gone()
        )

    def _run_isolated(
        self,
        goal: str,
        session_id: str,
        model: str,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str | None, list[Any]]:
        """Run the SDK call in a confined, killable worker process (B2/B4).

        Goal images ride the request line as base64 rather than as artifact
        references: the worker is OS-confined to this run's workspace/session
        and cannot read the shared artifact store, and it must not be given a
        path into it.
        """
        if self.daemon_url:
            return self._run_via_daemon(goal, session_id, model, on_progress, images)
        boundary = None
        try:
            workspace = self.run_workspace(session_id)
            sess = self.run_session_dir(session_id)
            tmp = self.run_tmp_dir(session_id)
            for d in (workspace, sess, tmp):
                os.makedirs(d, exist_ok=True)
            module = os.environ.get("HARNESS_ENGINE_WORKER_MODULE", REAL_WORKER_MODULE)
            refusal = real_worker_confinement_error(module)
            if refusal is not None:
                # Fail closed, never degrade silently; the message names THIS host's mode.
                raise confine.ConfinementUnavailableError(refusal)
            mode = confine.resolve_mode()
            if mode == "none":
                logger.warning(
                    "engine worker for %s runs UNCONFINED (no OS confinement on this host)",
                    session_id,
                )
            env = confine.scrubbed_env(
                {
                    "DEEPSEEK_API_KEY": self.api_key,
                    **({"DEEPSEEK_BASE_URL": self.base_url} if self.base_url else {}),
                    **self.worker_env,
                    "DSH_HOME": sess,
                    # The worker's own write-capability check must see the boundary the
                    # parent chose; inside a container "auto" would otherwise read "none".
                    confine.CONFINEMENT_ENV: "off" if mode == "none" else mode,
                    # W4 slice 4: installs are only routed through the egress proxy
                    # when the operator flagged them AND the boundary is a container.
                    **confine.install_env(mode),
                },
                tmp,
            )
            if mode == "container":
                # A root launcher (the worker daemon) must hand the run dirs to the
                # uid it is about to drop the worker to, or nothing the agent writes
                # is writable. See confine.claim_run_dirs_for_worker.
                confine.claim_run_dirs_for_worker((workspace, sess, tmp))
                # session_id is the run id: labelling the network with it is what
                # lets `sweep_orphan_boundaries` tell this run's leftovers from a
                # live run's equipment after a SIGKILL.
                boundary = ContainerBoundary(self.base_url, run_id=session_id)
                env["DEEPSEEK_BASE_URL"] = boundary.start()
            policy = self._shell_policies.get(session_id) or {}
            forbidden = policy.get("forbiddenPaths")
            before = confine.forbidden_snapshot(workspace, forbidden)
            argv = confine.wrap_command(
                [sys.executable, "-m", module],
                mode=mode,
                roots=[os.path.realpath(self.workspace), os.path.realpath(self.session_root)],
                workspace=workspace,
                session_dir=sess,
                tmp_dir=tmp,
                container_name=boundary.name if boundary else None,
                network=boundary.network if boundary else None,
                image=boundary.image if boundary else "",
                env=env,
                forbidden_paths=forbidden,
                scope_paths=policy.get("scopePaths"),
            )
            request = json.dumps(
                {
                    "goal": goal,
                    "session_id": session_id,
                    "model": model,
                    "workspace_root": os.path.realpath(self.workspace),
                    "session_root": os.path.realpath(self.session_root),
                    "shell_policy": self._shell_policies.get(session_id),
                    "images": images or [],
                }
            )
            with self._proc_lock:
                if session_id in self._cancel_requested:
                    return "Execution cancelled.", "cancelled", model, []
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=workspace,
                    env=env,
                    start_new_session=True,  # own process group => killable as a unit
                )
                self._procs[session_id] = proc
                if boundary:
                    self._containers[session_id] = boundary
            budget = float(os.environ.get("HARNESS_ENGINE_TIMEOUT_SECONDS", "900"))
            # Read the worker's stdout as it arrives instead of at the end: the
            # worker writes one NDJSON line per tool event, then one result
            # line. Without this the transcript could only appear after the run
            # finished, which is exactly what a "live" transcript must not do.
            err_chunks: list[str] = []

            def _drain_stderr() -> None:
                try:
                    for chunk in iter(lambda: proc.stderr.readline(), ""):
                        err_chunks.append(chunk)
                except (OSError, ValueError):
                    pass

            def _kill() -> None:
                nonlocal timed_out
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)  # pgid == pid (start_new_session)
                except ProcessLookupError:
                    pass

            timed_out = False
            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()
            watchdog = threading.Timer(budget, _kill)
            watchdog.start()
            result_line: dict[str, Any] | None = None
            try:
                proc.stdin.write(request + "\n")
                proc.stdin.flush()
                proc.stdin.close()
                for raw in proc.stdout:  # blocks until a line or EOF
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except ValueError:
                        continue  # a chatty runtime line, not our protocol
                    if not isinstance(parsed, dict):
                        continue
                    progress = parsed.get("progress")
                    if isinstance(progress, dict):
                        if on_progress is not None:
                            on_progress(progress)
                        continue
                    if "status" in parsed:
                        result_line = parsed
                proc.wait(timeout=10)
            finally:
                watchdog.cancel()
                with self._proc_lock:
                    if self._procs.get(session_id) is proc:
                        if boundary and not boundary.kill(proc):
                            raise RuntimeError("container termination could not be confirmed")
                        del self._procs[session_id]
                        self._containers.pop(session_id, None)
            if timed_out:
                return (
                    f"DeepSeek Harness worker exceeded {budget:g}s and was killed",
                    "error",
                    model,
                    [],
                )
            if session_id in self._cancel_requested:
                return "Execution cancelled.", "cancelled", model, []
            if confine.forbidden_snapshot(workspace, forbidden) != before:
                return "Run changed a forbidden path.", "error", model, []
            if proc.returncode != 0 or result_line is None:
                tail = scrub_secrets("".join(err_chunks)[-500:], self.api_key)
                return (
                    f"DeepSeek Harness worker exited {proc.returncode}: {tail}",
                    "error",
                    model,
                    [],
                )
            result_line["text"] = scrub_secrets(str(result_line.get("text", "")), self.api_key)
            return (
                result_line["text"],
                result_line["status"],
                result_line.get("model") or model,
                result_line.get("events") or [],
            )
        except Exception as exc:  # noqa: BLE001 - includes ConfinementUnavailableError: fail closed
            msg = scrub_secrets(f"DeepSeek Harness run failed: {exc}", self.api_key)
            return msg, "error", model, []

        finally:
            if boundary:
                boundary.close()

    def _daemon_call(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 10.0
    ) -> Any:
        token = os.environ.get(DAEMON_TOKEN_ENV, "")
        request = urllib.request.Request(
            self.daemon_url + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - operator-set URL

    def _run_via_daemon(
        self,
        goal: str,
        session_id: str,
        model: str,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str | None, list[Any]]:
        """M07: the same confined worker, created by ``worker_daemon`` instead of this process.

        The deployed orchestrator has no Docker socket, by design. The daemon that holds it
        receives the run id, goal, policy and allowlisted (``DSH_*``) settings - never paths,
        an image or limits - and streams back the worker's own NDJSON. The run's directories
        are created HERE first, as this process's user, on the volume both share, so the
        worker (run as their owner) can write them.
        """
        try:
            for path in (
                self.run_workspace(session_id),
                self.run_session_dir(session_id),
                self.run_tmp_dir(session_id),
            ):
                os.makedirs(path, exist_ok=True)
            body = {
                "goal": goal,
                "model": model,
                "images": images or [],
                "shellPolicy": self._shell_policies.get(session_id),
                "apiKey": self.api_key,
                "baseUrl": self.base_url,
                "env": {
                    k: v for k, v in os.environ.items() if k.startswith(confine.ENV_ALLOW_PREFIX)
                },
            }
            with self._proc_lock:
                if session_id in self._cancel_requested:
                    return "Execution cancelled.", "cancelled", model, []
                self._daemon_runs.add(session_id)
            budget = float(os.environ.get("HARNESS_ENGINE_TIMEOUT_SECONDS", "900"))
            result: dict[str, Any] | None = None
            try:
                with self._daemon_call(
                    "POST", f"/runs/{session_id}", body, timeout=budget + 60
                ) as resp:
                    for raw in resp:  # one NDJSON line per tool event, then the result
                        try:
                            parsed = json.loads(raw)
                        except ValueError:
                            continue
                        if not isinstance(parsed, dict):
                            continue
                        if isinstance(parsed.get("progress"), dict):
                            if on_progress is not None:
                                on_progress(parsed["progress"])
                        elif isinstance(parsed.get("result"), dict):
                            result = parsed["result"]
            finally:
                with self._proc_lock:
                    self._daemon_runs.discard(session_id)
            if session_id in self._cancel_requested:
                return "Execution cancelled.", "cancelled", model, []
            if result is None:
                return "worker daemon ended the run without a result", "error", model, []
            return (
                scrub_secrets(str(result.get("text", "")), self.api_key),
                str(result.get("status") or "error"),
                result.get("model") or model,
                result.get("events") or [],
            )
        except Exception as exc:  # noqa: BLE001 - daemon loss fails the run, never degrades it
            msg = scrub_secrets(f"DeepSeek Harness run failed: worker daemon: {exc}", self.api_key)
            return msg, "error", model, []

    @property
    def mode(self) -> LiveMode:
        """The explicit engine mode (never inferred from secret presence)."""
        return self.live_config

    @property
    def live_mode(self) -> bool:
        """Backward-compat boolean view of :attr:`mode`."""
        return self.live_config is LiveMode.LIVE

    @staticmethod
    def _fold_usage(events: list[Any]) -> dict[str, int] | None:
        """Fold dsh ``assistant/chunk`` usage chunks into one totals dict.

        Live runs surface real token accounting as events shaped
        ``{"type": "assistant/chunk", "data": {"chunk": {"type": "usage",
        "usage": {"inputTokens": .., "outputTokens": .., ...}}}}`` — one per
        billed step. Summing keeps multi-step runs honest; None when the run
        surfaced no usage (stubs/dry mode → caller estimates from text).
        """
        totals: dict[str, int] | None = None
        for event in events or []:
            if not isinstance(event, dict) or event.get("type") != "assistant/chunk":
                continue
            data = event.get("data")
            chunk = data.get("chunk") if isinstance(data, dict) else None
            usage = chunk.get("usage") if isinstance(chunk, dict) else None
            if not isinstance(usage, dict):
                continue
            try:
                step = {
                    "inputTokens": int(usage.get("inputTokens") or 0),
                    "outputTokens": int(usage.get("outputTokens") or 0),
                }
            except (TypeError, ValueError):
                continue
            if totals is None:
                totals = step
            else:
                totals["inputTokens"] += step["inputTokens"]
                totals["outputTokens"] += step["outputTokens"]
        return totals

    def session_usage(self, session_id: str) -> dict[str, int] | None:
        """Token usage summed from the SDK session transcript, else None.

        Measured 2026-10-29 on the deployed stack: a live run's event stream
        carries NO ``assistant/chunk`` usage chunks (``usage`` comes back None),
        while the SDK writes one usage object per billed step into
        ``session*.jsonl`` under the run's session dir. Reading those back is
        what makes live metering real instead of a text estimate.

        The SDK writes a message update and then its final form, so identical
        consecutive records are collapsed. ``cacheReadTokens`` is returned
        separately: the catalog has no cache tier, and the metering hook bills
        it at the input rate (a conservative upper bound) rather than dropping
        prompt tokens it knows were sent.
        """
        try:
            root = Path(self.run_session_dir(session_id))
        except (ValueError, OSError):
            return None
        if not root.is_dir():
            return None
        totals = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}
        previous: tuple[int, int, int] | None = None
        found = False
        for path in sorted(root.rglob("session*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                for usage in _iter_usage_objects(record):
                    try:
                        step = (
                            int(usage.get("inputTokens") or 0),
                            int(usage.get("outputTokens") or 0),
                            int(usage.get("cacheReadTokens") or 0),
                        )
                    except (TypeError, ValueError):
                        continue
                    if step == previous:  # message update + its final form
                        continue
                    previous = step
                    found = True
                    totals["inputTokens"] += step[0]
                    totals["outputTokens"] += step[1]
                    totals["cacheReadTokens"] += step[2]
        return totals if found else None

    def _with_repo_context(self, goal: str, session_id: str) -> str:
        """Prepend the workspace baseline, once per session.

        The runtime's own ``includeRuntimeContext`` is off in the shipped profile
        and that key belongs to the SDK; this is the product's own baseline, in
        the goal string, on both execution paths — the isolated worker receives
        the goal verbatim, so injecting here covers it too.
        """
        if os.environ.get("HARNESS_REPO_CONTEXT", "1").strip().lower() in {"0", "off", "false"}:
            return goal
        if session_id in self._repo_context_sent:
            return goal
        block = build_repo_context(self.workspace)
        if not block:
            return goal
        self._repo_context_sent.add(session_id)
        return f"{goal}\n\n{block}"

    def execute_goal(
        self,
        goal: str,
        session_id: str = "session-001",
        model: str | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # N02: one injection point, so the direct SDK call and the isolated
        # worker cannot drift apart on what the model was told about the repo.
        goal = self._with_repo_context(goal, session_id)
        # A per-run/team choice wins over the deployment default. ``None``
        # keeps the exact historical behavior.
        effective_model = model or self.model
        used_model: str | None = None
        usage: dict[str, int] | None = None
        tool_calls: list[dict[str, Any]] = []
        events: list[Any] = []
        if self.backend_runner:
            result_text = self.backend_runner(goal)
            status = "completed"
        elif self.live_config is LiveMode.LIVE:
            # The configured model is THE model: passed explicitly so no
            # callee ever picks another one (no-downgrade law).
            try:
                live_kwargs: dict[str, Any] = {"model": effective_model}
                if on_progress is not None:
                    live_kwargs["on_progress"] = on_progress
                if images:
                    live_kwargs["images"] = images
                result_text, status, used_model, events = self._run_live_events(
                    goal, session_id, **live_kwargs
                )
                # M14-W4 F1a: real token accounting rides the SDK's usage
                # chunks; folded onto the result so the orchestrator's
                # completion hook can meter ACTUAL usage, not estimates.
                usage = self._fold_usage(events)
                if usage is None:
                    # This deployment's SDK surfaces no usage events; the
                    # transcript is the ground truth (see session_usage).
                    usage = self.session_usage(session_id)
                tool_calls = self._fold_tool_calls(events)
            except Exception as exc:  # noqa: BLE001 — honest error, never escape
                result_text = scrub_secrets(
                    f"DeepSeek Harness run failed on {effective_model}: {exc}",
                    self.api_key,
                )
                status = "error"
                used_model = effective_model
                events = []
            if status == "completed" and not (result_text or "").strip():
                # Wave 6 (D2) honesty rule, preserved after ladder deletion:
                # an empty/whitespace response is a FAILED answer, never a
                # vacuous success attributed to the configured model.
                status = "error"
                result_text = f"model {used_model} returned an empty response"
        else:
            result_text = (
                "DeepSeek Harness not configured: set ENGINE_LIVE_MODE=live "
                "with DEEPSEEK_API_KEY (and DEEPSEEK_BASE_URL for "
                "OpenAI-compatible endpoints) to enable live runs."
            )
            status = "not_configured"

        self._shell_policies.pop(session_id, None)
        return {
            "sessionId": session_id,
            "engine": self._engine_name(used_model),
            "status": status,
            "finalResponse": result_text,
            # M14-W4 F1a: real token usage when the SDK surfaced it. Absent
            # (never None-valued) so stub/dry results keep their exact shape.
            **({"usage": usage} if usage is not None else {}),
            # Part 44: the run's own tool calls, recorded toward its tool-call limit.
            **({"toolCalls": tool_calls} if tool_calls else {}),
            # W1: the window the run actually ran with, and what it compacted.
            "context": _context_meta(events),
        }

    @staticmethod
    def _fold_tool_calls(events: list[Any]) -> list[dict[str, Any]]:
        """One ``{callId, tool, target, outcome}`` per runtime ``tool/call`` event.

        ``target`` is the bash command (else empty); ``outcome`` is ``ok`` or ``error`` from the
        matching ``tool/result`` (a refused command is an error result), ``error`` when none came.
        """
        calls: dict[str, dict[str, Any]] = {}
        for event in events or []:
            if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
                continue
            data = event["data"]
            if event.get("type") == "tool/call" and data.get("callId"):
                args = data.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                command = args.get("command") if isinstance(args, dict) else None
                calls[str(data["callId"])] = {
                    "callId": str(data["callId"]),
                    "tool": str(data.get("name") or "unknown"),
                    "target": command if isinstance(command, str) else "",
                    "outcome": "error",
                }
            elif event.get("type") == "tool/result" and isinstance(data.get("message"), dict):
                call = calls.get(str(data["message"].get("toolCallId")))
                if call is not None:
                    call["outcome"] = "error" if data["message"].get("isError") else "ok"
        return list(calls.values())

    @staticmethod
    def _tool_record(event: Any) -> dict[str, Any] | None:
        """One transcript record from a runtime ``tool/call`` or ``tool/result``.

        Shapes come from the runtime's own ``SessionEventMap``:
        ``tool/call {turn, step, callId, name, arguments}`` and
        ``tool/result {turn, step, message: {toolCallId, isError?, content},
        error?}``. Text is scrubbed by the caller before it is stored; nothing
        here reads a path, a key or the environment.
        """
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            return None
        kind = event.get("type")
        data = event["data"]
        base = {
            "turn": data.get("turn"),
            "step": data.get("step"),
            "at": event.get("time"),
            "seq": event.get("seq"),
        }
        if kind == "tool/call" and data.get("callId"):
            args = data.get("arguments")
            if not isinstance(args, str):
                try:
                    args = json.dumps(args, ensure_ascii=False)
                except (TypeError, ValueError):
                    args = ""
            return {
                **base,
                "phase": "call",
                "callId": str(data["callId"]),
                "name": str(data.get("name") or "unknown"),
                "arguments": args[:2000],
            }
        if kind == "tool/result" and isinstance(data.get("message"), dict):
            message = data["message"]
            call_id = message.get("toolCallId")
            if not call_id:
                return None
            error = data.get("error") if isinstance(data.get("error"), dict) else None
            failed = bool(message.get("isError") or error)
            reason = None
            if error:
                reason = str(error.get("reason") or error.get("code") or "tool error")
            return {
                **base,
                "phase": "result",
                "callId": str(call_id),
                "ok": not failed,
                "error": reason,
                "output": _content_preview(message.get("content")),
            }
        return None

    @classmethod
    def _tool_records(cls, events: list[Any]) -> list[dict[str, Any]]:
        records = []
        for event in events or []:
            record = cls._tool_record(event)
            if record is not None:
                records.append(record)
        return records

    def _engine_name(self, model_used: str | None = None) -> str:
        """Name the engine AND the model that actually answered.

        raw-02 L1 honesty rule: report the winning candidate, never assume
        the configured primary answered."""
        if self.backend_runner:
            return "injected"
        if self.live_config is LiveMode.LIVE:
            return f"deepseek-harness-sdk:{model_used or self.model}"
        return "stub"

    def _run_live(
        self, goal: str, session_id: str, model: str | None = None
    ) -> tuple[str, str, str | None]:
        """Legacy 3-tuple surface: (text, status, model_that_answered)."""
        text, status, used_model, _events = self._run_live_events(goal, session_id, model)
        return text, status, used_model

    def _run_live_events(
        self,
        goal: str,
        session_id: str,
        model: str | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str | None, list[Any]]:
        """One attempt against exactly one model — no ladder, no retries on
        other models. Returns (text, status, model_that_answered, events); the
        third element keeps attribution honest even on failure paths and the
        fourth carries the raw SDK events so real token usage can be metered
        (M14-W4 F1a).

        ``on_progress`` receives one record per tool call / tool result as the
        runtime reports it, so a caller can write the transcript while the run
        is still going. The same records are re-derived from ``events`` after
        the run and anything not already sent is pushed as a final sweep — a
        provider or runtime that ignores the notification channel still yields
        a complete transcript, just not a live one.

        ``images`` are SDK image content blocks (``{"type": "image", "data":
        <canonical base64>, "mimeType": ...}``) attached to the same prompt as
        the goal. They are validated by ``goal_images`` before they get here;
        the runtime rewrites them into its own attachment store, so the base64
        exists only in this call's transport.
        """
        model = model or self.model
        if self.isolated:
            return self._run_isolated(goal, session_id, model, on_progress, images)
        try:
            from deepseek_harness import DeepSeekHarness

            # Fail closed BEFORE any SDK import/call: a bad session id raises
            # ValueError, which the handler below reports as status "error".
            run_cwd = self.run_workspace(session_id)
            os.makedirs(run_cwd, exist_ok=True)
            os.makedirs(self.run_session_dir(session_id), exist_ok=True)
            # Real SDK (0.2.1rc2) surface: explicit dsh_home (never ~/.dsh), a
            # restricted profile instead of the default danger-full-access
            # composition, optional patch files; credentials go to the runtime
            # child via kwargs (no mutation of this process's environment).
            policy_patch = _execution_policy_patch(
                self.run_session_dir(session_id), run_cwd, self._shell_policies.get(session_id)
            )
            kwargs: dict[str, Any] = dict(
                provider=DEFAULT_DSH_PROVIDER,
                model=model,
                cwd=run_cwd,
                dsh_home=self.run_session_dir(session_id),
                # Do not allow an environment override to select a profile
                # whose tool roster or sandbox policy was not reviewed here.
                profile=DEFAULT_DSH_PROFILE,
                api_key=self.api_key,
            )
            max_tokens = _sdk_max_tokens()
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            if self.base_url:
                kwargs["base_url"] = self.base_url
            operator_patches = tuple(
                p for p in os.environ.get("DSH_PATCH_FILES", "").split(os.pathsep) if p
            )
            missing = [p for p in operator_patches if not os.path.exists(p)]
            if missing:  # fail closed: a missing restriction patch must not be skipped
                raise FileNotFoundError(f"DSH_PATCH_FILES entry not found: {missing[0]}")
            kwargs["patches"] = (*operator_patches, policy_patch)
            sent: set[str] = set()

            def _push(record: dict[str, Any]) -> None:
                key = f"{record['phase']}:{record.get('callId')}"
                if key in sent:
                    return
                sent.add(key)
                if on_progress is not None:
                    on_progress(record)

            def _notify(notification: Any) -> None:
                """Runtime notification -> transcript record.

                The SDK hands us whatever the runtime notifies; a session event
                may arrive bare, wrapped in ``event``, or inside ``data``. All
                three shapes are tried rather than assuming one, and anything
                unrecognised is ignored (never guessed at).
                """
                payload = getattr(notification, "payload", notification)
                for candidate in _notification_events(payload):
                    record = self._tool_record(candidate)
                    if record is not None:
                        _push(record)

            with DeepSeekHarness(**kwargs) as harness:
                # Subscribe only when someone is listening: a caller that does
                # not want progress should not pay for the callback, and an
                # engine stub with the narrow historical signature still works.
                session_home = Path(self.run_session_dir(session_id)) / "sessions"
                histories = list(session_home.glob(f"*/{session_id}/session.v4.jsonl"))
                if len(histories) > 1:
                    raise RuntimeError("multiple persisted sessions; refusing to guess")
                run_kwargs: dict[str, Any] = {}
                if on_progress is not None:
                    run_kwargs["on_notification"] = _notify
                if histories:
                    # A paused worker may already have persisted this session. The
                    # SDK's run() starts a new one and rejects an existing ID.
                    # Resume must fail closed if the history cannot be restored.
                    resume_goal = (
                        "The previous attempt was interrupted. Its last tool call may "
                        "not have finished, and the workspace may have been rewound. "
                        "Do not assume previous actions completed: verify the current "
                        "workspace and complete the approved task.\n\n" + goal
                    )
                    result = harness.resume_session(session_id).run(
                        _prompt_input(resume_goal, images), **run_kwargs
                    )
                else:
                    result = harness.run(
                        _prompt_input(goal, images), session_id=session_id, **run_kwargs
                    )
            text = result.final_response
            events = list(result.events or [])
            for record in self._tool_records(events):  # final sweep: nothing lost
                _push(record)
            if getattr(result, "finish_reason", None) == "error":
                message = "runtime reported a failed turn"
                for event in reversed(events):
                    if isinstance(event, dict) and event.get("type") == "turn/end":
                        reason = event.get("data", {}).get("reason", {})
                        failure = reason.get("error") or reason.get("failure") or {}
                        if isinstance(failure, dict):
                            message = str(failure.get("message") or message)
                        break
                return scrub_secrets(message, self.api_key), "error", model, events
            return text, "completed", model, events
        except ImportError:
            return (
                "deepseek-harness-sdk is not installed in this environment.",
                "sdk_missing",
                model,
                [],
            )
        except Exception as exc:  # noqa: BLE001 — surface engine failures to the UI
            return (
                scrub_secrets(f"DeepSeek Harness run failed: {exc}", self.api_key),
                "error",
                model,
                [],
            )

"""The policy-request manifest: what the program asks the Harness for.

This file is the hand-off between the language and the platform. It is a
*request* and says so in the data: the program lists capabilities, the mapping
to Harness permissions is a ceiling, grants are ``none`` and review is required.
A host that reads it must still authenticate the run, resolve the team policy
and check every call; the manifest cannot grant anything by existing.

It also pins what the plan demands be pinned for evidence: a source digest, the
profile, the spec and toolchain versions, and the declared budgets.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from . import ast as A
from .api import CheckResult
from .capabilities import CAPABILITIES, HOST_OPERATIONS
from .version import SPEC_VERSION, TOOLCHAIN_VERSION


def _walk(node: object):
    if not dataclasses.is_dataclass(node) or isinstance(node, type):
        return
    yield node
    for field_info in dataclasses.fields(node):
        value = getattr(node, field_info.name)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            yield from _walk(value)
        elif isinstance(value, tuple):
            for item in value:
                yield from _walk(item)


def _dotted(node: A.Expr) -> tuple[str, ...] | None:
    if isinstance(node, A.Ident):
        return (node.name,)
    if isinstance(node, A.Member):
        base = _dotted(node.base)
        if base is None:
            return None
        return (*base, node.name)
    return None


def _type_name(node: A.TypeRef) -> str:
    """Render a type the way the source wrote it, so ``Agent<P>`` stays legible."""
    name = node.name
    if node.args:
        name = f"{name}<{', '.join(_type_name(arg) for arg in node.args)}>"
    if node.optional:
        name = f"{name}?"
    return name


def _budget(decl: A.BudgetDecl | None) -> dict[str, object] | None:
    if decl is None:
        return None
    budget: dict[str, object] = {}
    for entry in decl.entries:
        value = entry.value
        if entry.key in {"steps", "tool_calls", "tasks"} and value.kind == "int":
            budget[entry.key] = int(value.raw)
        elif entry.key == "cost" and value.currency:
            budget[entry.key] = f"{value.raw} {value.currency}"
        else:
            budget[entry.key] = value.raw
    return budget


def _requests(decl: A.WorkflowDecl) -> list[dict[str, str]]:
    agent_names = {agent.name for agent in decl.agents}
    seen: dict[str, dict[str, str]] = {}
    for node in _walk(decl.body):
        if not isinstance(node, A.Call):
            continue
        dotted = _dotted(node.callee)
        if dotted is None:
            continue
        if len(dotted) == 2 and dotted[0] in agent_names:
            operation = ".".join(dotted)
            capability = "model.invoke"
        elif ".".join(dotted) in HOST_OPERATIONS:
            operation = ".".join(dotted)
            capability = HOST_OPERATIONS[operation].capability
            if capability is None:
                continue
        else:
            continue
        seen.setdefault(capability, {"capability": capability, "operation": operation})
    return [seen[name] for name in sorted(seen)]


def build_manifest(result: CheckResult, profile: str) -> dict[str, object]:
    """Build the JSON object. Refuses nothing — callers must check ``result.ok``."""
    workflows = []
    agents = []
    for decl in result.program.decls:
        if isinstance(decl, A.WorkflowDecl):
            workflows.append(
                {
                    "name": decl.name,
                    "returns": _type_name(decl.returns),
                    "requires": sorted(
                        capability.name
                        for capability in (decl.requires.capabilities if decl.requires else ())
                    ),
                    "requests": _requests(decl),
                    "budget": _budget(decl.budget),
                }
            )
        elif isinstance(decl, A.AgentDecl):
            agents.append(
                {
                    "name": decl.name,
                    "type": _type_name(decl.type),
                    "settings": sorted(setting.key for setting in decl.settings),
                }
            )
    digest = hashlib.sha256(result.source.encode("utf-8")).hexdigest()
    sources = [
        {
            "module": result.program.module,
            "path": result.path,
            "sha256": digest,
            "bytes": len(result.source.encode("utf-8")),
        }
    ]
    for module in getattr(result, "modules", ()):
        sources.append(
            {
                "module": module.name,
                "path": module.path,
                "sha256": hashlib.sha256(module.source.encode("utf-8")).hexdigest(),
                "bytes": len(module.source.encode("utf-8")),
            }
        )
    # One digest over the whole file set. `source.sha256` still answers "is this the
    # entry I checked"; this answers "is this the program I checked", which since M8
    # is not the same question.
    #
    # It is taken over the *declared module names*, never the paths. A path says where a
    # file happened to sit for this one run, so a digest that mixed paths in would move
    # when the same program was checked out somewhere else — and an approved digest that
    # cannot survive a clone or a container mount is not an approval. The path stays in
    # `sources` because a person reading a manifest wants to know which file they are
    # looking at; it is simply not part of what the program is.
    by_module = sorted(sources, key=lambda entry: str(entry["module"]))
    program_digest = hashlib.sha256(
        json.dumps(
            [{"module": entry["module"], "sha256": entry["sha256"]} for entry in by_module],
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    requested = sorted(
        {
            capability
            for workflow in workflows
            for capability in workflow["requires"]
        }
    )
    return {
        "language": "hx",
        "spec_version": SPEC_VERSION,
        "toolchain": {"name": "hxc", "version": TOOLCHAIN_VERSION},
        "profile": profile,
        "module": result.program.module,
        "source": {
            "path": result.path,
            "sha256": digest,
            "bytes": len(result.source.encode("utf-8")),
        },
        # M8: every file that built this tree, named by the module it declares, each
        # with its own digest. A run bound to this manifest is bound to all of them, so
        # swapping a helper is as refused as swapping the workflow itself.
        "sources": by_module,
        "program_sha256": program_digest,
        "agents": agents,
        "workflows": workflows,
        "policy_request": {
            "authority": "harness",
            "grants": "none",
            "requested_capabilities": requested,
            "permission_ceiling": {
                name: CAPABILITIES[name].permission for name in requested if name in CAPABILITIES
            },
            "approval": "required-for-review",
            "forgeable": False,
        },
    }


def render_manifest(result: CheckResult, profile: str) -> str:
    return json.dumps(build_manifest(result, profile), indent=2, sort_keys=True) + "\n"

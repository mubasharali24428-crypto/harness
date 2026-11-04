"""What an Hx program may ask for, and what that request means to the Harness.

Two tables, both closed on purpose:

* ``CAPABILITIES`` — the only names that may appear after ``requires``. Each
  maps to a Harness permission so the emitted manifest speaks the platform's
  vocabulary instead of inventing a parallel one. The mapping is a *ceiling*
  description, not a grant: the host still decides, and nothing in this table
  includes ``approve`` or ``administer`` because a program may never hold them.
* ``HOST_OPERATIONS`` — the closed set of effects a workflow may invoke. There
  is no ambient namespace: ``exec``, ``curl``, ``open`` and friends are not
  missing, they are forbidden, and the checker says so by name.

Everything else — filesystem, processes, sockets, dynamic module loading — is
deliberately absent. Absence is the security boundary; the spec documents it and
the tests pin it, so a future addition has to fight a failing test rather than
slip in through a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    permission: str
    summary: str


CAPABILITIES: dict[str, Capability] = {
    "model.invoke": Capability(
        "model.invoke",
        "read",
        "Call a declared agent. Routed by the Harness model policy; never downgraded silently.",
    ),
    "repo.read": Capability(
        "repo.read",
        "read",
        "Read repository files and listings inside the allowed paths.",
    ),
    "repo.propose_patch": Capability(
        "repo.propose_patch",
        "comment",
        "Propose a patch for human review. Never a write, never a merge.",
    ),
    "tool.test": Capability(
        "tool.test",
        "read",
        "Run an allow-listed test command in the confined worker.",
    ),
}


@dataclass(frozen=True)
class HostOperation:
    name: str
    capability: str | None
    params: tuple[tuple[str, str], ...]
    returns: str
    summary: str

    @property
    def is_effect(self) -> bool:
        return self.capability is not None


HOST_OPERATIONS: dict[str, HostOperation] = {
    "repo.read": HostOperation(
        "repo.read",
        "repo.read",
        (("path", "String"),),
        "RepoFile",
        "Read one file. The result is untrusted data and must match a schema.",
    ),
    "repo.list": HostOperation(
        "repo.list",
        "repo.read",
        (("prefix", "String"),),
        "List<RepoFile>",
        "List repository entries under a prefix.",
    ),
    "repo.propose_patch": HostOperation(
        "repo.propose_patch",
        "repo.propose_patch",
        (("changes", "Any"),),
        "Patch",
        "Propose a patch as reviewable evidence. No write, no merge.",
    ),
    "verify.tests": HostOperation(
        "verify.tests",
        "tool.test",
        (("target", "Any"), ("suite", "String")),
        "Evidence",
        "Run an allow-listed suite and return evidence, not a verdict.",
    ),
    "review.bundle": HostOperation(
        "review.bundle",
        None,
        (("changes", "Any"), ("evidence", "Any")),
        "ReviewBundle",
        "Assemble a review bundle. Pure: it requests no capability and approves nothing.",
    ),
}

# Agent operations. Kept closed so a typo is a diagnostic instead of a method
# the host would have to invent at runtime.
AGENT_OPERATIONS: tuple[str, ...] = ("plan", "propose", "review")

# Names that would imply ambient host access. Rejected by name wherever they
# appear (call, member base, or declaration) so an author gets a clear message
# instead of "unknown identifier" six lines later.
FORBIDDEN_AMBIENT: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "system",
        "shell",
        "sh",
        "bash",
        "process",
        "os",
        "fs",
        "open",
        "curl",
        "http",
        "https",
        "socket",
        "net",
        "fetch",
        "load",
        "require",
        "import",
    }
)

# Bases that would be an attempt to grant or change authority from source.
FORBIDDEN_AUTHORITY: frozenset[str] = frozenset(
    {"policy", "policies", "grant", "grants", "capability", "capabilities"}
)


def capability_names() -> str:
    return ", ".join(sorted(CAPABILITIES))


def operation_names() -> str:
    return ", ".join(sorted(HOST_OPERATIONS))

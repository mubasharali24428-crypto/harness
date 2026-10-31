"""Typed runtime failures for the Hx host runner.

The checker (``harness_hx``) answers "is this program well formed?". These are
the failures only a run can produce: policy said no, the budget ran out, the
host returned something the declared schema does not describe, a human
cancelled.

Every failure carries a stable code in the same ``HX`` namespace the checker
uses, and one ``status`` string. Statuses are the vocabulary a caller may act
on, so they are a closed set and not free text:

``refused``   — never ran (check failed, digest mismatch, unsupported construct)
``denied``    — the host's policy said no
``budget``    — a declared limit was exhausted
``cancelled`` — a human or a deadline stopped the run
``error``     — the program or the host failed
"""

from __future__ import annotations

from typing import Any

from .secrets import scrub_text, scrub_value

STATUSES: tuple[str, ...] = ("ok", "refused", "denied", "budget", "cancelled", "error")


class HxHostError(Exception):
    """Base class: one failure, one code, one status, one position if known.

    The message and detail are scrubbed where they are created. A host adapter's
    exception may quote a response header, and an error travels further than the
    value that caused it — into a log, an event, the evidence bundle — so this is
    the one place where every failure is made safe once.
    """

    status = "error"
    code = "HXH000"

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        col: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = scrub_text(message) if isinstance(message, str) else message
        self.line = line
        self.col = col
        self.detail = scrub_value(detail or {})

    def as_record(self, path: str | None = None) -> dict[str, object]:
        """The frozen Section 5 wire shape, so a runtime failure reads like a check."""
        record: dict[str, object] = {}
        if path is not None:
            record["path"] = path
        if self.line is not None:
            record["line"] = self.line
        if self.col is not None:
            record["col"] = self.col
        record["kind"] = "error"
        record["message"] = f"[{self.code}] {self.message}"
        return record


class CheckRefused(HxHostError):
    """The checker found errors; nothing was executed."""

    status = "refused"
    code = "HXH101"


class NotRunnable(HxHostError):
    """Well formed, but outside the construct set this runner executes."""

    status = "refused"
    code = "HXH102"


class DigestMismatch(HxHostError):
    """The run was authorised for a different source than the one supplied."""

    status = "refused"
    code = "HXH103"


class ScriptError(HxHostError):
    """The program itself failed: ``ensure`` was false, a value did not fit its type."""

    code = "HXH201"


class CapabilityDenied(HxHostError):
    """A call needed a capability the host did not grant."""

    status = "denied"
    code = "HXH401"


class HostUnavailable(HxHostError):
    """The operation is real but this host has no adapter wired for it."""

    code = "HXH403"


class InvalidHostResult(HxHostError):
    """A host result did not match the schema the program declared."""

    code = "HXH301"


class BudgetExhausted(HxHostError):
    """A declared budget was exceeded before the workflow finished."""

    status = "budget"
    code = "HXH501"


class CostUnpriceable(HxHostError):
    """The run declares ``budget.cost`` but the host's table has no price for this
    operation. Refused rather than run for free: an unpriced crossing is a hole in
    the ceiling, and a ceiling with a hole in it is not a limit."""

    status = "budget"
    code = "HXH502"


class BadPriceTable(HxHostError):
    """The host supplied a price table that is not usable. Never a program's fault."""

    status = "refused"
    code = "HXH503"


class StaleAuthority(HxHostError):
    """A decision handed to the runner does not belong to this program.

    Mid-run revocation means the host owns the decision object, and with that
    comes the possibility of passing one built for a different request — or one
    that grants a capability no program may hold. The runner refuses the
    authority instead of borrowing it.
    """

    status = "denied"
    code = "HXH404"


class Cancelled(HxHostError):
    """A cancellation request or the declared duration ended the run."""

    status = "cancelled"
    code = "HXH601"

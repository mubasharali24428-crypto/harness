"""P-15 (I-02): GitHub verdict comment — a pure, offline builder.

Renders an advisory Markdown summary of the RECORDED facts about one agent run,
for a single PR comment the bot creates once and updates in place (hidden marker).
It never approves or merges; a human merges. Unknown facts stay unknown — never
"passed", "$0", "safe" or "approved" unless that exact fact was recorded. Every
free-text value is secret-scrubbed and length-clamped; no raw diffs, tool output,
logs, prompts, env values or file contents are ever included (the builder reads
only the structured fact keys below and ignores anything else).

Lives in section4 (where the recorded facts are read) so no cross-section import
is needed. Risk flags / the adversarial digest are passed in as data: section8
owns them and section4 must not import section8 (isolation law). The digest is
advisory and is rendered only when it was recorded.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence

from .redact import scrub_secret_patterns, scrub_secrets

MARKER_PREFIX = "<!-- harness-verdict:v1 run="
FOOTER = "Advisory summary of recorded facts. Not an approval to merge. A human merges."
MAX_COMMENT_CHARS = 60000  # safely below GitHub's 65536-char comment limit
_FIELD_CLAMP = 2000
_TRUNCATED_NOTE = "\n\n_(truncated: exceeded the comment length cap)_"


_RUN_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def safe_run_id(run_id) -> str:
    """Run ids are server-generated, but the marker is also a lookup key and sits
    inside an HTML comment, so anything outside [A-Za-z0-9_.-] becomes ``_``."""
    return _RUN_ID_UNSAFE.sub("_", str(run_id or "unknown"))[:200] or "unknown"


def verdict_marker(run_id: str) -> str:
    """The hidden marker the posting path searches for to update one comment in place."""
    return f"{MARKER_PREFIX}{safe_run_id(run_id)} -->"


def _neutralize(text: str) -> str:
    """Make free text inert in GitHub Markdown: no mentions, issue cross-refs,
    links/images, raw HTML, backtick breakouts or auto-links."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("`", "'")
    text = text.replace("[", "\\[").replace("]", "\\]")
    # the scrubber's own placeholders are inert text; keep them readable
    text = text.replace("\\[redacted\\]", "[redacted]").replace(
        "\\[redacted-private-key\\]", "[redacted-private-key]"
    )
    text = re.sub(r"@(?=[A-Za-z0-9_])", "@\u200b", text)
    text = re.sub(r"#(?=\d)", "#\u200b", text)
    return text.replace("://", ":\u200b//")


def _text(
    value, secrets: Sequence[str], clamp: int = _FIELD_CLAMP, code: bool = False
) -> str | None:
    """Scrub, flatten and clamp a value. ``code=True`` is for text the builder wraps in a
    code span (only the backtick must go); everything else is neutralized."""
    if value is None:
        return None
    cleaned = scrub_secret_patterns(scrub_secrets(str(value), *secrets))
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").strip()
    if len(cleaned) > clamp:
        cleaned = cleaned[:clamp] + "…"
    cleaned = cleaned.replace("`", "'") if code else _neutralize(cleaned)
    return cleaned or None


def _usd(value) -> float | None:
    """A recorded, finite, non-negative dollar amount, else None (unknown)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def _plan_section(facts: dict, secrets: Sequence[str]) -> str:
    plan = facts.get("plan") or {}
    by = _text(plan.get("approved_by_display") or plan.get("approved_by"), secrets)
    at = _text(plan.get("approved_at"), secrets)
    if not by and not at:
        return "## Plan approved by\n- not recorded\n"
    line = f"- {by}" if by else "- approver not recorded"
    if at:
        line += f" at {at}"
    return "## Plan approved by\n" + line + "\n"


def _sandbox_section(facts: dict, secrets: Sequence[str]) -> str:
    sb = facts.get("sandbox") or {}
    backend = _text(sb.get("backend"), secrets)
    mode = _text(sb.get("mode"), secrets)
    if not backend and not mode:
        return "## Sandbox boundary\n- not recorded\n"
    parts = [
        f"backend: {backend}" if backend else "backend: not recorded",
        f"confinement mode: {mode}" if mode else "confinement mode: not recorded",
    ]
    return "## Sandbox boundary\n- " + "; ".join(parts) + "\n"


def _tests_section(facts: dict, secrets: Sequence[str]) -> str:
    tests = facts.get("tests")
    lines = ["## Tests"]
    if not tests or not tests.get("known"):
        lines.append("- unknown (no recorded tool calls)")
        return "\n".join(lines) + "\n"
    runs = tests.get("runs") or []
    if not runs:
        lines.append("- not run (NO_TESTS_RUN)")
    else:
        for r in runs[:20]:
            cmd = _text(r.get("command"), secrets, 200, code=True) or "(command not recorded)"
            result = _text(r.get("result"), secrets, 40) or "unknown"
            lines.append(f"- `{cmd}`: {result}")
    for u in (facts.get("uncertainty") or [])[:10]:
        code = _text(u.get("code") if isinstance(u, dict) else u, secrets, 80)
        if code:
            lines.append(f"- uncertainty: {code}")
    return "\n".join(lines) + "\n"


def _cost_section(facts: dict, secrets: Sequence[str]) -> str:
    cost = facts.get("cost") or {}
    actual = _usd(cost.get("actual_usd"))
    estimated = _usd(cost.get("estimated_usd"))
    lines = ["## Cost"]
    if actual is None:
        lines.append("- actual: unknown")
    else:
        lines.append(f"- actual: ${actual:.6f} (cost_ledger)")
    if estimated is not None:
        lines.append(f"- estimated: ${estimated:.6f} (plan estimate, not actual)")
    return "\n".join(lines) + "\n"


def _risk_section(facts: dict, secrets: Sequence[str]) -> str:
    flags = facts.get("risk_flags")
    lines = ["## Risk flags"]
    if flags is None:
        lines.append("- not recorded (risk analysis unavailable)")
    elif not flags:
        lines.append("- none flagged")
    else:
        for f in flags[:20]:
            path = _text(f.get("path"), secrets, 200, code=True) or "(path not recorded)"
            sev = _text(f.get("severity"), secrets, 40) or "unknown"
            reason = _text(f.get("reason"), secrets, 200) or ""
            lines.append(f"- {sev}: `{path}` — {reason}".rstrip(" —"))
    digest = facts.get("adversarial_digest")
    if digest:
        lines.append("- adversarial pre-review (advisory, recorded):")
        for c in (digest.get("concerns") or [])[:10]:
            ct = _text(c, secrets, 200)
            if ct:
                lines.append(f"  - {ct}")
        model = _text(digest.get("model_used"), secrets, 80)
        if model:
            lines.append(f"  - model: {model}")
    return "\n".join(lines) + "\n"


def build_verdict_comment(facts: dict, *, pr_number: int, secrets: Sequence[str] = ()) -> str:
    """Render the advisory verdict comment for one run + PR from recorded facts.

    ``facts`` keys (all optional; missing/None => unknown / not recorded):
      run_id, goal, plan{approved_by, approved_by_display, approved_at},
      sandbox{backend, mode}, tests{known, runs[{command, result}]},
      uncertainty[{code, text}], cost{actual_usd, estimated_usd},
      risk_flags[{path, severity, reason}], adversarial_digest{concerns, model_used}.
    ``secrets`` are known literal secret values to scrub in addition to patterns.
    """
    run_id = safe_run_id(facts.get("run_id"))
    secrets = [s for s in secrets if s]
    marker = verdict_marker(run_id) + "\n\n"
    goal = _text(facts.get("goal"), secrets, 300)
    goal_line = f"Goal: {goal}\n\n" if goal else ""
    body = (
        f"# Harness verdict — PR #{pr_number}\n\n"
        + _plan_section(facts, secrets) + "\n"
        + _sandbox_section(facts, secrets) + "\n"
        + _tests_section(facts, secrets) + "\n"
        + _cost_section(facts, secrets) + "\n"
        + _risk_section(facts, secrets) + "\n"
        + goal_line
        + FOOTER + "\n"
    )
    full = marker + body
    if len(full) > MAX_COMMENT_CHARS:
        budget = MAX_COMMENT_CHARS - len(marker) - len(FOOTER) - len(_TRUNCATED_NOTE) - 4
        full = marker + body[:budget] + _TRUNCATED_NOTE + "\n\n" + FOOTER + "\n"
    return full


__all__ = ["build_verdict_comment", "verdict_marker", "safe_run_id", "FOOTER", "MAX_COMMENT_CHARS"]
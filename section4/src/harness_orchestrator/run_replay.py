"""F111 — run replay: faithful, ordered, honest-about-absence reconstruction.

Review gap: "Events/rehydration/demo replay; not every prompt/tool/file
observation captured." This module closes the observability half:

* :func:`build_run_record` — the durable record of what execution actually
  saw and said (composed prompt text + meta, engine result, engine
  observations), redacted BEFORE persistence (replay_archive law);
* :func:`summarize_engine_events` — bounded, shape-tolerant digest of the
  live SDK event stream (the only place tool/file observations exist);
* :func:`build_replay_view` — merges the durable event trail (ordered) with
  the archived record into the replay response. Anything missing is an
  explicit ``unavailable`` entry with a reason — never silently absent.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from harness_orchestrator.replay_archive import redact_record

#: Bounds: the record is a digest, not a transcript.
MAX_PROMPT_CHARS = 64_000
MAX_OBSERVATIONS = 200
MAX_OBSERVATION_TEXT = 400
MAX_TIMELINE_EVENTS = 500


def summarize_engine_events(
    events: Any,
    *,
    max_items: int = MAX_OBSERVATIONS,
    max_text: int = MAX_OBSERVATION_TEXT,
) -> list[dict[str, Any]]:
    """Best-effort structured digest of the SDK event stream, IN ORDER.

    Shape-tolerant by design: the live SDK may add event kinds this code has
    never seen (the wrapper's docstring documents the usage-chunk shape; tool
    chunks ride the same ``assistant/chunk`` envelope). Unknown shapes are
    reduced to their type labels — dropped detail is honest, crashes are not.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(events, (list, tuple)):
        return out
    for event in events:
        if len(out) >= max_items:
            out.append({"kind": "truncated", "reason": f"over {max_items} observations"})
            break
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        chunk = data.get("chunk") if isinstance(data, dict) else None
        chunks = chunk if isinstance(chunk, dict) else {}
        entry: dict[str, Any] = {"kind": str(event.get("type") or "unknown")}
        chunk_type = chunks.get("type")
        if chunk_type is not None:
            entry["chunkType"] = str(chunk_type)
        for field in ("toolName", "path", "name", "id"):
            if chunks.get(field) is not None:
                entry[field] = str(chunks[field])[:max_text]
        for field in ("text", "content", "result"):
            value = chunks.get(field)
            if isinstance(value, str) and value:
                capped = value[:max_text]
                entry["text"] = capped
                if len(value) > max_text:
                    entry["textTruncated"] = True
                break
        out.append(entry)
    return out


def build_run_record(
    *,
    run_id: str,
    prompt_text: str,
    prompt_meta: dict[str, Any],
    engine_result: dict[str, Any],
    instructions_text: str = "",
    secrets: tuple[str | None, ...] = (),
) -> dict[str, Any]:
    """Assemble + REDACT one run's replay record (pure; safe to unit test)."""
    prompt_capped = str(prompt_text or "")
    prompt_truncated = len(prompt_capped) > MAX_PROMPT_CHARS
    if prompt_truncated:
        prompt_capped = prompt_capped[:MAX_PROMPT_CHARS]

    instructions = str(instructions_text or "")
    observations = engine_result.get("observations")
    record: dict[str, Any] = {
        "runId": run_id,
        "prompt": {
            "available": bool(prompt_capped),
            "text": prompt_capped,
            "truncated": prompt_truncated,
            "meta": dict(prompt_meta or {}),
        },
        # F018 durable decision memory: WHICH instruction text execution ran
        # with, content-hashed and timestamped — the record body itself is
        # content-addressed, so this pin survives every later rules edit.
        "instructions": {
            "available": bool(instructions),
            "sha256": (
                hashlib.sha256(instructions.encode("utf-8")).hexdigest()
                if instructions
                else None
            ),
            "bytes": len(instructions.encode("utf-8")),
            "recordedAt": datetime.now(UTC).isoformat(),
        },
        "engineResult": {
            k: v for k, v in dict(engine_result or {}).items() if k != "observations"
        },
        "observations": list(observations) if isinstance(observations, list) else [],
    }
    redacted, replacements = redact_record(record, secrets=secrets)
    redacted["redaction"] = {"applied": replacements > 0, "replacements": replacements}
    return redacted


def _event_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Observation entries derived from the durable event trail, IN ORDER."""
    obs: list[dict[str, Any]] = []
    for row in rows:
        etype = str(row.get("type") or "")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        base = {"seq": row.get("seq"), "ts": row.get("ts")}
        if etype == "terminal_output":
            obs.append({**base, "kind": "tool_output", "line": payload.get("line")})
        elif etype == "diff_progress":
            obs.append(
                {
                    **base,
                    "kind": "file_observation",
                    "path": payload.get("path"),
                    "checkpoint": payload.get("checkpoint"),
                }
            )
        elif etype in ("engine_completed", "engine_failed"):
            obs.append(
                {
                    **base,
                    "kind": "engine_settled",
                    "status": "completed" if etype == "engine_completed" else "failed",
                    "summary": payload.get("summary") or payload.get("reason"),
                }
            )
    return obs


def latest_context_ref(rows: list[dict[str, Any]]) -> Any:
    """The most recent replay-record reference on the settlement trail."""
    for row in reversed(rows):
        if str(row.get("type") or "") not in ("engine_completed", "engine_failed"):
            continue
        payload = row.get("payload")
        if isinstance(payload, dict) and payload.get("contextRef"):
            return payload["contextRef"]
    return None


def build_replay_view(
    *,
    run_id: str,
    rows: list[dict[str, Any]],
    record: dict[str, Any] | None,
    record_ref: Any,
) -> dict[str, Any]:
    """Merge the durable trail + archived record into the replay response."""
    timeline = [
        {
            "seq": row.get("seq"),
            "ts": row.get("ts"),
            "type": row.get("type"),
            "payload": row.get("payload") if isinstance(row.get("payload"), dict) else {},
        }
        for row in rows[:MAX_TIMELINE_EVENTS]
    ]
    unavailable: list[dict[str, str]] = []
    if record is None:
        unavailable.append(
            {
                "what": "execution prompt / engine result / engine observations",
                "reason": (
                    "no durable replay record referenced by this run's event trail"
                    + ("" if record_ref else " (no contextRef on any settlement event)")
                ),
            }
        )
    observations = _event_observations(rows)
    settle_marks = [o for o in observations if o.get("kind") == "engine_settled"]
    observations = [o for o in observations if o.get("kind") != "engine_settled"]
    if record is not None:
        observations.extend(
            {**o, "kind": str(o.get("kind") or "engine_observation")}
            if isinstance(o, dict)
            else {"kind": "engine_observation", "value": o}
            for o in record.get("observations") or []
        )
    # Engine-run observations belong INSIDE the run, before the settle mark.
    observations.extend(settle_marks)
    return {
        "runId": run_id,
        "available": record is not None,
        "timeline": timeline,
        "timelineTruncated": len(rows) > MAX_TIMELINE_EVENTS,
        "prompt": (record or {}).get("prompt")
        or {"available": False, "reason": unavailable[0]["reason"]},
        "instructions": (record or {}).get("instructions")
        or {"available": False, "reason": unavailable[0]["reason"]},
        "engineResult": (record or {}).get("engineResult"),
        "observations": observations,
        "recordRef": record_ref,
        "redaction": (record or {}).get("redaction")
        or {"applied": False, "replacements": 0},
        "unavailable": unavailable,
    }

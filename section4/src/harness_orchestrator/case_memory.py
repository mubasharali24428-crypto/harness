"""Task 9 — case-base error memory on top of the I-08 denial-learning loop.

A *case* is one remembered failure: what class it was, a fingerprint of where it happened,
how it ended, and the bounded rule text a human could save. Similar past cases can be shown
next to a new denial stop so the human sees "this happened before, and this is what fixed it".

Laws (CD-02, denial_learning.py):

* A case is always ``status="proposed"``. Nothing here writes a project rule or injects text
  into a prompt; promotion stays the human PUT on the existing rules API.
* Deterministic: same inputs -> same fingerprint, same ranking (ties broken by case id).
* Old cases fade: weight halves every ``half_life_hours`` (default one week).

ponytail: in-memory similarity (path-set Jaccard + same-tool bonus) over a caller-supplied
list; a case_memory table + pgvector fingerprint search are the upgrade once cases are
persisted by the denial stop.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Literal

from harness_orchestrator.denial_learning import suggested_rule_text

Outcome = Literal["recovered", "denied", "repeat"]
OUTCOME_WEIGHT = {"recovered": 1.0, "denied": 0.6, "repeat": 0.3}  # what fixed it ranks first
DEFAULT_HALF_LIFE_HOURS = 168.0


def fingerprint(failure_class: str, tool: str, paths: list[str]) -> str:
    body = "\0".join([failure_class, tool, *sorted(set(paths))])
    return hashlib.sha256(body.encode()).hexdigest()


def record_case(run_id: str, failure_class: str, tool: str, paths: list[str],
                outcome: Outcome, now: datetime) -> dict[str, Any]:
    if outcome not in OUTCOME_WEIGHT:
        raise ValueError(f"unknown outcome {outcome!r}")
    return {
        "caseId": str(uuid.uuid4()), "runId": run_id, "failureClass": failure_class,
        "tool": tool, "paths": sorted(set(paths)),
        "fingerprint": fingerprint(failure_class, tool, paths),
        "outcome": outcome, "weight": OUTCOME_WEIGHT[outcome], "recordedAt": now,
        "suggestedRule": suggested_rule_text(run_id, [{"path": p} for p in sorted(set(paths))]),
        "status": "proposed",  # CD-02: never auto-applied
    }


def decayed(weight: float, recorded_at: datetime, now: datetime,
            half_life_hours: float = DEFAULT_HALF_LIFE_HOURS) -> float:
    age_h = max(0.0, (now - recorded_at).total_seconds() / 3600)
    return weight * 0.5 ** (age_h / half_life_hours)


def similarity(query_paths: list[str], tool: str, case: dict[str, Any]) -> float:
    q, c = set(query_paths), set(case["paths"])
    jaccard = len(q & c) / len(q | c) if q | c else 0.0
    return jaccard + (0.25 if case["tool"] == tool else 0.0)


def retrieve_similar_cases(cases: list[dict[str, Any]], query_paths: list[str], tool: str,
                           now: datetime, limit: int = 5,
                           half_life_hours: float = DEFAULT_HALF_LIFE_HOURS) -> list[dict]:
    scored = []
    for c in cases:
        sim = similarity(query_paths, tool, c)
        if sim <= 0:
            continue
        score = sim * decayed(c["weight"], c["recordedAt"], now, half_life_hours)
        scored.append((score, c["caseId"], c))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [{**c, "score": round(s, 6)} for s, _, c in scored[:limit]]

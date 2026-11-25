"""Task 9 — case-base error memory on top of the I-08 denial-learning loop.

A *case* is one remembered failure: what class it was, a fingerprint of where it happened,
how it ended, and the bounded rule text a human could save. Similar past cases can be shown
next to a new denial stop so the human sees "this happened before, and this is what fixed it".

Laws (CD-02, denial_learning.py):

* A case is always ``status="proposed"``. Nothing here writes a project rule or injects text
  into a prompt; promotion stays the human PUT on the existing rules API.
* Deterministic: same inputs -> same fingerprint, same ranking (ties broken by case id).
* Old cases fade: weight halves every ``half_life_hours`` (default one week).

Persistence (migration 0080): ``recall_and_record`` is what the denial stop calls. It reads
the team's recent cases FIRST (so a case never matches itself), ranks them, then stores the
new one. Every query filters ``team_id``.

Candidate set (Track 2 hardening, 2026-11-25): similarity still runs in Python, but over the
union of (a) the team's newest ``MAX_SCAN`` cases and (b) up to ``MAX_FINGERPRINT_MATCHES``
cases whose fingerprint equals the query's, at any age. (b) is an equality lookup on the
fingerprint column inside the team, so an exact repeat of a denial is recalled even after
the team has logged more than ``MAX_SCAN`` newer cases. The union only ever adds candidates;
for a team with at most ``MAX_SCAN`` cases the result is identical to the old window scan.
Partial-overlap cases older than the window are still not seen; a path-token index (or
pgvector, which 0030 installs only when the host image ships it) is the proposed next step.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text

from harness_orchestrator.denial_learning import suggested_rule_text

Outcome = Literal["recovered", "denied", "repeat"]
OUTCOME_WEIGHT = {"recovered": 1.0, "denied": 0.6, "repeat": 0.3}  # what fixed it ranks first
DEFAULT_HALF_LIFE_HOURS = 168.0
MAX_SCAN = 200  # newest cases per team considered for similarity
MAX_RECALLED = 3  # what rides on the run record next to a stop
MAX_FINGERPRINT_MATCHES = 50  # exact repeats fetched beyond the window, newest first


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


def _aware(ts: Any) -> datetime:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _row_to_case(r: Any) -> dict[str, Any]:
    return {"caseId": r["case_id"], "runId": r["run_id"], "failureClass": r["failure_class"],
            "tool": r["tool"], "paths": json.loads(r["paths"]), "fingerprint": r["fingerprint"],
            "outcome": r["outcome"], "weight": float(r["weight"]),
            "recordedAt": _aware(r["recorded_at"]), "suggestedRule": r["suggested_rule"],
            "status": r["status"]}


async def load_cases(db: Any, team_id: str) -> list[dict[str, Any]]:
    """The team's newest ``MAX_SCAN`` cases (ties on recorded_at broken by case_id)."""
    rows = (await db.execute(text(
        "SELECT * FROM case_memory WHERE team_id = :t"
        " ORDER BY recorded_at DESC, case_id LIMIT :n"),
        {"t": team_id, "n": MAX_SCAN})).mappings().all()
    return [_row_to_case(r) for r in rows]


async def load_fingerprint_matches(db: Any, team_id: str, fp: str) -> list[dict[str, Any]]:
    """Exact repeats of ``fp`` in this team, any age, newest ``MAX_FINGERPRINT_MATCHES``."""
    rows = (await db.execute(text(
        "SELECT * FROM case_memory WHERE team_id = :t AND fingerprint = :fp"
        " ORDER BY recorded_at DESC, case_id LIMIT :n"),
        {"t": team_id, "fp": fp, "n": MAX_FINGERPRINT_MATCHES})).mappings().all()
    return [_row_to_case(r) for r in rows]


def merge_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union of candidate lists, first occurrence of each caseId wins, order preserved."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        for c in group:
            if c["caseId"] not in seen:
                seen.add(c["caseId"])
                out.append(c)
    return out


async def load_candidates(db: Any, team_id: str, fp: str) -> list[dict[str, Any]]:
    """Window scan plus exact-fingerprint lookup, both team-scoped (see module docstring)."""
    return merge_candidates(await load_cases(db, team_id),
                            await load_fingerprint_matches(db, team_id, fp))


async def recall_and_record(factory: Any, team_id: str, project_id: str | None, run_id: str,
                            failure_class: str, tool: str, paths: list[str],
                            now: datetime) -> list[dict[str, Any]]:
    """Similar earlier cases (bounded, JSON-safe), then persist this one as ``denied``."""
    case = record_case(run_id, failure_class, tool, paths, "denied", now)
    async with factory() as db:
        candidates = await load_candidates(db, team_id, case["fingerprint"])
        similar = retrieve_similar_cases(candidates, case["paths"], tool, now, limit=MAX_RECALLED)
        await db.execute(text(
            "INSERT INTO case_memory (case_id, team_id, project_id, run_id, failure_class, tool,"
            " paths, fingerprint, outcome, weight, suggested_rule, status, recorded_at) VALUES"
            " (:c, :t, :p, :r, :f, :tool, :paths, :fp, :o, :w, :s, 'proposed', :at)"),
            {"c": case["caseId"], "t": team_id, "p": project_id, "r": run_id, "f": failure_class,
             "tool": tool, "paths": json.dumps(case["paths"]), "fp": case["fingerprint"],
             "o": case["outcome"], "w": case["weight"], "s": case["suggestedRule"], "at": now})
        await db.commit()
    return [{"caseId": c["caseId"], "runId": c["runId"], "outcome": c["outcome"],
             "score": c["score"], "suggestedRule": c["suggestedRule"]} for c in similar]

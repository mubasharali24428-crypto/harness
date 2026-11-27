"""X-12 prototype: simplicial (edge-storing triangle) context store — candidate experiment.

Design: docs/briefs/harness-10x-plan-2026-11-22.md §3. In-memory, stdlib only; the production
shape would be Postgres rows + pgvector HNSW (section11), which this does not touch.

* A Face is a first-class 3-way fact (e.g. decision D on file F in task T) with provenance.
  Retrieval returns whole faces, never three orphaned pairwise edges.
* Retrieval = ANN top-k (brute-force cosine here; pgvector HNSW 256-d in production) followed
  by a pluggable rescore. Default rescore is identity; ``poincare_rescore`` is the hyperbolic
  option. Coordinates are logical and NEVER serialized into anything prompt-bound.
* Curators only PROPOSE similarity edges into a review queue (CD-02); nothing is live until a
  named reviewer accepts it.
* ``serialize()`` is hash-ordered (sorted by face digest), never insertion-ordered, so the same
  set of facts always yields the same bytes (S-02 prefix-cache stability).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_FACES = 20  # mirrors section4 memory_loader.MAX_MEMORY_ENTRIES (per project)


def _digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Face:
    entities: tuple[str, str, str]  # sorted; a triangle has no privileged corner
    fact: str
    provenance: dict = field(hash=False)  # reviewer, timestamp, revision
    embedding: tuple[float, ...] = field(hash=False, repr=False)

    @property
    def digest(self) -> str:
        return _digest({"e": self.entities, "f": self.fact, "p": self.provenance})

    def edges(self) -> list[tuple[str, str]]:
        a, b, c = self.entities
        return [(a, b), (a, c), (b, c)]

    def prompt_view(self) -> dict:
        """What may enter a prompt: the fact and its provenance. No vector, no coordinates."""
        return {"entities": list(self.entities), "fact": self.fact, "provenance": self.provenance}


def make_face(a: str, b: str, c: str, fact: str, provenance: dict, embedding) -> Face:
    ents = tuple(sorted((a, b, c)))
    if len(set(ents)) != 3:
        raise ValueError("a face needs three distinct entities")
    if not {"reviewer", "timestamp", "revision"} <= provenance.keys():
        raise ValueError("provenance needs reviewer, timestamp, revision")
    return Face(ents, fact, dict(provenance), tuple(float(x) for x in embedding))


def cosine(u, v) -> float:
    nu, nv = math.sqrt(sum(x * x for x in u)), math.sqrt(sum(x * x for x in v))
    return 0.0 if nu == 0 or nv == 0 else sum(x * y for x, y in zip(u, v, strict=True)) / (nu * nv)


def poincare_distance(u, v) -> float:
    """Distance in the Poincaré ball (all points must have norm < 1)."""
    du = 1 - sum(x * x for x in u)
    dv = 1 - sum(x * x for x in v)
    if du <= 0 or dv <= 0:
        raise ValueError("points must lie inside the unit ball")
    diff = sum((x - y) ** 2 for x, y in zip(u, v, strict=True))
    return math.acosh(1 + 2 * diff / (du * dv))


def identity_rescore(query, hits: list[tuple[float, Face]]) -> list[tuple[float, Face]]:
    return hits


def poincare_rescore(query, hits: list[tuple[float, Face]]) -> list[tuple[float, Face]]:
    """Hybrid: re-rank ANN candidates by hyperbolic closeness (vectors scaled into the ball)."""
    def ball(v):
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [0.9 * x / n for x in v]

    q = ball(query)
    scored = [(-poincare_distance(q, ball(f.embedding)), f) for _, f in hits]
    return sorted(scored, key=lambda s: (-s[0], s[1].digest))


@dataclass
class Proposal:
    a: str  # face digest
    b: str
    reason: str
    proposed_by: str


class SimplicialStore:
    def __init__(self, rescore: Callable = identity_rescore, max_faces: int = MAX_FACES) -> None:
        self.faces: dict[str, Face] = {}
        self.similarity: set[tuple[str, str]] = set()  # reviewed, live portals between faces
        self.review_queue: list[Proposal] = []
        self.rescore = rescore
        self.max_faces = max_faces

    def add(self, face: Face) -> str:
        if face.digest not in self.faces and len(self.faces) >= self.max_faces:
            raise OverflowError("face cap reached: coarsen (collapse) before adding")
        self.faces[face.digest] = face
        return face.digest

    def retrieve(self, query, k: int = 3) -> list[Face]:
        hits = sorted(((cosine(query, f.embedding), f) for f in self.faces.values()),
                      key=lambda s: (-s[0], s[1].digest))[: max(k * 3, k)]
        return [f for _, f in self.rescore(query, hits)[:k]]

    def faces_touching(self, entity: str) -> list[Face]:
        return sorted((f for f in self.faces.values() if entity in f.entities), key=lambda f: f.digest)

    def propose_similarity_edge(self, a: str, b: str, reason: str, curator: str) -> Proposal:
        """Curator path (off the request path). Queues only; never live."""
        if a not in self.faces or b not in self.faces or a == b:
            raise KeyError("both faces must exist and differ")
        p = Proposal(*sorted((a, b)), reason=reason[:400], proposed_by=curator)
        self.review_queue.append(p)
        return p

    def accept(self, proposal: Proposal, reviewer: str) -> None:
        if not reviewer or reviewer == proposal.proposed_by:
            raise PermissionError("a different, named human reviewer must accept")
        self.review_queue.remove(proposal)
        self.similarity.add((proposal.a, proposal.b))

    def serialize(self) -> str:
        """Hash-ordered, prompt-safe canonical bytes."""
        body = [self.faces[d].prompt_view() for d in sorted(self.faces)]
        return json.dumps({"faces": body, "links": sorted(self.similarity)},
                          sort_keys=True, separators=(",", ":"))

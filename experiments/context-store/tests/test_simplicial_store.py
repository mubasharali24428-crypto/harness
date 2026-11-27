import random

import pytest

from simplicial import (
    SimplicialStore,
    identity_rescore,
    make_face,
    poincare_distance,
    poincare_rescore,
)

PROV = {"reviewer": "boss", "timestamp": "2026-11-24T12:00:00Z", "revision": "5d72398e"}


def faces():
    return [
        make_face("task:otp", "file:auth.py", "decision:lockout", "lock after 5 tries", PROV, [1, 0, 0]),
        make_face("task:otp", "file:otp.py", "person:sec", "sec owns OTP review", PROV, [0.9, 0.1, 0]),
        make_face("task:ui", "file:form.tsx", "decision:a11y", "labels on every input", PROV, [0, 1, 0]),
    ]


def test_retrieval_returns_whole_face_with_provenance():
    s = SimplicialStore()
    for f in faces():
        s.add(f)
    top = s.retrieve([1, 0, 0], k=1)[0]
    assert top.entities == ("decision:lockout", "file:auth.py", "task:otp")
    assert top.provenance["reviewer"] == "boss" and len(top.edges()) == 3


@pytest.mark.parametrize("seed", range(20))
def test_serialization_is_hash_ordered_not_insertion_ordered(seed):
    base = SimplicialStore()
    for f in faces():
        base.add(f)
    shuffled = SimplicialStore()
    fs = faces()
    random.Random(seed).shuffle(fs)
    for f in fs:
        shuffled.add(f)
    assert shuffled.serialize() == base.serialize()


def test_no_vectors_or_coordinates_reach_the_prompt_view():
    s = SimplicialStore()
    s.add(faces()[0])
    out = s.serialize()
    assert "embedding" not in out and "1.0" not in out and "coord" not in out


def test_curator_proposals_are_not_live_until_a_different_human_accepts():
    s = SimplicialStore()
    a, b = (s.add(f) for f in faces()[:2])
    p = s.propose_similarity_edge(a, b, "both about OTP", curator="curator-agent")
    assert s.similarity == set() and s.review_queue == [p]
    with pytest.raises(PermissionError):
        s.accept(p, reviewer="curator-agent")
    s.accept(p, reviewer="boss")
    assert s.similarity == {tuple(sorted((a, b)))} and s.review_queue == []


def test_cap_forces_coarsening():
    s = SimplicialStore(max_faces=2)
    s.add(faces()[0])
    s.add(faces()[1])
    with pytest.raises(OverflowError):
        s.add(faces()[2])


def test_bad_faces_rejected():
    with pytest.raises(ValueError):
        make_face("a", "a", "b", "x", PROV, [1])
    with pytest.raises(ValueError):
        make_face("a", "b", "c", "x", {"reviewer": "boss"}, [1])


def test_hyperbolic_rescore_is_pluggable_and_sane():
    assert poincare_distance([0.1, 0.2], [0.1, 0.2]) == 0
    assert poincare_distance([0, 0], [0.5, 0]) < poincare_distance([0, 0], [0.9, 0])
    hyper = SimplicialStore(rescore=poincare_rescore)
    flat = SimplicialStore(rescore=identity_rescore)
    for f in faces():
        hyper.add(f)
        flat.add(f)
    assert hyper.retrieve([1, 0, 0], k=1)[0].fact == flat.retrieve([1, 0, 0], k=1)[0].fact

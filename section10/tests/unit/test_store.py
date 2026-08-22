"""StateStore: defaults, roundtrip, corrupt-file recovery, atomic update."""

import json

from harness_onboarding.store import DEFAULT_STATE, StateStore


def test_missing_file_returns_defaults(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    assert state == DEFAULT_STATE


def test_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.update(lambda s: s.__setitem__("securityChecklistAcknowledged", True))
    assert StateStore(tmp_path / "state.json").load()["securityChecklistAcknowledged"] is True


def test_corrupt_file_returns_defaults(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    assert StateStore(p).load() == DEFAULT_STATE


def test_partial_state_merges_over_defaults(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"telemetry": {"fullPromptText": True}}), encoding="utf-8")
    state = StateStore(p).load()
    assert state["telemetry"]["fullPromptText"] is True
    # untouched fields keep defaults
    assert state["telemetry"]["baselineEnabled"] is True
    assert state["securityChecklistAcknowledged"] is False


def test_update_mutator_sees_and_mutates_state(tmp_path):
    store = StateStore(tmp_path / "state.json")

    def mutate(state):
        state["telemetryEvents"].append({"kind": "error_log", "payload": {}})

    store.update(mutate)
    assert store.load()["telemetryEvents"] == [{"kind": "error_log", "payload": {}}]


def test_no_temp_files_left_behind(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.update(lambda s: None)
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".state-")]
    assert leftovers == []

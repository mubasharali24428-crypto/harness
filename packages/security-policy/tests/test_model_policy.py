import pytest

from harness_security_policy.decisions import Outcome
from harness_security_policy.model_policy import (
    TeamModelPolicy,
    TenantCache,
    cache_key,
    compare_models,
    evaluate_model,
    parse_local_models,
    plan_fallback,
    resolve_model,
    score_response,
    validate_local_endpoint,
)

CAT = {"a/x", "a/y", "b/z", "b/w"}


def pol(**doc):
    return TeamModelPolicy.from_json(doc, approved=doc.pop("approved", ()) or ())


def test_allowlist_denies_unapproved_and_unknown_never_substitutes():
    p = TeamModelPolicy.from_json({"allowlistEnforced": True}, approved={"a/x"})
    assert evaluate_model(p, "a/x", CAT).outcome is Outcome.ALLOW
    assert evaluate_model(p, "a/y", CAT).reason == "model.not_approved"
    assert evaluate_model(p, "nope", CAT).reason == "model.unknown"
    open_p = TeamModelPolicy.from_json({}, approved=())
    assert evaluate_model(open_p, "a/y", CAT).outcome is Outcome.ALLOW


def test_requested_model_always_wins_over_rules():
    p = TeamModelPolicy.from_json({"routingRules": [{"taskType": "review", "model": "b/z"}]})
    assert resolve_model(p, requested="a/y", task_type="review", role_default="a/x") == {
        "model": "a/y",
        "source": "requested",
    }
    assert (
        resolve_model(p, requested=None, task_type="review", role_default="a/x")["model"] == "b/z"
    )
    assert (
        resolve_model(p, requested=None, task_type="other", role_default="a/x")["source"]
        == "role_default"
    )


def test_fallback_is_opt_in_attributed_and_respects_allowlist():
    doc = {
        "fallbackEnabled": True,
        "fallbackEquivalents": {"a/x": ["a/y", "b/z"]},
        "allowlistEnforced": True,
    }
    p = TeamModelPolicy.from_json(doc, approved={"a/x", "b/z"})
    plan = plan_fallback(p, requested="a/x", failure="unavailable", catalog=CAT)
    assert plan == {
        "requestedModel": "a/x",
        "servedModel": "b/z",
        "reason": "unavailable",
    }  # a/y unapproved
    assert plan_fallback(p, requested="a/x", failure="invalid_key", catalog=CAT) is None
    off = TeamModelPolicy.from_json({**doc, "fallbackEnabled": False}, approved={"a/x", "b/z"})
    assert plan_fallback(off, requested="a/x", failure="unavailable", catalog=CAT) is None
    assert plan_fallback(p, requested="b/w", failure="unavailable", catalog=CAT) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"routingRules": [{"taskType": "t", "model": "a"}, {"taskType": "t", "model": "b"}]},
        {"routingRules": "x"},
        {"cacheTtlSeconds": 0},
        {"cacheTtlSeconds": True},
        {"fallbackEquivalents": {"a": "b"}},
        {"routingRules": [{"taskType": "", "model": "a"}]},
    ],
)
def test_policy_validation_rejects_malformed(bad):
    with pytest.raises(ValueError):
        TeamModelPolicy.from_json(bad)


def test_cache_tenant_isolation_ttl_invalidation_bound():
    now = [0.0]
    c = TenantCache(max_entries=2, clock=lambda: now[0])
    k = cache_key("a/x", [{"role": "user", "content": "hi"}], {"temperature": 0})
    c.put("t1", k, "ans", ttl=10, model="a/x")
    assert c.get("t1", k) == "ans" and c.get("t2", k) is None and c.get("", k) is None
    now[0] = 11
    assert c.get("t1", k) is None  # expired
    c.put("t1", "k1", 1, ttl=10, model="a/x")
    c.put("t1", "k2", 2, ttl=10, model="b/z")
    c.put("t2", "k3", 3, ttl=10)
    assert c.size() == 2 and c.get("t1", "k1") is None  # LRU bound
    assert c.invalidate_team("t1", model="b/z") == 1 and c.get("t2", "k3") == 3
    assert c.put("t1", "k", 1, ttl=0) is False
    assert cache_key("a/x", [], {"t": 0}) != cache_key("a/y", [], {"t": 0})


def test_comparison_ranks_only_scored_rows():
    rows = [
        {"model": "a", "costUsd": 0.02, "latencyMs": 10, "score": 1.0, "error": None},
        {"model": "b", "costUsd": 0.01, "latencyMs": 20, "score": 1.0, "error": None},
        {"model": "c", "costUsd": 0.0, "latencyMs": 5, "score": None, "error": None},
        {"model": "d", "costUsd": 0.0, "latencyMs": 5, "score": 0.5, "error": "boom"},
    ]
    r = {x["model"]: x for x in compare_models(rows)}
    assert (
        r["b"]["rank"] == 1
        and r["a"]["rank"] == 2
        and r["c"]["rank"] is None
        and r["d"]["rank"] is None
    )
    assert r["c"]["qualityPerDollar"] is None
    assert score_response(
        "Hello World", expected_contains=["hello", "x"], pattern=r"W\w+"
    ) == pytest.approx(2 / 3)
    assert score_response("x") is None


def test_local_endpoint_ssrf_rules():
    res = lambda ips: lambda h: ips  # noqa: E731
    assert (
        validate_local_endpoint("http://127.0.0.1:11434/v1", resolver=res(["127.0.0.1"])).outcome
        is Outcome.ALLOW
    )
    assert (
        validate_local_endpoint("http://169.254.169.254/", resolver=res(["169.254.169.254"])).reason
        == "local.link_local_denied"
    )
    assert (
        validate_local_endpoint("http://gpu.lan/v1", resolver=res(["10.0.0.5"])).reason
        == "local.private_denied"
    )
    assert (
        validate_local_endpoint(
            "http://gpu.lan/v1", resolver=res(["10.0.0.5"]), operator_hosts=["gpu.lan"]
        ).outcome
        is Outcome.ALLOW
    )
    assert (
        validate_local_endpoint("http://evil.com/", resolver=res(["8.8.8.8"])).reason
        == "local.public_denied"
    )
    assert (
        validate_local_endpoint("http://evil.com/", resolver=res(["8.8.8.8", "127.0.0.1"])).outcome
        is Outcome.DENY
    )
    assert (
        validate_local_endpoint("http://u:p@127.0.0.1/", resolver=res(["127.0.0.1"])).reason
        == "local.bad_url"
    )
    assert (
        validate_local_endpoint("file:///etc/passwd", resolver=res(["127.0.0.1"])).reason
        == "local.bad_url"
    )


def test_parse_local_models_both_dialects_bounded():
    assert parse_local_models({"data": [{"id": "m1"}, {"id": "m1"}, {"x": 1}]}) == ["m1"]
    assert parse_local_models({"models": [{"name": "qwen:7b"}]}) == ["qwen:7b"]
    assert (
        parse_local_models("junk") == [] and parse_local_models({"data": [{"id": "x" * 500}]}) == []
    )
    assert len(parse_local_models({"data": [{"id": f"m{i}"} for i in range(500)]})) == 200


def test_residency_enforced_fail_closed_and_fallback_respects_it():
    doc = {
        "residency": {
            "enforced": True,
            "allowedRegions": ["eu"],
            "providerRegions": {"a": ["eu"], "b": ["us"], "c": ["eu", "us"]},
        }
    }
    p = TeamModelPolicy.from_json(doc)
    assert evaluate_model(p, "a/x", CAT).outcome is Outcome.ALLOW
    assert evaluate_model(p, "b/z", CAT).reason == "model.residency_denied"
    assert (
        evaluate_model(
            TeamModelPolicy.from_json(
                {
                    "residency": {
                        "enforced": True,
                        "allowedRegions": ["eu"],
                        "providerRegions": {"c": ["eu", "us"]},
                    }
                }
            ),
            "c/q",
            {"c/q"},
        ).reason
        == "model.residency_denied"
    )  # any out-of-region processing denies
    unknown = TeamModelPolicy.from_json({"residency": {"enforced": True, "allowedRegions": ["eu"]}})
    assert evaluate_model(unknown, "a/x", CAT).reason == "model.residency_unknown"
    off = TeamModelPolicy.from_json({"residency": {"enforced": False, "allowedRegions": ["eu"]}})
    assert evaluate_model(off, "b/z", CAT).outcome is Outcome.ALLOW
    fb = TeamModelPolicy.from_json(
        {**doc, "fallbackEnabled": True, "fallbackEquivalents": {"a/x": ["b/z", "a/y"]}}
    )
    assert (
        plan_fallback(fb, requested="a/x", failure="unavailable", catalog=CAT)["servedModel"]
        == "a/y"
    )
    assert TeamModelPolicy.from_json(p.to_json()).to_json() == p.to_json()  # round trip


@pytest.mark.parametrize(
    "bad",
    [
        {"residency": {"enforced": True}},
        {"residency": {"allowedRegions": ["EU!"]}},
        {"residency": "x"},
        {"residency": {"providerRegions": {"a": "eu"}}},
    ],
)
def test_residency_validation(bad):
    with pytest.raises(ValueError):
        TeamModelPolicy.from_json(bad)

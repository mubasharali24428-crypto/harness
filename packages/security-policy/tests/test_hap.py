import pytest

from harness_security_policy.capabilities import CapabilitySigner
from harness_security_policy.hap import HapRequest, evaluate_hap_policy_request


def _req(op, args, **kw):
    return HapRequest.from_wire(
        {
            "schemaVersion": "hap/1",
            "requestId": "r1",
            "projectId": "p1",
            "runId": "run1",
            "operation": op,
            "arguments": args,
            **kw,
        }
    )


def test_truncated_pem_is_denied():
    resp, dec = evaluate_hap_policy_request(
        _req("policy.scan_outbound", {"text": "-----BEGIN RSA PRIVATE KEY-----\nMIIabc"}),
        team_id="t1",
    )
    assert resp.status == "denied" and dec.text is None


def test_scan_clean_succeeds_and_wire_shape():
    resp, _ = evaluate_hap_policy_request(
        _req("policy.scan_outbound", {"text": "hello"}), team_id="t1"
    )
    w = resp.to_wire()
    assert w["schemaVersion"] == "hap/1" and w["status"] == "succeeded" and "error" not in w


@pytest.mark.parametrize("bad", [{}, {"schemaVersion": "hap/2"}, {"requestId": " "}])
def test_malformed_request_rejected(bad):
    base = {
        "schemaVersion": "hap/1",
        "requestId": "r",
        "projectId": "p",
        "runId": "x",
        "operation": "o",
        "arguments": {},
    }
    base.update(bad)
    if not bad:
        base = {}
    with pytest.raises(ValueError):
        HapRequest.from_wire(base)


def test_unknown_operation_unsupported():
    resp, _ = evaluate_hap_policy_request(_req("policy.nope", {}), team_id="t1")
    assert resp.status == "unsupported"


def test_egress_blocks_metadata_ip():
    resp, _ = evaluate_hap_policy_request(
        _req(
            "policy.egress",
            {
                "url": "http://169.254.169.254/",
                "allowHttp": True,
                "allowIpLiterals": True,
                "allowedHosts": ["169.254.169.254"],
            },
        ),
        team_id="t1",
    )
    assert resp.status == "denied"


def test_capability_verify_binds_team_and_run_and_is_dry():
    signer = CapabilitySigner(b"k" * 32)
    cap = signer.issue(
        team_id="t1", run_id="run1", operation="tool.x", audience="s5", project_id="p1", max_uses=1
    )
    args = {"token": cap.token, "requiredOperation": "tool.x", "audience": "s5"}
    for _ in range(2):  # dry: never consumes the use budget
        resp, _ = evaluate_hap_policy_request(
            _req("policy.capability_verify", args), team_id="t1", signer=signer
        )
        assert resp.status == "succeeded"
    resp, dec = evaluate_hap_policy_request(
        _req("policy.capability_verify", args), team_id="other", signer=signer
    )
    assert resp.status == "denied" and dec.reason == "capability.wrong_team"
    resp, _ = evaluate_hap_policy_request(
        _req("policy.capability_verify", args), team_id="t1", signer=None
    )
    assert resp.status == "failed"

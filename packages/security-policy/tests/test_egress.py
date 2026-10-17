from harness_security_policy import (
    EgressPolicy,
    Outcome,
    classify_ip,
    evaluate_redirect_chain,
    evaluate_url,
    host_matches,
)


def _policy(**overrides):
    base = {
        "allowed_hosts": ("api.example.com", "*.trusted.example", "127.0.0.1"),
        "require_dns_check": False,
    }
    base.update(overrides)
    return EgressPolicy(**base)  # type: ignore[arg-type]


def test_empty_allowlist_fails_closed():
    decision = evaluate_url(
        "https://anything.example", policy=EgressPolicy(require_dns_check=False)
    )
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "egress.host_not_allowed"


def test_allowlisted_https_host_allowed():
    decision = evaluate_url("https://api.example.com/v1/call", policy=_policy())
    assert decision.outcome is Outcome.ALLOW


def test_wildcard_matches_subdomain_not_lookalike():
    assert host_matches("a.trusted.example", "*.trusted.example")
    assert not host_matches("trusted.example", "*.trusted.example")
    assert not host_matches("evil-trusted.example", "*.trusted.example")
    assert not host_matches("trusted.example.evil.com", "*.trusted.example")


def test_http_denied_unless_opted_in():
    denied = evaluate_url("http://api.example.com/", policy=_policy())
    assert denied.reason == "egress.scheme_denied"
    allowed = evaluate_url(
        "http://api.example.com/",
        policy=_policy(allow_http=True, allowed_ports=(80,)),
    )
    assert allowed.outcome is Outcome.ALLOW


def test_non_http_schemes_denied():
    for url in ("file:///etc/passwd", "ftp://api.example.com/x", "gopher://x"):
        assert evaluate_url(url, policy=_policy()).reason == "egress.scheme_denied"


def test_userinfo_denied_even_when_host_allowlisted():
    decision = evaluate_url("https://user:pass@api.example.com/", policy=_policy())
    assert decision.reason == "egress.userinfo_denied"


def test_port_must_be_allowlisted():
    decision = evaluate_url("https://api.example.com:8443/x", policy=_policy())
    assert decision.reason == "egress.port_denied"
    ok = evaluate_url("https://api.example.com:8443/x", policy=_policy(allowed_ports=(8443,)))
    assert ok.outcome is Outcome.ALLOW


def test_dns_resolution_to_private_ip_denied():
    decision = evaluate_url(
        "https://api.example.com/",
        policy=_policy(require_dns_check=True),
        resolver=lambda host: ["10.0.0.5"],
    )
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "egress.resolved_ip_class_denied"
    assert decision.details["classification"] == "private"


def test_metadata_endpoint_denied_by_default():
    decision = evaluate_url(
        "https://api.example.com/",
        policy=_policy(require_dns_check=True),
        resolver=lambda host: ["169.254.169.254"],
    )
    assert decision.outcome is Outcome.DENY
    assert decision.details["classification"] == "link_local"


def test_private_opt_in_allows_rfc1918_but_not_link_local():
    policy = _policy(require_dns_check=True, allow_private=True)
    private_ok = evaluate_url(
        "https://api.example.com/", policy=policy, resolver=lambda host: ["192.168.1.10"]
    )
    assert private_ok.outcome is Outcome.ALLOW
    link_local = evaluate_url(
        "https://api.example.com/", policy=policy, resolver=lambda host: ["169.254.169.254"]
    )
    assert link_local.outcome is Outcome.DENY


def test_dns_failure_fails_closed():
    def exploding(_host):
        raise OSError("nxdomain")

    decision = evaluate_url(
        "https://api.example.com/", policy=_policy(require_dns_check=True), resolver=exploding
    )
    assert decision.reason == "egress.dns_failure"
    empty = evaluate_url(
        "https://api.example.com/", policy=_policy(require_dns_check=True), resolver=lambda h: []
    )
    assert empty.reason == "egress.dns_failure"


def test_missing_resolver_denied_when_dns_check_required():
    decision = evaluate_url("https://api.example.com/", policy=_policy(require_dns_check=True))
    assert decision.reason == "egress.dns_check_unavailable"


def test_ip_literals_denied_unless_opted_in():
    denied = evaluate_url("https://127.0.0.1/", policy=_policy(allow_ip_literals=False))
    assert denied.reason == "egress.ip_literal_denied"
    allowed = evaluate_url(
        "https://127.0.0.1/",
        policy=_policy(allow_ip_literals=True, allow_private=True),
    )
    assert allowed.outcome is Outcome.ALLOW


def test_denied_hosts_win_over_allowlist():
    policy = _policy(denied_hosts=("api.example.com",))
    assert evaluate_url("https://api.example.com/", policy=policy).reason == "egress.host_denied"


def test_redirect_chain_requires_every_hop():
    policy = _policy()
    ok = evaluate_redirect_chain(
        ["https://api.example.com/a", "https://x.trusted.example/b"], policy=policy
    )
    assert ok.outcome is Outcome.ALLOW
    denied = evaluate_redirect_chain(
        ["https://api.example.com/a", "https://evil.example/b"], policy=policy
    )
    assert denied.outcome is Outcome.DENY
    assert denied.details["hopIndex"] == 1


def test_classify_ip_covers_mapped_and_special_ranges():
    assert classify_ip("127.0.0.1") == "loopback"
    assert classify_ip("::1") == "loopback"
    assert classify_ip("10.1.2.3") == "private"
    assert classify_ip("fd00::1") == "private"
    assert classify_ip("169.254.169.254") == "link_local"
    assert classify_ip("0.0.0.0") == "unspecified"
    assert classify_ip("::ffff:127.0.0.1") == "loopback"
    assert classify_ip("93.184.216.34") == "public"

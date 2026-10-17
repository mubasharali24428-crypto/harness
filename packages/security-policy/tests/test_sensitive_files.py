from harness_security_policy import (
    Outcome,
    SensitiveFilePolicy,
    evaluate_path,
    evaluate_transmission,
)


def test_env_file_denied():
    decision = evaluate_path(".env")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "file.sensitive_denied"


def test_env_variant_denied():
    assert evaluate_path("config/.env.production").outcome is Outcome.DENY


def test_pem_and_ssh_keys_denied():
    assert evaluate_path("certs/server.pem").outcome is Outcome.DENY
    assert evaluate_path("home/.ssh/id_rsa").outcome is Outcome.DENY
    assert evaluate_path(".aws/credentials").outcome is Outcome.DENY


def test_ordinary_source_file_allowed():
    decision = evaluate_path("src/harness/app.py")
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason == "file.allowed"


def test_traversal_denied():
    decision = evaluate_path("docs/../../etc/passwd")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "file.traversal_denied"


def test_empty_path_denied():
    assert evaluate_path("   ").outcome is Outcome.DENY


def test_exact_allowlist_overrides_deny():
    policy = SensitiveFilePolicy(allow_paths=frozenset({".env.example"}))
    assert evaluate_path(".env.example", policy=policy).outcome is Outcome.ALLOW


def test_write_to_sensitive_path_also_denied():
    assert evaluate_path("prod.pem", operation="write").outcome is Outcome.DENY


def test_transmission_combines_path_and_content_gates():
    denied_path = evaluate_transmission(".env", "SAFE_CONTENT=1")
    assert denied_path.decision.outcome is Outcome.DENY

    secret_content = evaluate_transmission("src/config.py", "password = 'hunter2hunter2hunter2'")
    assert secret_content.decision.outcome is Outcome.REDACT
    assert "hunter2" not in secret_content.decision.text

    clean = evaluate_transmission("src/app.py", "print('hello')")
    assert clean.decision.outcome is Outcome.ALLOW

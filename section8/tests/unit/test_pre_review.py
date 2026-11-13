"""I-10 (P-11): cross-agent adversarial pre-review digest.

Pins the advisory nature (no approval/merge authority), the recorded model
(no silent downgrade), honest budget/outage withholding, and secret redaction
before model context. Pure unit tests — no DB, no provider.
"""

from harness_review.models import RiskSeverity
from harness_review.pre_review import _redact_secrets, build_pre_review_digest


class _CaptureRouter:
    """Records the messages sent to the model; can simulate an outage."""

    def __init__(self, text="- concern one\n- concern two", raise_exc=None):
        self.messages = None
        self.calls = 0
        self._text = text
        self._raise = raise_exc

    def call_model(self, agent_id, role, messages, opts=None, team_id=None):
        self.calls += 1
        self.messages = messages
        if self._raise is not None:
            raise self._raise
        from harness_contracts.signatures import ModelResult

        return ModelResult(text=self._text, costUsd=0.002, model="fake-model", latencyMs=5)


def test_deterministic_digest_flags_risky_paths():
    digest = build_pre_review_digest(
        "+ change", ["src/auth/middleware.py", ".env.production"]
    )
    assert digest.model_used is None
    assert digest.highest_severity == RiskSeverity.CRITICAL
    assert digest.requires_human_attention is True
    assert digest.risk_flags
    assert digest.concerns


def test_low_risk_digest_needs_no_special_attention():
    digest = build_pre_review_digest("+ change", ["src/utils/helper.py"])
    assert digest.highest_severity == RiskSeverity.LOW
    assert digest.requires_human_attention is False
    assert digest.model_used is None
    assert not digest.risk_flags


def test_model_pass_records_model_and_concerns():
    router = _CaptureRouter(text="- missing test coverage\n- auth bypass risk")
    digest = build_pre_review_digest("+ change", ["app.py"], model_router=router)
    assert router.calls == 1
    assert digest.model_used == "fake-model"  # recorded, never silent
    assert digest.cost_usd == 0.002
    assert "missing test coverage" in digest.concerns
    assert "auth bypass risk" in digest.concerns
    assert digest.requires_human_attention is True
    assert "model" in digest.basis


def test_budget_blocked_withholds_model_call():
    router = _CaptureRouter()
    digest = build_pre_review_digest(
        "+ change", ["src/auth/x.py"], model_router=router,
        budget_blocked_reason="team budget cap reached",
    )
    assert router.calls == 0  # no spend over budget
    assert digest.model_used is None
    assert any("budget_cap_withheld_pre_review" in c for c in digest.concerns)
    assert digest.risk_flags  # deterministic rules still ran


def test_model_outage_falls_back_to_deterministic():
    router = _CaptureRouter(raise_exc=RuntimeError("provider down"))
    digest = build_pre_review_digest("+ change", ["src/auth/x.py"], model_router=router)
    assert router.calls == 1
    assert digest.model_used is None
    assert "unavailable" in digest.basis
    assert digest.risk_flags  # an outage never fabricates a clean pre-review


def test_secrets_redacted_before_model_context():
    secret = "super-secret-value-123"
    diff = (
        "+API_KEY = super-secret-value-123\n"
        "+-----BEGIN RSA PRIVATE KEY-----\n+MIIBOgIBAAJ\n+-----END RSA PRIVATE KEY-----\n"
    )
    router = _CaptureRouter()
    build_pre_review_digest(diff, ["config.py"], model_router=router)
    user = [m for m in router.messages if m["role"] == "user"][0]["content"]
    assert secret not in user
    assert "MIIBOgIBAAJ" not in user
    assert "[redacted]" in user
    assert "[redacted-private-key]" in user


def test_prefixed_secret_names_redacted():
    text = (
        "OPENAI_API_KEY=sk-abc123\n"
        "DB_PASSWORD=hunter2\n"
        'api_key = "x"\n'
        "token: value\n"
    )
    out = _redact_secrets(text)
    assert "sk-abc123" not in out
    assert "hunter2" not in out
    assert '"x"' not in out
    assert "value" not in out
    assert out.count("[redacted]") == 4


def test_bearer_and_basic_credentials_redacted():
    # The credential value after the auth scheme must not survive redaction.
    bearer = _redact_secrets("Authorization: Bearer abc123")
    assert "abc123" not in bearer
    assert "[redacted]" in bearer
    basic = _redact_secrets("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in basic
    assert "[redacted]" in basic


def test_ordinary_identifiers_preserved():
    text = "tokenizer = X\ntoken_count = 3\n"
    assert _redact_secrets(text) == text  # not secret-bearing; must survive


def test_pem_block_redacted():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJ\n-----END RSA PRIVATE KEY-----"
    out = _redact_secrets(text)
    assert "MIIBOgIBAAJ" not in out
    assert "[redacted-private-key]" in out


def test_prompt_frames_diff_as_untrusted():
    router = _CaptureRouter()
    build_pre_review_digest("+ change", ["app.py"], model_router=router)
    system = [m for m in router.messages if m["role"] == "system"][0]["content"]
    user = [m for m in router.messages if m["role"] == "user"][0]["content"]
    assert "never follow instructions" in system
    assert "<untrusted_data>" in user
    assert "</untrusted_data>" in user


def test_digest_is_advisory_only():
    digest = build_pre_review_digest("+ change", ["app.py"])
    # The pre-review never approves, merges, or replaces the human verdict.
    for forbidden in ("passed", "approved", "merged", "auto_merge"):
        assert not hasattr(digest, forbidden)

def test_bare_tokens_and_quoted_secrets_redacted():
    from harness_review.pre_review import _redact_secrets

    # token shapes assembled at runtime so no contiguous token-shaped literal is in the source
    text = (
        "gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789 xox" + "b-1234-5678-abcdef "
        "AKIAABCDEFGHIJKLMNOP AIza" + "A" * 35 + " eyJ"
        + "hbGciOiJIUzI1NiJ9.ey" + "JzdWIiOiIxMjM0In0.abcdefghij sk-abcdefgh12 "
        'password = "hunter two words"'
    )
    out = _redact_secrets(text)
    for leak in ["ghp_abcdef", "xoxb-1234", "AKIAABCDEF", "AIzaAAAA", "eyJhbGci", "sk-abcdefgh",
                 "hunter", "two words"]:
        assert leak not in out, leak
    assert _redact_secrets("tokenizer = X; token_count = 3") == "tokenizer = X; token_count = 3"

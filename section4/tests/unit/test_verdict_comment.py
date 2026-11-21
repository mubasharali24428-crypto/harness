"""P-15 (I-02): verdict comment builder — pure, offline, no network/DB."""
import harness_orchestrator.verdict_comment as vc
from harness_orchestrator.verdict_comment import (
    FOOTER,
    MAX_COMMENT_CHARS,
    build_verdict_comment,
    verdict_marker,
)

# Assembled at runtime so no contiguous token-shaped literal sits in the source (secret scanners).
GHP = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0In0" + "." + "abcdefghij"


def _facts(**over):
    base = {
        "run_id": "run-1",
        "goal": "refactor the parser",
        "plan": {"approved_by": "u1", "approved_by_display": "Dev",
                 "approved_at": "2026-11-13T00:00:00Z"},
        "sandbox": {"backend": "docker", "mode": "landlock"},
        "tests": {"known": True, "runs": [{"command": "pytest tests/", "result": "passed"}]},
        "cost": {"actual_usd": 0.0123, "estimated_usd": 0.05},
        "risk_flags": [{"path": "src/auth.py", "severity": "HIGH", "reason": "auth path"}],
    }
    base.update(over)
    return base


def test_marker_footer_and_cap():
    out = build_verdict_comment(_facts(), pr_number=7)
    assert verdict_marker("run-1") in out
    assert "<!-- harness-verdict:v1 run=run-1 -->" in out
    assert FOOTER in out
    assert "PR #7" in out
    assert len(out) < 65536
    assert len(out) <= MAX_COMMENT_CHARS


def test_recorded_facts_rendered():
    out = build_verdict_comment(_facts(), pr_number=7)
    assert "Dev" in out and "2026-11-13" in out
    assert "docker" in out and "landlock" in out
    assert "pytest tests/" in out and "passed" in out
    assert "0.012300" in out
    assert "estimated" in out
    assert "src/auth.py" in out and "HIGH" in out


def test_secrets_never_in_output():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJ\n-----END RSA PRIVATE KEY-----"
    facts = _facts(
        goal="use OPENAI_API_KEY=sk-abc123secret and DB_PASSWORD=hunter2",
        tests={"known": True, "runs": [
            {"command": "curl -H 'Authorization: Bearer abc123token' https://x",
             "result": "passed"},
            {"command": "echo sk-zzz99999999", "result": "failed"},
        ]},
        risk_flags=[{"path": "config.py", "severity": "HIGH", "reason": "DB_PASSWORD=hunter2"}],
        adversarial_digest={"concerns": [f"leak {pem}", "token=abc"], "model_used": "m"},
    )
    out = build_verdict_comment(facts, pr_number=7, secrets=["hunter2"])
    for leak in ["sk-abc123secret", "hunter2", "abc123token", "sk-zzz99999999",
                 "MIIBOgIBAAJ", "token=abc"]:
        assert leak not in out, leak
    assert "[redacted]" in out


def test_unknown_tests_never_passed():
    out = build_verdict_comment(_facts(tests={"known": False, "runs": []}), pr_number=7)
    assert "unknown" in out.lower()
    assert "passed" not in out.lower()


def test_no_tests_run_records_code():
    out = build_verdict_comment(
        _facts(tests={"known": True, "runs": []}, uncertainty=[{"code": "NO_TESTS_RUN"}]),
        pr_number=7,
    )
    assert "NO_TESTS_RUN" in out
    assert "not run" in out.lower()


def test_unknown_cost_never_zero():
    out = build_verdict_comment(
        _facts(cost={"actual_usd": None, "estimated_usd": None}), pr_number=7)
    assert "actual: unknown" in out
    assert "$0" not in out


def test_unrecorded_sandbox_not_recorded():
    out = build_verdict_comment(_facts(sandbox=None), pr_number=7)
    assert "Sandbox boundary" in out
    assert "not recorded" in out


def test_plan_not_recorded():
    out = build_verdict_comment(_facts(plan=None), pr_number=7)
    assert "Plan approved by" in out
    assert "not recorded" in out


def test_hard_cap_truncates_with_note_and_keeps_footer(monkeypatch):
    # Per-field clamps keep normal output far below the cap; exercise the backstop
    # by lowering the cap so a normal comment exceeds it.
    monkeypatch.setattr(vc, "MAX_COMMENT_CHARS", 300)
    out = vc.build_verdict_comment(_facts(), pr_number=7)
    assert len(out) <= 300
    assert "truncated" in out
    assert FOOTER in out
    assert verdict_marker("run-1") in out


def test_no_raw_diff_log_or_contents_even_if_present():
    facts = _facts()
    facts["diff"] = "diff --git a/secret b/secret\n+OPENAI_API_KEY=sk-leak99999"
    facts["log"] = "DEBUG token=supersecretlogvalue"
    facts["file_contents"] = "DB_PASSWORD=hunter2"
    out = build_verdict_comment(facts, pr_number=7)
    assert "diff --git" not in out
    assert "sk-leak99999" not in out
    assert "supersecretlogvalue" not in out
    assert "DEBUG" not in out


def test_adversarial_digest_advisory_only_when_recorded():
    out = build_verdict_comment(
        _facts(adversarial_digest={"concerns": ["maybe bug"], "model_used": "fake"}), pr_number=7
    )
    assert "advisory" in out.lower()
    assert "maybe bug" in out
    out2 = build_verdict_comment(_facts(), pr_number=7)
    assert "adversarial pre-review" not in out2.lower()

def test_bare_tokens_and_quoted_secrets_masked():
    goal = (
        GHP + " xox" + "b-1234-5678-abcdef AKIAABCDEFGHIJKLMNOP AIza" + "A" * 35 + " " + JWT
        + ' password = "hunter two words" secret: \'a b c\''
    )
    out = build_verdict_comment(_facts(goal=goal), pr_number=7)
    for leak in ["ghp_abcdef", "xoxb-1234", "AKIAABCDEF", "AIzaAAAA", "eyJhbGci",
                 "hunter", "two words", "a b c"]:
        assert leak not in out, leak


def test_run_id_cannot_break_the_marker():
    out = build_verdict_comment(_facts(run_id="x --> <img src=//e/p> <!--"), pr_number=7)
    marker_line = out.splitlines()[0]
    assert marker_line.startswith("<!-- harness-verdict:v1 run=") and marker_line.endswith(" -->")
    assert "<img" not in out and "<" not in marker_line.replace("<!--", "", 1)
    assert marker_line.count("-->") == 1
    assert verdict_marker("x --> <img src=//e/p> <!--") == marker_line  # same key both sides


def test_free_text_is_inert():
    facts = _facts(
        goal="ping @everyone @acme/security see #12 ![t](http://evil.example/p.png) [c](http://e.example)",
        risk_flags=[{"path": "a`b`/c", "severity": "high", "reason": "@admin <b>look</b>"}],
    )
    out = build_verdict_comment(facts, pr_number=7)
    assert "@everyone" not in out and "@acme" not in out and "@admin" not in out
    assert "#12" not in out
    assert "![t](" not in out and "[c](" not in out
    assert "<b>" not in out and "<img" not in out
    assert "http://" not in out  # no auto-link
    assert "a'b'/c" in out  # the backtick cannot break out of the code span


def test_bad_cost_is_unknown_not_a_crash():
    for bad in ("n/a", float("nan"), float("inf"), -1, object()):
        out = build_verdict_comment(_facts(cost={"actual_usd": bad, "estimated_usd": bad}),
                                    pr_number=7)
        assert "actual: unknown" in out
        assert "estimated:" not in out

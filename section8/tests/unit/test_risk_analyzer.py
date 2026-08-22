"""Unit tests for the risk analyzer."""


from harness_review.models import RiskFlag, RiskSeverity
from harness_review.risk_analyzer import (
    DEFAULT_RISK_RULES,
    RiskAnalyzer,
    RiskRule,
    get_risk_summary,
)
from tests.fixtures.golden_fixtures import GOLDEN_RISK_FILE_PATHS


def test_auth_directory_flagged():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(["src/auth/middleware.py"])
    auth_flags = [f for f in flags if f.rule == "auth_directory"]
    assert len(auth_flags) == 1
    assert auth_flags[0].severity == RiskSeverity.CRITICAL


def test_payment_directory_flagged():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(["src/payment/stripe_handler.py"])
    pay_flags = [f for f in flags if f.rule == "payment_directory"]
    assert len(pay_flags) == 1
    assert pay_flags[0].severity == RiskSeverity.CRITICAL


def test_migration_files_flagged():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(["migrations/0003_add_indexes.py"])
    mig_flags = [f for f in flags if f.rule == "migration_files"]
    assert len(mig_flags) == 1
    assert mig_flags[0].severity == RiskSeverity.HIGH


def test_env_files_flagged():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze([".env.production"])
    env_flags = [f for f in flags if f.rule == "env_files"]
    assert len(env_flags) == 1
    assert env_flags[0].severity == RiskSeverity.CRITICAL


def test_secret_files_flagged():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(["secrets/api_keys.py"])
    sec_flags = [f for f in flags if f.rule == "secret_files"]
    assert len(sec_flags) == 1
    assert sec_flags[0].severity == RiskSeverity.CRITICAL


def test_routes_medium_risk():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(["src/routes/users.py"])
    route_flags = [f for f in flags if f.rule == "route_files"]
    assert len(route_flags) == 1
    assert route_flags[0].severity == RiskSeverity.MEDIUM


def test_no_flags_for_safe_file():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(["src/utils/helpers.py"])
    assert len(flags) == 0


def test_multiple_matches_single_file():
    """A file can match multiple rules (e.g., auth + token)."""
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(["src/auth/token_handler.py"])
    # Should match auth_directory and token_files
    rules_matched = {f.rule for f in flags}
    assert "auth_directory" in rules_matched
    assert "token_files" in rules_matched


def test_golden_risk_file_paths():
    analyzer = RiskAnalyzer()
    flags = analyzer.analyze(GOLDEN_RISK_FILE_PATHS)
    assert len(flags) >= 5
    severities = [f.severity for f in flags]
    assert RiskSeverity.CRITICAL in severities
    assert RiskSeverity.HIGH in severities


def test_empty_file_list():
    analyzer = RiskAnalyzer()
    assert analyzer.analyze([]) == []


def test_get_risk_summary_empty():
    assert get_risk_summary([]) == RiskSeverity.LOW


def test_get_risk_summary_critical():
    flags = [RiskFlag(path="x", severity=RiskSeverity.CRITICAL, rule="test", reason="test")]
    assert get_risk_summary(flags) == RiskSeverity.CRITICAL


def test_get_risk_summary_picks_highest():
    flags = [
        RiskFlag(path="x", severity=RiskSeverity.LOW, rule="a", reason="b"),
        RiskFlag(path="y", severity=RiskSeverity.HIGH, rule="c", reason="d"),
    ]
    assert get_risk_summary(flags) == RiskSeverity.HIGH


def test_custom_rules():
    custom = [
        RiskRule(name="custom", pattern="*.yaml", severity=RiskSeverity.LOW, reason="cfg"),
    ]
    analyzer = RiskAnalyzer(rules=custom)
    flags = analyzer.analyze(["config/app.yaml"])
    assert len(flags) == 1
    assert flags[0].rule == "custom"


def test_default_rules_count():
    assert len(DEFAULT_RISK_RULES) >= 10

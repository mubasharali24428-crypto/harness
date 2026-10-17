"""F011 hardening proofs: secrets never enter the shared index.

Two controls, both load-bearing for the "share your context index" promise:

1. Secret-named files are excluded at the WALKER level — never read, never
   hashed, never embedded (so a .env can never leak through search results,
   storage rows, or even a chunk-hash side channel).
2. High-confidence secret patterns inside ordinary files are redacted BEFORE
   chunking, so the stored content AND the chunk_hash reflect the redaction,
   and the embedding never sees the raw value.

Precision is deliberately conservative (patterns that are essentially never
legitimate source); over-redaction would poison retrieval, so the tests also
pin that normal code/content is left byte-identical.
"""

import asyncio

from conftest import AUTH_HEADERS, seed_project

from harness_context.chunking import chunk_file, should_index
from harness_context.classify import classify_repo
from harness_context.secret_scan import (
    is_secret_file,
    scrub_secret_paths_seen,
    scrub_secret_text,
)

PROJECT = "p-sec"

_FAKE_AWS = "AKIAIOSFODNN7EXAMPLE"  # AWS docs example id — pattern matches
_FAKE_GH = "ghp_" + "A" * 36
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4"


def test_secret_file_names_are_recognized():
    for name in (
        ".env",
        ".env.production",
        "secrets/.env.local",
        "id_rsa",
        "deploy/server.pem",
        "cert.p12",
        ".netrc",
    ):
        assert is_secret_file(name), name
    for name in ("environment.py", "env.py", "readme.md", "keyboard.js", "main.py"):
        assert not is_secret_file(name), name


def test_should_index_excludes_secret_files():
    assert should_index(".env") is False
    assert should_index("certs/server.key") is False
    assert should_index("pkg/main.py") is True


def test_scrub_replaces_high_confidence_patterns():
    text = f"aws = '{_FAKE_AWS}'\ngh = '{_FAKE_GH}'\n"
    out = scrub_secret_text(text)
    assert _FAKE_AWS not in out
    assert _FAKE_GH not in out
    assert "[REDACTED:aws-access-key]" in out
    assert "[REDACTED:github-token]" in out
    assert out.count("\n") == text.count("\n")  # line geometry preserved


def test_scrub_pem_block_and_jwt():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"
    )
    assert "[REDACTED:private-key]" in scrub_secret_text(pem)
    assert "[REDACTED:jwt]" in scrub_secret_text(f"token = {_FAKE_JWT}")
    assert scrub_secret_paths_seen(pem) == ["private-key"]


def test_scrub_is_idempotent_and_ignores_ordinary_code():
    code = "password: str = 'hunter2'\n# discuss .env handling\n"
    assert scrub_secret_text(code) == code  # no fuzzy over-redaction
    once = scrub_secret_text(f"k = {_FAKE_AWS}")
    assert scrub_secret_text(once) == once


def test_chunk_hashes_are_scrubbed_not_raw(tmp_path):
    """The stored hash must be the SCRUBBED text's hash — otherwise the
    content-addressed index itself becomes a secret's side channel."""
    repo = tmp_path / "secrepo"
    repo.mkdir()
    (repo / "cfg.py").write_text(f"TOKEN = '{_FAKE_AWS}'\n")
    chunks = chunk_file(repo, "cfg.py", window=120, overlap=20)
    assert chunks, "file must chunk"
    for c in chunks:
        assert _FAKE_AWS not in c.content
        assert "REDACTED" in c.content

    # And classification never sees the secret file at all.
    (repo / ".env").write_text("AWS_SECRET_ACCESS_KEY=supersecret\n")
    result = classify_repo(repo, {}, window=120, overlap=20, chunk_file=chunk_file)
    assert ".env" not in result.pending
    assert ".env" not in result.unchanged
    assert "cfg.py" in result.pending


async def test_end_to_end_index_contains_no_secret(client, db, tmp_path):
    await seed_project(db, PROJECT)
    repo = tmp_path / "secrepo"
    repo.mkdir()
    (repo / "deploy.py").write_text(
        f"AWS_KEY = '{_FAKE_AWS}'\ndef ship():\n    return AWS_KEY\n"
    )
    (repo / ".env").write_text("AWS_SECRET_ACCESS_KEY=supersecret\n")

    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": str(repo), "projectId": PROJECT},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    job_id = res.json()["jobId"]
    for _ in range(200):
        st = (
            await client.get(f"/api/v1/context/index/{job_id}", headers=AUTH_HEADERS)
        ).json()
        if st["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.01)
    assert st["status"] == "completed"
    assert st["filesIndexed"] == 1  # deploy.py only; .env never read

    # Nothing served from the index can contain the raw secret.
    res = await client.post(
        "/api/v1/context/search",
        json={"query": "ship deploy aws key", "projectId": PROJECT, "topK": 8},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200
    assert _FAKE_AWS not in res.text
    assert "supersecret" not in res.text

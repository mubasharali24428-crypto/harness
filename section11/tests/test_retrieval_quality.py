"""F016 retrieval-quality harness: golden queries over a real index.

This is a small, deterministic quality gate for the whole ingestion->search
path (chunking, HashingEmbedding-space scoring, ranked output). The repo
below is synthetic so every expected answer is by construction, not by
memory: each query's distinctive vocabulary exists in exactly one file.

hit@3 is the assertion floor (robust to hashing-space noise), plus one top-1
pin on the most distinctive query. If chunking or scoring regresses, these
would fail before any manual smoke test would notice.
"""

import asyncio

from conftest import AUTH_HEADERS, seed_project

PROJECT = "p-golden"

_FILES = {
    "auth/session.py": (
        "def rotate_session_token(token):\n"
        "    \"\"\"Rotate a session token and persist the fresh credential.\"\"\"\n"
        "    return token + ':rotated'\n"
    ),
    "auth/password.py": (
        "def hash_password(plain):\n"
        "    \"\"\"Hash a password with the password hashing routine.\"\"\"\n"
        "    return '$h$' + plain\n"
    ),
    "billing/invoice.py": (
        "def generate_monthly_invoice(customer):\n"
        "    \"\"\"Generate the monthly invoice document for a customer.\"\"\"\n"
        "    return {'customer': customer, 'total': 0}\n"
    ),
    "docs/deploy.md": (
        "# Deployment\n\n"
        "Deploy the production service to kubernetes with the rollout script.\n"
    ),
    "web/theme.ts": (
        "export function palette(): string {\n"
        "  return 'cerulean';\n}\n"
    ),
}

_GOLDEN = [
    # (query, expected path, must_be_top1)
    ("rotate session token credential", "auth/session.py", True),
    ("password hashing routine", "auth/password.py", False),
    ("generate monthly invoice customer", "billing/invoice.py", False),
    ("deploy production kubernetes rollout", "docs/deploy.md", False),
    ("cerulean palette", "web/theme.ts", False),
]


def _golden_repo(tmp_path):
    root = tmp_path / "goldenrepo"
    for rel, text in _FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


async def test_golden_queries_hit_expected_files(client, db, tmp_path):
    await seed_project(db, PROJECT)
    repo = _golden_repo(tmp_path)

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
    assert st["status"] == "completed", st
    assert st["filesIndexed"] == len(_FILES)

    failures = []
    for query, expected_path, top1 in _GOLDEN:
        res = await client.post(
            "/api/v1/context/search",
            json={"query": query, "projectId": PROJECT, "topK": 3},
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200, res.text
        hits = res.json()["results"]
        paths = [h["path"] for h in hits]
        if expected_path not in paths:
            failures.append((query, expected_path, paths))
        elif top1 and paths[0] != expected_path:
            failures.append((f"TOP1:{query}", expected_path, paths))
    assert not failures, f"retrieval quality regressions: {failures}"

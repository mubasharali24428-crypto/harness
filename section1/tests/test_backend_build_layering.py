"""W15 gap: the backend build must not re-download the 60 MB SDK per edit.

The pip layer used to come after every source `COPY`, so any code change
invalidated it and re-fetched `deepseek-harness-sdk` (measured at ~28 kB/s in
the builder). Pinning the SDK in its own layer above the source copies keeps
that download cached; an optional `PIP_FIND_LINKS` build arg serves air-gapped
and CI builds from a wheel dir.
"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "deploy" / "backend.Dockerfile"


def _instructions() -> list[str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_sdk_installs_before_any_source_copy():
    instructions = _instructions()
    sdk_layer = next(
        index
        for index, line in enumerate(instructions)
        if line.startswith("RUN") and "deepseek-harness-sdk" in line
    )
    first_copy = next(index for index, line in enumerate(instructions) if line.startswith("COPY"))
    assert sdk_layer < first_copy


def test_offline_wheel_dir_is_a_build_arg():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "PIP_FIND_LINKS" in text

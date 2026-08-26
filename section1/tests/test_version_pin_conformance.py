"""Contracts version-pin conformance — revived M13-H3b (audit F6 / raw-23 [4]).

History: the original ``test_contracts_version_frozen()`` was defined in
``section1/tests/conftest.py`` — pytest never collects tests from conftest, so
the guard on the monorepo's most sacred invariant never ran once (it also
drifted to a stale ``== "0.1.0"`` literal). This file revives it as a REAL,
collected test and widens it into the 4-way rot detector the audits demanded:
all four places a contracts version lives must tell the same story.

    1. installed distribution metadata   importlib.metadata.version("harness-contracts")
                                         (canonical release truth, per raw-23)
    2. contracts/pyproject.toml          [project] version
    3. contracts/src/harness_contracts/__init__.py   __version__
    4. contracts/ts/types.d.ts           header "// TS mirror of harness_contracts vX.Y.Z"
                                         (X.Y.Z may carry a PEP 440 pre-release
                                         suffix, e.g. 1.0.0rc1)

Failure policy (deliberate asymmetry):

* SOURCE-tree divergence (pyproject vs __init__ vs types.d.ts header) is a hard
  FAIL listing every divergent source plus the exact remedy — that is genuine
  rot inside the frozen package.
* A mismatching INSTALLED metadata version is a hard FAIL only when it did not
  come from this working tree. An editable install stamped BEFORE a version
  bump (direct_url.json pointing back at this very contracts/ dir) is
  provisioning lag on one developer machine, not package rot: it emits a loud
  warning telling you to re-run ``pip install -e ../contracts``, and stays
  green. CI installs fresh at every run, so the strict equality above always
  holds where it matters.

It also checks consumer floor pins (``harness-contracts>=X`` in sectionN
pyprojects) are satisfiable by the released version, so a future downgrade
can't sneak past a stale pin.

Version bumps here are atomic by repo law (AGENTS.md): all four stamps move in
one commit alongside the TS-mirror regen (``python3 contracts/ts/generate.py
--check`` enforces the generated files separately).
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import warnings
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]

PYPROJECT = REPO_ROOT / "contracts" / "pyproject.toml"
CONTRACTS_DIR = REPO_ROOT / "contracts"
INIT_PY = CONTRACTS_DIR / "src" / "harness_contracts" / "__init__.py"
TS_MIRROR = CONTRACTS_DIR / "ts" / "types.d.ts"

_DIST_NAME = "harness-contracts"
_TS_HEADER_RE = re.compile(
    r"^//\s*TS mirror of harness_contracts\s+v(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)", re.M
)
_INIT_VER_RE = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)"', re.M)
_PYPROJECT_VER_RE = re.compile(r'^version\s*=\s*"(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)"', re.M)
_FLOOR_PIN_RE = re.compile(r'[\'"]harness-contracts>=((?:\d+\.){1,2}\d+(?:(?:a|b|rc)\d+)?)[\'"]')


def _as_tuple(version: str) -> tuple[int | str, ...]:
    """Sort key over release segment plus optional pre-release phase.

    Release-only versions sort AFTER their own pre-releases (1.0.0 > 1.0.0rcN)
    by appending a sentinel; pre-release phases order a < b < rc per PEP 440.
    """
    match = re.fullmatch(
        r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", version
    )
    if match is None:
        return (0,)
    release = tuple(int(p) for p in match.group(1).split("."))
    if match.group(2) is None:
        return (*release, "z")
    return (*release, match.group(2), int(match.group(3)))


def _read_installed_version() -> str | None:
    try:
        return importlib.metadata.version(_DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None


def _installed_from_this_tree() -> bool:
    """True iff the discovered dist is a local install OF THIS REPO.

    Two shapes count (both are provisioning artifacts, not package rot):

    * classic editable: site-packages ``*.dist-info/direct_url.json`` pointing
      back at this very contracts/ dir;
    * in-source egg-info: importlib.metadata resolving
      ``contracts/src/harness_contracts.egg-info`` directly — pytest injects
      ``../contracts/src`` into sys.path (pyproject pythonpath), so the source
      tree's own build residue is frequently what gets discovered FIRST.
    """
    try:
        dist = importlib.metadata.distribution(_DIST_NAME)
        if dist is None:
            return False

        # Shape 2: dist metadata resolved from INSIDE the repo working tree.
        dist_path = getattr(dist, "_path", None)
        if dist_path is not None:
            try:
                Path(dist_path).resolve().relative_to(CONTRACTS_DIR.resolve())
                return True
            except ValueError:
                pass

        # Shape 1: classic editable pointing back at this tree.
        raw = dist.read_text("direct_url.json")
        if not raw:
            return False
        info = json.loads(raw)
        if not info.get("dir_info", {}).get("editable"):
            return False
        url = info.get("url", "")
        local_path = Path(unquote(urlparse(url).path)).resolve()
        return local_path == CONTRACTS_DIR.resolve()
    except Exception:
        return False


def _collect_source_versions() -> dict[str, str | None]:
    """Read the three file-backed version stamps; None = unreadable/unparseable."""
    out: dict[str, str | None] = {}

    match = _PYPROJECT_VER_RE.search(PYPROJECT.read_text(encoding="utf-8"))
    out["contracts/pyproject.toml [project].version"] = (
        match.group(1) if match else None
    )

    match = _INIT_VER_RE.search(INIT_PY.read_text(encoding="utf-8"))
    out["contracts/src/harness_contracts/__init__.py __version__"] = (
        match.group(1) if match else None
    )

    header = _TS_HEADER_RE.search(TS_MIRROR.read_text(encoding="utf-8"))
    out["contracts/ts/types.d.ts mirror header"] = header.group(1) if header else None
    return out


def _canonical_source_version(sources: dict[str, str | None]) -> str | None:
    values = {ver for ver in sources.values() if ver is not None}
    return next(iter(values)) if len(values) == 1 else None


def test_contracts_version_pinned_across_all_four_sources():
    """THE rot detector: all four contracts version stamps must tell one story."""
    sources = _collect_source_versions()

    unreadable = [name for name, ver in sources.items() if ver is None]
    assert not unreadable, (
        f"Contracts version sources unreadable — fix before anything else: {unreadable}"
    )

    distinct = sorted(set(sources.values()))  # type: ignore[arg-type]
    assert len(distinct) == 1, (
        "CONTRACTS VERSION ROT in the frozen package source tree — "
        f"{len(distinct)} different version stories:\n"
        + "\n".join(f"  - {name}: {ver}" for name, ver in sources.items())
        + "\n\nRemedy (atomic, ONE commit, per AGENTS.md): align every source at "
        f"{max(distinct, key=_as_tuple)} — set __init__.py __version__, keep "
        "pyproject.toml [project].version as release truth, regenerate/update the "
        "TS mirror header. Never bump one source alone."
    )
    canonical = distinct[0]

    installed = _read_installed_version()
    if installed is None:
        warnings.warn(
            "harness-contracts is not installed as a distribution in this "
            "environment; skipping the metadata leg of the 4-way pin check "
            "(CI always installs it, so parity is still enforced there).",
            stacklevel=1,
        )
    elif installed != canonical:
        if _installed_from_this_tree():
            warnings.warn(
                f"harness-contracts installed dist metadata says {installed} but "
                f"the source tree is at {canonical}. This is a stale EDITABLE "
                "install of this same contracts/ dir (pre-bump provisioning), NOT "
                "package rot. Heal locally with:\n"
                "  ./.venv/bin/pip install -q -e ../contracts   # from any section\n"
                "(CI installs fresh each run and enforces strict equality.)",
                stacklevel=1,
            )
        else:
            raise AssertionError(
                f"Installed harness-contracts {installed} != source-tree version "
                f"{canonical}, and it is NOT a local install of this tree "
                "(stale/wrong artifact shadowing the repo — uninstall it or fix "
                "the venv provisioning)."
            )


def test_consumer_floor_pins_satisfiable_by_released_version():
    """sectionN 'harness-contracts>=X' floors must be <= the released version."""
    sources = _collect_source_versions()
    released = _canonical_source_version(sources)
    assert released is not None, (
        "Source-tree versions disagree (see the 4-way pin test); cannot "
        "evaluate floor pins against a moving target."
    )

    violations: list[str] = []
    for pyproj in sorted(REPO_ROOT.glob("section*/pyproject.toml")):
        text = pyproj.read_text(encoding="utf-8")
        for floor in _FLOOR_PIN_RE.findall(text):
            if _as_tuple(floor) > _as_tuple(released):
                violations.append(f"  - {pyproj.relative_to(REPO_ROOT)}: >= {floor}")

    assert not violations, (
        f"harness-contracts released as {released}, but unsatisfiable floor pins:\n"
        + "\n".join(violations)
        + "\nRaise the released version or lower the pin — dependency resolution "
        "would otherwise pick a broken combination."
    )

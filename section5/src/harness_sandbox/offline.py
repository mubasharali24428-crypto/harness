"""S4: offline / air-gapped provisioning from a local, hash-verified package mirror.

Online side (outside this module): ``pip download`` into a directory, then ``Mirror.index``
records sha256 for every artifact. Air-gapped side: ``Mirror.verify`` detects any
tampering, and installs use ``--no-index --find-links`` so no network is attempted.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_WHEEL = re.compile(r"^(?P<name>[A-Za-z0-9_.]+)-(?P<ver>[0-9][^-]*)(-[^-]+){3}\.whl$")


class MirrorError(Exception):
    pass


class Mirror:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @property
    def index_path(self) -> Path:
        return self.root / "mirror-index.json"

    def index(self) -> dict:
        entries = {}
        for f in sorted(self.root.glob("*.whl")):
            m = _WHEEL.match(f.name)
            if not m:
                raise MirrorError(f"unrecognised artifact name: {f.name}")
            name = re.sub(r"[-_.]+", "-", m["name"]).lower()
            entries.setdefault(name, {})[m["ver"]] = {
                "file": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        self.index_path.write_text(json.dumps(entries, indent=1, sort_keys=True))
        return entries

    def lookup(self, package: str, version: str) -> dict:
        data = json.loads(self.index_path.read_text())
        try:
            return data[re.sub(r"[-_.]+", "-", package).lower()][version]
        except KeyError as e:
            raise MirrorError(f"{package}=={version} is not in the mirror") from e

    def verify(self) -> list[str]:
        """Names of artifacts whose bytes no longer match the recorded hash (or are missing)."""
        bad = []
        for versions in json.loads(self.index_path.read_text()).values():
            for meta in versions.values():
                f = self.root / meta["file"]
                if not f.exists() or hashlib.sha256(f.read_bytes()).hexdigest() != meta["sha256"]:
                    bad.append(meta["file"])
        return sorted(bad)

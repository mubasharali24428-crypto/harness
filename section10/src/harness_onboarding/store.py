"""Tiny JSON-file persistence for Section 10 state.

Section 1 owns the product's Postgres schema (append-only migrations), and
telemetry consent records are not part of the frozen 17-table schema — so this
section keeps its own state in a single JSON file, volume-mounted in deploy.
Atomic writes only; corrupt/missing file starts from defaults.
"""

import json
import os
import tempfile
from pathlib import Path
from threading import RLock

DEFAULT_STATE: dict = {
    "securityChecklistAcknowledged": False,
    "telemetry": {
        "baselineEnabled": True,
        "errorLogs": True,
        "usageAnalytics": True,
        "promptMetadata": True,
        "codeMetadata": True,
        "fullPromptText": False,
        "fullCodeContent": False,
    },
    "telemetryEvents": [],
}


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> dict:
        with self._lock:
            if not self.path.exists():
                return json.loads(json.dumps(DEFAULT_STATE))
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return json.loads(json.dumps(DEFAULT_STATE))
            merged = json.loads(json.dumps(DEFAULT_STATE))
            merged.update(data)
            merged["telemetry"] = {**DEFAULT_STATE["telemetry"], **data.get("telemetry", {})}
            return merged

    def save(self, state: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def update(self, mutator) -> dict:
        with self._lock:
            state = self.load()
            mutator(state)
            self.save(state)
            return state

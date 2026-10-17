"""S1-R02e — the deployed assembly's boot gate (deploy/runners/services.py).

The production image must NEVER silently run secrets under the public dev
master key. The gate lives at module import time and had ZERO tests; this
file pins the matrix by importing the module in a subprocess with a
controlled environment:

* missing key, no opt-in            -> refuse to start
* key identical to the dev constant -> refuse to start
* real key                           -> boot proceeds
* explicit HARNESS_ALLOW_DEV_MASTER_KEY=1 -> boot proceeds (local dev)
"""

import os
import subprocess
import sys
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parents[3] / "deploy" / "runners"
DEV_KEY = "dev-master-key-change-me-in-prod!"
REAL_KEY = "0123456789abcdef0123456789abcdef"


def _boot(env_overrides: dict[str, str | None]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("HARNESS_MASTER_KEY", None)
    env.pop("HARNESS_ALLOW_DEV_MASTER_KEY", None)
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-c", "import services; print('boot-ok')"],
        cwd=SERVICES_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_missing_master_key_refuses_to_start():
    result = _boot({})
    assert result.returncode != 0
    assert "Refusing to start" in result.stderr


def test_dev_constant_master_key_refuses_to_start():
    result = _boot({"HARNESS_MASTER_KEY": DEV_KEY, "HARNESS_ALLOW_DEV_MASTER_KEY": None})
    assert result.returncode != 0
    assert "Refusing to start" in result.stderr


def test_real_master_key_boots():
    result = _boot({"HARNESS_MASTER_KEY": REAL_KEY})
    assert result.returncode == 0, result.stderr
    assert "boot-ok" in result.stdout


def test_explicit_dev_optin_boots():
    result = _boot({"HARNESS_ALLOW_DEV_MASTER_KEY": "1"})
    assert result.returncode == 0, result.stderr
    assert "boot-ok" in result.stdout

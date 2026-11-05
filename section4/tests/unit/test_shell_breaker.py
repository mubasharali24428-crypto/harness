"""W27: the shell plugin's denial circuit breaker (3 in a row, or 10 of the last 50)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = (Path(__file__).resolve().parents[2] / "src" / "harness_orchestrator"
          / "dsh_plugins" / "harness-shell.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def trips(pattern: str) -> list[bool]:
    """Feed 'D' (denied) / '.' (allowed) calls to a fresh breaker; return tripped after each."""
    script = (f"import {{ denialBreaker }} from {json.dumps(PLUGIN.as_uri())};"
              f"const b = denialBreaker(); console.log(JSON.stringify([...{json.dumps(pattern)}]"
              ".map((c) => b.record(c === 'D'))));")
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def test_three_denials_in_a_row_trip_it_and_it_stays_tripped():
    assert trips("DDD.") == [False, False, True, True]


def test_an_allowed_call_resets_the_run():
    assert not any(trips("DD.DD.DD."))


def test_ten_denials_in_fifty_calls_trip_it_even_when_spread_out():
    assert not any(trips("D.." * 9))
    assert trips("D.." * 10)[-3] is True and not any(trips("D.." * 10)[:-3])


def test_plugin_and_gate_together_stop_a_probing_run(tmp_path):
    """No model: drive the real plugin over the real gate. Three out-of-scope reads trip the
    breaker; the fourth call is refused by the guard, and the denial carries the scope request."""
    import sys
    gate = Path(__file__).resolve().parents[3] / "section5/src/harness_sandbox/shell_gate.py"
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    script = f"""
import {{ apply }} from {json.dumps(PLUGIN.as_uri())};
let guard, tool;
const ctx = {{ workingDirectory: {{ ensure: async () => {json.dumps(str(tmp_path))} }},
  tools: {{ guard: (f) => (guard = f), register: (t) => (tool = t) }} }};
apply(ctx, {{ scopePaths: ["src"],
  gate: [{json.dumps(sys.executable)}, "-I", {json.dumps(str(gate))}] }});
const out = [];
for (let i = 0; i < 4; i++) {{
  const exec = {{ callId: "c" + i, agent: "a", signal: new AbortController().signal }};
  const blocked = guard(exec);
  if (blocked) {{ out.push({{ guard: blocked }}); continue; }}
  try {{ await tool.execute({{ command: "ls docs" }}, exec); out.push({{ ok: true }}); }}
  catch (e) {{ out.push({{ error: e.message }}); }}
}}
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True, timeout=60)
    out = json.loads(res.stdout)
    assert "outside the run intent's scope paths" in out[0]["error"]
    assert '[scope-request] {"path":"docs","grants":false}' in out[0]["error"]
    assert "has been stopped" not in out[1]["error"] and "has been stopped" in out[2]["error"]
    assert "has been stopped after repeated denials" in out[3]["guard"]

import json, os
from harness_orchestrator.engine import DeepSeekHarnessWrapper
os.makedirs("/tmp/ws/run1/src"); os.makedirs("/tmp/ws/run1/docs")
w = DeepSeekHarnessWrapper(workspace_dir="/tmp/ws", session_root="/tmp/sess", api_key="sk-probe0123456789",
                           base_url=os.environ["PROBE_BASE_URL"], model="deepseek-chat", live_mode="live", isolated=False)
w.set_shell_policy("run1", {"maxToolCalls": 10, "scopePaths": ["src"], "forbiddenPaths": []})
r = w.execute_goal("probe", session_id="run1")
print("STATUS", r["status"], r["finalResponse"])
for c in r.get("toolCalls", []): print("CALL", json.dumps(c))

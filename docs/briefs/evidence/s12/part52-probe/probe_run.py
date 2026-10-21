import json
import os

from deepseek_harness import DeepSeekHarness

ws, dsh = "/tmp/ws", "/tmp/dsh"
os.makedirs(ws, exist_ok=True)
os.makedirs(dsh, exist_ok=True)
mode = os.environ.get("PROBE_MODE", "workspace-write")
patch = os.path.join(dsh, "policy.yml")
with open(patch, "w", encoding="utf-8") as f:
    f.write(
        "- id: sandbox-policy\n"
        "  name: '@deepseek-ai/dsh-sandbox-policy'\n"
        "  disabled: false\n"
        "  config:\n"
        f"    mode: {mode}\n"
        f"    workspaceRoot: {json.dumps(ws)}\n"
    )
with DeepSeekHarness(
    provider="deepseek-official",
    model="deepseek-chat",
    max_tokens=4096,
    cwd=ws,
    dsh_home=dsh,
    profile="sdk-minimal",
    api_key="probe-key",
    base_url=os.environ["PROBE_BASE_URL"],
    patches=(patch,),
) as h:
    result = h.run("probe", session_id="probe-1")
print("FINAL:", result.final_response)

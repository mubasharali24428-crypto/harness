import os
import sys
from pathlib import Path

from deepseek_harness import DeepSeekHarness

here = Path(__file__).resolve().parent
workspace = here / "workspace"
sessions = here / "sessions"
workspace.mkdir(exist_ok=True)
sessions.mkdir(exist_ok=True)

kwargs = dict(
    provider=os.environ.get("DSH_PROVIDER", "deepseek-official"),
    model=os.environ["DSH_MODEL"],
    max_tokens=4096,
    cwd=str(workspace),
    session_root=str(sessions),
)
if (here / "minimal.cordis.yml").exists():
    kwargs["cordis"] = str(here / "minimal.cordis.yml")

with DeepSeekHarness(**kwargs) as harness:
    result = harness.run(
        "Create a file named hello.txt containing the text 'task0 ok' and then tell me the exact contents of that file.",
        session_id="hello-001",
    )
    print("FINAL RESPONSE:")
    print(result.final_response)

hello_file = workspace / "hello.txt"
print("FILE EXISTS:", hello_file.exists())
if hello_file.exists():
    print("FILE CONTENT:", hello_file.read_text())

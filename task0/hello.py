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
    profile="sdk-minimal",
    cwd=str(workspace),
    dsh_home=str(sessions),
)
if os.environ.get("DEEPSEEK_BASE_URL"):
    kwargs["base_url"] = os.environ["DEEPSEEK_BASE_URL"]
if os.environ.get("DEEPSEEK_API_KEY"):
    kwargs["api_key"] = os.environ["DEEPSEEK_API_KEY"]
if (here / "minimal.cordis.yml").exists():
    kwargs["patches"] = (str(here / "minimal.cordis.yml"),)

with DeepSeekHarness(**kwargs) as harness:
    result = harness.run(
        "Create a file named hello.txt containing the text 'task0 ok' and then tell me the exact contents of that file.",
        session_id="hello-001",
    )
    print("FINAL RESPONSE:")
    print(result.final_response)
    print("FINISH REASON:", result.finish_reason)

hello_file = workspace / "hello.txt"
print("FILE EXISTS:", hello_file.exists())
if hello_file.exists():
    print("FILE CONTENT:", hello_file.read_text())
if not hello_file.is_file() or hello_file.read_text().strip() != "task0 ok":
    sys.exit("Task0 verification failed: expected file and content were not produced.")

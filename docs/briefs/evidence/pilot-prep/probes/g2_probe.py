"""G2 negative drill (p04 review §7.3), macOS analogue.
Linux reads /proc/<runner-pid>/environ; macOS exposes the same bytes through
`ps eww <pid>` for same-UID processes. A Seatbelt-confined gate child (file
reads allow-listed to the workspace, network denied) tries both the environ
side-channel and a $HOME canary. Gap evidence, not a pass/fail gate."""
import subprocess, sys, os
from pathlib import Path

sys.path.insert(0, "/Users/user/untitled folder 2/harness/section5/src")
from harness_sandbox.confinement import confine_argv, seatbelt_available

runner_pid = int(Path("/tmp/harness-g2/runner.pid").read_text().strip())
ws = Path("/tmp/harness-g2/ws"); tmp = Path("/tmp/harness-g2/tmp"); tmp.mkdir(exist_ok=True)
print("seatbelt_available:", seatbelt_available())

argv = ["sh", "-c",
        f"ps eww {runner_pid} 2>/dev/null | grep -o hrn_g2[A-Za-z0-9]* | head -1; "
        f"echo '---'; cat \"$HOME/.harness-g2-canary\" 2>&1 | head -c 30; echo"]
wrapped = confine_argv(argv, workspace=ws, tmpdir=tmp, allow_network=False)
r = subprocess.run(wrapped, capture_output=True, text=True, timeout=30)
out = r.stdout
lines = out.split("---")
token_line = lines[0].strip()
canary_line = lines[1].strip() if len(lines) > 1 else ""
print("exit:", r.returncode)
print("environ side-channel via ps eww:", repr(token_line))
print("home canary read:", repr(canary_line))
print("G2_VERDICT:",
      "ENVIRON-LEAKED ps-eww-exposes-runner-env" if token_line.startswith("hrn_g2")
      else "ENVIRON-PROTECTED")
print("CANARY_VERDICT:", "READ-GAP" if "G2-SSH-CANARY" in canary_line else "READ-BLOCKED")

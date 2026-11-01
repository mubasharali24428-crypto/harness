#!/bin/zsh
# Opens a clean VS Code window for recording the demo: its own profile (nothing personal on screen),
# the installed .vsix, and a two-bug workspace (calc.py, shout.py) so one change can be rejected.
#
#     scripts/demo/yc_demo_vscode.sh            # fresh workspace + window
#     scripts/demo/yc_demo_vscode.sh --otp EMAIL  # print the sign-in code for EMAIL
#
# Same setup as clients/vscode/scripts/w24_window_run.mjs --demo, which rehearses this path automated.
set -eu
REPO=${0:A:h:h:h}
PROFILE=/tmp/harness-demo-profile   # short: macOS caps unix socket paths at ~104 chars
WS=$HOME/harness-demo
CODE="/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"

if [[ ${1:-} == --otp ]]; then
  docker logs --tail 400 "$(docker ps -q --filter label=com.docker.compose.service=identity | head -1)" 2>&1 \
    | grep -o "HARNESS-OTP login code for ${2}: [0-9]*" | tail -1 | grep -o '[0-9]*$'
  exit 0
fi

# ponytail: wipes the demo workspace each time, so every take starts from the same two bugs.
rm -rf "$WS" && mkdir -p "$WS" "$PROFILE/User"
print 'def add(a, b):\n    return a - b' > "$WS/calc.py"
print 'def shout(s):\n    return s.lower() + "!"' > "$WS/shout.py"
cat > "$WS/test_calc.py" <<'EOF'
import unittest

from calc import add


class AddTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
EOF
cat > "$WS/test_shout.py" <<'EOF'
import unittest

from shout import shout


class ShoutTest(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(shout("hi"), "HI!")


if __name__ == "__main__":
    unittest.main()
EOF

cat > "$PROFILE/User/settings.json" <<EOF
{
  "window.restoreWindows": "none",
  "security.workspace.trust.enabled": false,
  "workbench.startupEditor": "none",
  "workbench.tips.enabled": false,
  "extensions.ignoreRecommendations": true,
  "chat.disableAIFeatures": true,
  "editor.fontSize": 16,
  "window.zoomLevel": 1,
  "harness.repoRoot": "$REPO",
  "harness.launcherPython": "$REPO/section4/.venv/bin/python",
  "harness.runTimeoutSeconds": 600
}
EOF

"$CODE" --user-data-dir "$PROFILE" --extensions-dir "$PROFILE/ext" \
  --install-extension "$REPO/clients/vscode/harness-vscode.vsix" --force >/dev/null

# The window gets the provider settings through its environment only, as in the rehearsal.
export HARNESS_IMAGE_TAG=$(docker inspect -f '{{.Config.Image}}' harness-orchestrator-1 | sed 's/.*:v//')
for v in DEEPSEEK_API_KEY DEEPSEEK_BASE_URL DSH_MODEL DSH_PATCH_FILES; do
  line=$(grep -m1 "^$v=" "$REPO/.env" || true)
  [[ -n $line ]] && export "$v=${${line#*=}//\"/}"
done
"$CODE" --user-data-dir "$PROFILE" --extensions-dir "$PROFILE/ext" --new-window "$WS" "$WS/calc.py"
print "Opened $WS (image tag $HARNESS_IMAGE_TAG). Sign-in code: $0 --otp <your email>"

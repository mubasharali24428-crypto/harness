#!/bin/sh
# install-launchd.sh — install (or --uninstall) the nightly backup as a per-user launchd agent.
#   03:17 local daily PLUS an hourly tick, both `nightly.sh --if-due`: the run happens when the newest base
#   backup is > 23 h old, so a Mac that slept through 03:17 (or had Docker down) catches up on wake/the next
#   hour. `caffeinate -i` stops idle sleep while a backup runs; it cannot wake a sleeping Mac (that needs
#   `sudo pmset repeat wakeorpoweron MTWRFSU 03:15:00`, a system setting left to the owner).
#   Logs: ~/harness-backups/nightly.log. A failed or skipped night is also caught by HarnessBackupStale (> 25 h).
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
LABEL=com.harness.nightly-backup
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
if [ "${1:-}" = "--uninstall" ]; then rm -f "$PLIST"; echo "removed $LABEL"; exit 0; fi
mkdir -p "$HOME/harness-backups" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-i</string><string>/bin/sh</string><string>$ROOT/deploy/backup/nightly.sh</string><string>--if-due</string></array>
  <key>StartInterval</key><integer>3600</integer>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>17</integer></dict>
  <key>StandardOutPath</key><string>$HOME/harness-backups/nightly.log</string>
  <key>StandardErrorPath</key><string>$HOME/harness-backups/nightly.log</string>
</dict></plist>
PL
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "installed $LABEL -> $PLIST (next: 03:17 local)"

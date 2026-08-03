#!/usr/bin/env bash
# Install / reload macOS LaunchAgent that publishes the report when VPN is up.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.work-reporter.publish"
SRC="$ROOT/scripts/${LABEL}.plist"
DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"
chmod +x "$ROOT/scripts/publish-if-vpn.sh"

sed \
  -e "s#__PROJECT_ROOT__#${ROOT}#g" \
  -e "s#__HOME__#${HOME}#g" \
  "$SRC" >"$DEST"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true

echo "Installed: $DEST"
echo "Poll: every 2 min · publish cadence: 30 min · hours: 09:00–19:00"
echo "Script: $ROOT/scripts/publish-if-vpn.sh"
echo "Logs: ~/Library/Logs/work-reporter.log"
echo
echo "Useful:"
echo "  launchctl print gui/\$(id -u)/${LABEL}"
echo "  tail -f ~/Library/Logs/work-reporter.log"
echo "  # uninstall:"
echo "  launchctl bootout gui/\$(id -u)/${LABEL} && rm -f $DEST"

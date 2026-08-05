#!/usr/bin/env bash
# Poll often via launchd; publish at most every PUBLISH_EVERY_SEC when VPN is up
# and only within ACTIVE_HOUR_START..ACTIVE_HOUR_END (local time).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${HOME}/Library/Logs"
LOG_FILE="${LOG_DIR}/work-reporter.log"
STATE_FILE="${LOG_DIR}/work-reporter.last-publish"
LOCK_DIR="${TMPDIR:-/tmp}/work-reporter.publish.lock"

# How often the report may be rebuilt/published.
PUBLISH_EVERY_SEC="${PUBLISH_EVERY_SEC:-3600}"
# Inclusive local hour window: [start, end). Default 09:00–19:00.
ACTIVE_HOUR_START="${ACTIVE_HOUR_START:-9}"
ACTIVE_HOUR_END="${ACTIVE_HOUR_END:-19}"

mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"
}

hour_now="$(date '+%H')"
hour_now=$((10#$hour_now))
if (( hour_now < ACTIVE_HOUR_START || hour_now >= ACTIVE_HOUR_END )); then
  # Quiet outside working hours — avoid log spam on frequent polls.
  exit 0
fi

now_epoch="$(date '+%s')"
if [[ -f "$STATE_FILE" ]]; then
  last_epoch="$(tr -d '[:space:]' <"$STATE_FILE" || true)"
  if [[ "$last_epoch" =~ ^[0-9]+$ ]]; then
    elapsed=$((now_epoch - last_epoch))
    if (( elapsed < PUBLISH_EVERY_SEC )); then
      # Due window not reached yet — do not touch Jira/VPN.
      exit 0
    fi
  fi
fi

if [[ ! -f "$ROOT/.env" ]]; then
  log "skip: missing .env"
  exit 0
fi

set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

# PUBLISH_NOTIFY_SCRIPT runs via ssh ON THE SERVER. Bash `source .env` expands
# ~/… to this Mac's $HOME, which then fails remotely. Put the tilde back.
if [[ -n "${PUBLISH_NOTIFY_SCRIPT:-}" && "${PUBLISH_NOTIFY_SCRIPT}" == "$HOME"/* ]]; then
  PUBLISH_NOTIFY_SCRIPT="~${PUBLISH_NOTIFY_SCRIPT#"$HOME"}"
  export PUBLISH_NOTIFY_SCRIPT
fi

JIRA_URL="${JIRA_URL:-}"
if [[ -z "$JIRA_URL" ]]; then
  log "skip: JIRA_URL empty"
  exit 0
fi

# host from https://jira.example.com/...
JIRA_HOST="$(printf '%s' "$JIRA_URL" | sed -E 's#^[a-zA-Z]+://##' | cut -d/ -f1 | cut -d@ -f2 | cut -d: -f1)"
if [[ -z "$JIRA_HOST" ]]; then
  log "skip: cannot parse JIRA host from JIRA_URL"
  exit 0
fi

if ! nc -z -G 2 "$JIRA_HOST" 443 >/dev/null 2>&1; then
  log "skip: VPN/Jira unreachable ($JIRA_HOST:443) — retry on next poll"
  exit 0
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "skip: another publish is running"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  log "error: missing venv python at $PYTHON"
  exit 1
fi

log "start: python run.py --publish (vpn ok → $JIRA_HOST)"
set +e
# Mark trigger for Telegram notify wording («Режим: Автоматически»).
export WORK_REPORTER_PUBLISH_MODE=auto
"$PYTHON" "$ROOT/run.py" --publish >>"$LOG_FILE" 2>&1
rc=$?
set -e
if [[ $rc -eq 0 ]]; then
  date '+%s' >"$STATE_FILE"
  log "done: publish ok"
else
  log "error: publish failed (exit $rc)"
  if command -v osascript >/dev/null 2>&1; then
    osascript -e 'display notification "publish завершился с ошибкой — смотри Library/Logs/work-reporter.log" with title "work-reporter"' || true
  fi
  exit "$rc"
fi

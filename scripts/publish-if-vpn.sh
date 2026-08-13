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

JIRA_TOKEN="${JIRA_TOKEN:-}"
if [[ -z "$JIRA_TOKEN" ]]; then
  log "skip: JIRA_TOKEN empty"
  exit 0
fi

# Real API preflight (not TCP ping): VPN/TLS/auth must work for collect.
# /rest/api/2/myself is cheap; non-200 → skip without Telegram noise.
jira_base="${JIRA_URL%/}"
myself_url="${jira_base}/rest/api/2/myself"

curl_config_escape() {
  local value="${1:-}"
  case "$value" in
    *$'\n'*|*$'\r'*) return 1 ;;
  esac
  printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

jira_auth_config() {
  local escaped_token escaped_identity
  escaped_token="$(curl_config_escape "$JIRA_TOKEN")" || return 1
  if [[ -n "${JIRA_EMAIL:-}" ]]; then
    escaped_identity="$(curl_config_escape "$JIRA_EMAIL")" || return 1
    printf 'user = "%s:%s"\n' "$escaped_identity" "$escaped_token"
  elif [[ -n "${JIRA_USER:-}" ]]; then
    escaped_identity="$(curl_config_escape "$JIRA_USER")" || return 1
    printf 'user = "%s:%s"\n' "$escaped_identity" "$escaped_token"
  else
    printf 'header = "Authorization: Bearer %s"\n' "$escaped_token"
  fi
}

case "${JIRA_TOKEN}${JIRA_EMAIL:-}${JIRA_USER:-}" in
  *$'\n'*|*$'\r'*)
    log "skip: Jira credentials contain unsupported line breaks"
    exit 0
    ;;
esac

http_code="$(
  # Keep secrets in shell memory and the curl stdin config, not argv/environment.
  export -n JIRA_TOKEN
  if [[ -n "${GITLAB_TOKEN+x}" ]]; then export -n GITLAB_TOKEN; fi
  if [[ -n "${CORP_LLM_TOKEN+x}" ]]; then export -n CORP_LLM_TOKEN; fi
  jira_auth_config |
    curl --config - -sS -o /dev/null -w '%{http_code}' \
      --connect-timeout 5 --max-time 8 \
      "$myself_url" 2>/dev/null || true
)"
http_code="${http_code:-000}"
if [[ "$http_code" != "200" ]]; then
  log "skip: Jira API unavailable (myself HTTP ${http_code}) — VPN off or auth/TLS issue"
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

log "start: python run.py --publish (jira myself ok)"
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

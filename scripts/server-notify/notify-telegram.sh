#!/usr/bin/env bash
# Runs on the home server only. Sends Telegram message to a forum topic.
# Usage:
#   ./notify-telegram.sh success "text…"
#   ./notify-telegram.sh error "text…"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ROOT}/.env"
STATUS="${1:-}"
shift || true
TEXT="${*:-}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "notify-telegram: missing $ENV_FILE" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

: "${TG_BOT_TOKEN:?TG_BOT_TOKEN required}"
: "${TG_CHAT_ID:?TG_CHAT_ID required}"
: "${TG_MESSAGE_THREAD_ID:?TG_MESSAGE_THREAD_ID required}"

if [[ -z "$STATUS" || -z "$TEXT" ]]; then
  echo "usage: $0 success|error <message>" >&2
  exit 2
fi

case "$STATUS" in
  success|ok) prefix="✅" ;;
  error|fail|failed) prefix="❌" ;;
  *) prefix="ℹ️" ;;
esac

REPORT_URL="${REPORT_URL:-}"
BODY="${prefix} ${TEXT}"
if [[ -n "$REPORT_URL" && "$STATUS" =~ ^(success|ok)$ ]]; then
  BODY="${BODY}"$'\n'"${REPORT_URL}"
fi

resp="$(curl -sS -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" \
  --data-urlencode "message_thread_id=${TG_MESSAGE_THREAD_ID}" \
  --data-urlencode "text=${BODY}" \
  --data-urlencode "disable_web_page_preview=true")"

ok="$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("ok", False))' 2>/dev/null || echo False)"
if [[ "$ok" != "True" ]]; then
  echo "notify-telegram: Telegram API error: $resp" >&2
  exit 1
fi
echo "notify-telegram: sent ($STATUS)"

#!/usr/bin/env bash
# Runs on the home server. Finds forum topic from recent bot updates.
# Prefers a message containing "нужный топик" or mentioning the bot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ROOT}/.env"
TOKEN="${1:-}"

if [[ -z "$TOKEN" && -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
  TOKEN="${TG_BOT_TOKEN:-}"
fi
if [[ -z "$TOKEN" ]]; then
  echo "usage: $0 <bot_token>   # or set TG_BOT_TOKEN in .env" >&2
  exit 2
fi

python3 - "$TOKEN" <<'PY'
import json, sys, urllib.request

token = sys.argv[1]
url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100"
with urllib.request.urlopen(url, timeout=30) as resp:
    data = json.load(resp)
if not data.get("ok"):
    print("getUpdates failed:", data, file=sys.stderr)
    sys.exit(1)

updates = data.get("result") or []
candidates = []
for upd in updates:
    msg = upd.get("message") or upd.get("channel_post") or {}
    text = (msg.get("text") or "")
    chat = msg.get("chat") or {}
    thread = msg.get("message_thread_id")
    if thread is None:
        continue
    chat_id = chat.get("id")
    if chat_id is None:
        continue
    score = 0
    low = text.lower()
    if "нужный топик" in low:
        score += 100
    if "sprint_reporter_bot" in low or "@sprint_reporter" in low:
        score += 50
    if msg.get("is_topic_message"):
        score += 10
    candidates.append(
        (score, upd.get("update_id", 0), chat_id, thread, chat.get("title"), text[:120])
    )

if not candidates:
    for upd in updates:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        thread = msg.get("message_thread_id")
        if thread is not None and chat.get("id") is not None:
            candidates.append(
                (
                    1,
                    upd.get("update_id", 0),
                    chat["id"],
                    thread,
                    chat.get("title"),
                    (msg.get("text") or "")[:120],
                )
            )

if not candidates:
    print("No forum-topic messages found in getUpdates.", file=sys.stderr)
    print(
        "Send '@sprint_reporter_bot нужный топик' in the target topic, then retry.",
        file=sys.stderr,
    )
    print(f"updates_seen={len(updates)}", file=sys.stderr)
    sys.exit(1)

candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
score, _uid, chat_id, thread, title, text = candidates[0]
print(f"chat_id={chat_id}")
print(f"message_thread_id={thread}")
print(f"chat_title={title or ''}")
print(f"matched_text={text}")
print(f"score={score}")
PY

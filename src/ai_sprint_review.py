from __future__ import annotations

import json
import re
import urllib.error
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .ai_brief import (
    _call_litellm_chat,
    _iso_now,
    _llm_settings,
    _snapshot_hash,
    corporate_author,
    extract_verdict,
    humanize_ai_text,
    short_model_name,
)
from .sprint_window import sprint_name_window
from .team_config import get_team_config


PROMPT_VERSION = "sprint-review-v2"

SYSTEM_PROMPT = """Ты — аналитик завершения спринта. Python уже разобрал Jira changelog,
списания, даты, переходы статусов, текущие и планируемые релизы и передал тебе только
подготовленные факты. Твоя задача — сделать их понятными руководителю и помочь
спланировать следующий спринт.

Правила:
1. Не додумывай причины и не добавляй факты, которых нет в JSON.
2. Сначала дай ясный итог спринта в 1–2 предложениях: что получилось и что помешало.
3. Затем выдели до пяти существенных наблюдений по аномалиям: поздний старт,
   возвраты по статусам, смены исполнителя, поздние списания, незакрытые задачи.
4. Рекомендации должны быть применимы к следующему спринту и опираться на блок
   next_planning: релизы, их описания, ключевые темы и наличие задач в плане.
5. Совпадение темы релиза с Jira-задачей может быть приблизительным. Если confidence
   ниже 0.58 или match_state=uncertain, пиши «не удалось уверенно сопоставить», а не
   утверждай, что задача отсутствует.
6. Если интервал следующего спринта forecast=true, называй его прогнозным.
7. Задачи упоминай только ключом в бэктиках. Людей — коротко и нейтрально.
8. Пиши по-русски, спокойно, конкретно, без эмодзи и общих советов. 140–230 слов.
9. Удобный формат: короткий итог, несколько содержательных буллетов и 2–4
   рекомендации. Не пересказывай весь JSON.
"""

USER_PROMPT_PREFIX = """Подготовь «Оценку прошедшего спринта» по фактам ниже.
Особенно проверь, что ключевые темы ближайших релизов представлены задачами в плане
следующего спринта. Неуверенные совпадения обозначай честно.

JSON:
"""


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_team_config().ratings.timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Moscow")


def _local_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=_tz())
    return value.astimezone(_tz())


def _as_day(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _last_workday(day: date) -> date:
    value = day
    while value.weekday() >= 5:
        value -= timedelta(days=1)
    return value


def sprint_review_available(sprint: dict, *, now: datetime | None = None) -> bool:
    end = _as_day(sprint.get("end_date"))
    if not end:
        return False
    local = _local_now(now)
    gate_day = _last_workday(end)
    start_hour = float(get_team_config().ratings.workday_start_hour)
    gate_time = time(
        hour=min(int(start_hour), 23),
        minute=min(int(round((start_hour % 1) * 60)), 59),
    )
    return local.date() > gate_day or (
        local.date() == gate_day and local.time().replace(tzinfo=None) >= gate_time
    )


def _status_rank(value: object) -> int:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("готов", "done", "closed", "закры", "resolved")):
        return 4
    if any(token in text for token in ("review", "ревью", "тест", "verify", "провер")):
        return 3
    if any(token in text for token in ("progress", "работ", "doing", "разработ")):
        return 2
    if any(token in text for token in ("open", "todo", "выполн", "очеред", "создан")):
        return 1
    return 0


def _events_in_window(items: list[dict], start: date, end: date) -> list[dict]:
    rows = []
    for item in items or []:
        day = _as_day(item.get("at"))
        if day and start <= day <= end:
            rows.append(item)
    return sorted(rows, key=lambda row: str(row.get("at") or ""))


def _issue_anomalies(issues: dict, start: date, end: date) -> tuple[list[dict], dict]:
    midpoint = start + timedelta(days=max((end - start).days // 2, 0))
    anomalies: list[dict] = []
    total_hours = 0.0
    late_hours = 0.0
    late_boundary = _last_workday(end) - timedelta(days=1)
    considered = 0

    for key, issue in (issues or {}).items():
        if not isinstance(issue, dict) or issue.get("in_current_sprint") is False:
            continue
        if issue.get("hidden_from_display"):
            continue
        considered += 1
        history = _events_in_window(issue.get("history") or [], start, end)
        worklogs = _events_in_window(issue.get("worklogs") or [], start, end)
        rollbacks = 0
        first_started: date | None = None
        assignee_changes = 0
        for event in history:
            before = _status_rank(event.get("status_from"))
            after = _status_rank(event.get("status_to"))
            if before and after and after < before:
                rollbacks += 1
            if first_started is None and after >= 2:
                first_started = _as_day(event.get("at"))
            if event.get("assignee_from") != event.get("assignee_to") and (
                event.get("assignee_from") or event.get("assignee_to")
            ):
                assignee_changes += 1

        issue_hours = sum(float(row.get("hours") or 0) for row in worklogs)
        issue_late_hours = sum(
            float(row.get("hours") or 0)
            for row in worklogs
            if (_as_day(row.get("at")) or start) >= late_boundary
        )
        total_hours += issue_hours
        late_hours += issue_late_hours

        signals: list[str] = []
        if rollbacks:
            signals.append(f"возвратов по статусам: {rollbacks}")
        if first_started and first_started > midpoint:
            signals.append(f"старт после середины спринта: {first_started.isoformat()}")
        if assignee_changes >= 2:
            signals.append(f"смен исполнителя: {assignee_changes}")
        if issue.get("direction_state") != "done" and not worklogs:
            signals.append("нет списаний в спринте")
        if issue.get("direction_state") != "done" and _status_rank(issue.get("status")) < 4:
            signals.append(f"не закрыта: {issue.get('status') or 'статус неизвестен'}")
        if signals:
            anomalies.append(
                {
                    "key": key,
                    "summary": issue.get("summary"),
                    "assignee": issue.get("assignee"),
                    "signals": signals,
                    "hours": round(issue_hours, 1),
                }
            )

    anomalies.sort(key=lambda row: (-len(row["signals"]), -(row.get("hours") or 0), row["key"]))
    late_share = round(100 * late_hours / total_hours, 1) if total_hours else 0.0
    return anomalies[:14], {
        "issues_considered": considered,
        "worklog_hours": round(total_hours, 1),
        "last_two_workdays_hours": round(late_hours, 1),
        "last_two_workdays_share_pct": late_share,
    }


def _forecast_next_window(sprint: dict) -> tuple[date | None, date | None]:
    start = _as_day(sprint.get("start_date"))
    end = _as_day(sprint.get("end_date"))
    if not start or not end:
        return None, None
    next_start = end + timedelta(days=1)
    while next_start.weekday() >= 5:
        next_start += timedelta(days=1)
    return next_start, next_start + (end - start)


def _next_sprint_from_tasks(
    releases: list[dict], current_id: object, *, after: date
) -> tuple[date, date, str] | None:
    candidates: list[tuple[date, date, str]] = []
    for release in releases:
        for task in release.get("tasks") or []:
            for sprint in task.get("sprints") or []:
                if str(sprint.get("id")) == str(current_id):
                    continue
                start = _as_day(sprint.get("start_date"))
                end = _as_day(sprint.get("end_date"))
                if start and end:
                    named = sprint_name_window(sprint.get("name"))
                    if named and named.start and named.end:
                        start, end = named.start, named.end
                    if start <= after:
                        continue
                    candidates.append((start, end, str(sprint.get("name") or "Следующий спринт")))
    return min(candidates, key=lambda row: row[0]) if candidates else None


def _topics(description: object) -> list[str]:
    text = str(description or "").strip()
    if not text:
        return []
    parts = re.split(r"[+;,\n]|\s+[–—]\s+", text)
    out: list[str] = []
    for part in parts:
        value = re.sub(r"\s+", " ", part).strip(" .:;–—-")
        if len(value) >= 3 and value.lower() not in {"релиз", "задачи", "доработки"}:
            out.append(value[:100])
    return out[:10]


def _norm_words(value: object) -> tuple[str, set[str]]:
    words = re.findall(r"[a-zа-яё0-9]+", str(value or "").lower())
    useful = {word for word in words if len(word) >= 2}
    return " ".join(words), useful


def _match_topic(
    topic: str,
    tasks: list[dict],
    *,
    next_start: date | None,
    next_end: date | None,
) -> dict:
    topic_norm, topic_words = _norm_words(topic)
    ranked: list[tuple[float, dict]] = []
    for task in tasks:
        summary_norm, summary_words = _norm_words(task.get("summary"))
        overlap = len(topic_words & summary_words) / max(len(topic_words), 1)
        fuzzy = SequenceMatcher(None, topic_norm, summary_norm).ratio()
        prefix = 0.0
        if topic_words and summary_words:
            matches = sum(
                1
                for left in topic_words
                if any(left[:4] == right[:4] for right in summary_words)
            )
            prefix = matches / len(topic_words)
        ranked.append((max(overlap, fuzzy * 0.8, prefix * 0.9), task))
    ranked.sort(key=lambda row: (-row[0], str(row[1].get("key") or "")))
    score, best = ranked[0] if ranked else (0.0, {})
    planned = False
    for sprint in best.get("sprints") or []:
        sprint_start = _as_day(sprint.get("start_date"))
        if str(sprint.get("state") or "").lower() == "future":
            planned = True
            break
        if next_start and next_end and sprint_start and next_start <= sprint_start <= next_end:
            planned = True
            break
    return {
        "topic": topic,
        "match_state": "matched" if score >= 0.58 else "uncertain",
        "confidence": round(score, 2),
        "candidate_key": best.get("key"),
        "candidate_summary": best.get("summary"),
        "candidate_planned_in_future_sprint": planned,
    }


def build_sprint_review_snapshot(sprint_report: dict, *, now: datetime | None = None) -> dict:
    sprint = sprint_report.get("sprint") or {}
    start = _as_day(sprint.get("start_date"))
    end = _as_day(sprint.get("end_date"))
    if not start or not end:
        return {"sprint": {"id": sprint.get("id")}, "error": "no_sprint_dates"}

    releases = [r for r in (sprint_report.get("releases") or []) if isinstance(r, dict)]
    anomalies, worklog_stats = _issue_anomalies(sprint_report.get("issues") or {}, start, end)
    actual_next = _next_sprint_from_tasks(releases, sprint.get("id"), after=end)
    if actual_next:
        next_start, next_end, next_name = actual_next
        forecast = False
    else:
        next_start, next_end = _forecast_next_window(sprint)
        next_name = "Прогнозный следующий спринт"
        forecast = True

    planned_releases = []
    if next_start and next_end:
        for release in releases:
            release_day = _as_day(release.get("release_date"))
            if not release_day or not (next_start <= release_day <= next_end):
                continue
            tasks = [t for t in (release.get("tasks") or []) if isinstance(t, dict)]
            planned_releases.append(
                {
                    "id": release.get("id"),
                    "name": release.get("name"),
                    "date": release_day.isoformat(),
                    "description": release.get("description") or None,
                    "tasks_total": len(tasks),
                    "key_topics": [
                        _match_topic(
                            topic,
                            tasks,
                            next_start=next_start,
                            next_end=next_end,
                        )
                        for topic in _topics(release.get("description"))
                    ],
                }
            )

    in_sprint_releases = [r for r in releases if r.get("in_sprint") is True]
    return {
        "sprint": {
            "id": sprint.get("id"),
            "name": sprint.get("name"),
            "goal": sprint.get("goal"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "done": sprint.get("done"),
            "total": sprint.get("total"),
            "tasks_progress_pct": sprint.get("tasks_progress_pct"),
        },
        "history_analysis": {
            "anomalies": anomalies,
            "worklogs": worklog_stats,
        },
        "release_outcomes": [
            {
                "name": release.get("name"),
                "date": release.get("release_date"),
                "released": bool(release.get("released")),
                "done": release.get("tasks_done"),
                "total": release.get("tasks_total"),
                "active": release.get("tasks_active"),
                "risk": release.get("risk"),
                "description": release.get("description") or None,
            }
            for release in in_sprint_releases
        ],
        "next_planning": {
            "name": next_name,
            "start_date": next_start.isoformat() if next_start else None,
            "end_date": next_end.isoformat() if next_end else None,
            "forecast": forecast,
            "releases": planned_releases,
        },
    }


def _empty(model: str, reason: str, *, error: str | None = None) -> dict:
    return {
        "status": "error" if error else "skipped",
        "reason": reason,
        "generated_at": _iso_now(),
        "model": model,
        "model_label": short_model_name(model),
        "author": corporate_author(model),
        "prompt_version": PROMPT_VERSION,
        "markdown": None,
        "verdict": None,
        "snapshot_hash": None,
        "error": error,
    }


def generate_sprint_review(
    sprint_report: dict,
    *,
    previous: dict | None = None,
    mock: bool = False,
    now: datetime | None = None,
) -> dict:
    settings = _llm_settings()
    model = settings["model"]
    sprint = sprint_report.get("sprint") or {}
    if not sprint_review_available(sprint, now=now):
        return _empty(model, "too_early")
    snapshot = build_sprint_review_snapshot(sprint_report, now=now)
    snap_hash = _snapshot_hash(snapshot)
    if (
        isinstance(previous, dict)
        and previous.get("status") == "ok"
        and previous.get("prompt_version") == PROMPT_VERSION
        and previous.get("snapshot_hash") == snap_hash
        and previous.get("markdown")
    ):
        cached = dict(previous)
        cached["reason"] = "cache"
        return cached
    if mock:
        planned = (snapshot.get("next_planning") or {}).get("releases") or []
        suffix = (
            f" В следующем интервале запланировано релизов: {len(planned)}."
            if planned
            else " В прогнозном следующем интервале релизы пока не определены."
        )
        markdown = (
            "Спринт завершён; итоговая оценка построена по истории статусов и списаниям."
            + suffix
            + " На планировании стоит разобрать отмеченные возвраты и проверить ключевые темы релизов."
        )
        return {
            **_empty(model, "mock"),
            "status": "ok",
            "markdown": markdown,
            "verdict": markdown.split(".", 1)[0] + ".",
            "snapshot_hash": snap_hash,
            "error": None,
        }
    if not settings["enabled"]:
        return _empty(model, "disabled")
    if not settings["url"] or not settings["token"]:
        return _empty(model, "no_api_key")
    try:
        markdown, used_model = _call_litellm_chat(
            url=settings["url"],
            token=settings["token"],
            model=model,
            temperature=settings["temperature"],
            timeout=settings["timeout"],
            max_tokens=min(settings["max_tokens"], 1600),
            reasoning=settings.get("reasoning", False),
            system=SYSTEM_PROMPT,
            user=USER_PROMPT_PREFIX + json.dumps(snapshot, ensure_ascii=False, indent=2),
        )
        markdown = humanize_ai_text(markdown)
        return {
            "status": "ok",
            "reason": None,
            "generated_at": _iso_now(),
            "model": used_model,
            "model_label": short_model_name(used_model),
            "author": corporate_author(used_model),
            "prompt_version": PROMPT_VERSION,
            "markdown": markdown,
            "verdict": extract_verdict(markdown),
            "snapshot_hash": snap_hash,
            "error": None,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return _empty(model, "error", error=f"HTTP {exc.code}: {detail or exc.reason}")
    except Exception as exc:  # noqa: BLE001 - retrospective must not fail collect
        return _empty(model, "error", error=f"{type(exc).__name__}: {exc}")


def attach_ai_sprint_review(
    report: dict,
    *,
    previous_report: dict | None = None,
    mock: bool = False,
    now: datetime | None = None,
) -> dict:
    sprint_report = report.get("sprint_report") if isinstance(report, dict) else None
    if not isinstance(sprint_report, dict):
        return report
    previous = None
    if isinstance(previous_report, dict):
        previous_sr = previous_report.get("sprint_report") or {}
        current_id = (sprint_report.get("sprint") or {}).get("id")
        previous_id = (previous_sr.get("sprint") or {}).get("id")
        if current_id is not None and str(current_id) == str(previous_id):
            previous = previous_sr.get("ai_sprint_review")
    sprint_report["ai_sprint_review"] = generate_sprint_review(
        sprint_report,
        previous=previous if isinstance(previous, dict) else None,
        mock=mock,
        now=now,
    )
    report["sprint_report"] = sprint_report
    return report

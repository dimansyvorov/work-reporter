from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from .config import ROOT
from .team_config import get_team_config

PROMPT_VERSION = "sprint-brief-v6"

SYSTEM_PROMPT = """Ты — аналитик спринтовой разработки. Тебе дают только JSON-снимок спринта (уже агрегированные метрики команды).

Задача: дать краткую и содержательную оценку движения спринта, подсветить реальные риски и предложить конкретные рекомендации на ближайшие 1–2 дня.

Правила:
1. Опирайся только на факты из JSON. Не выдумывай задачи, людей, релизы, проценты и причины, которых нет в данных.
2. Если данных мало — так и скажи, не достраивай картину.
3. Сравнивай прогресс задач с календарным прохождением спринта.
4. Приоритет рисков: релизы at_risk/overdue → перегруз людей → отстающие направления → stale/no_estimate/no_release.
5. Отдельно оцени достижимость цели спринта (goal.text) по связанным релизам из goal.releases.
6. Если цель сформулирована как релиз — вердикт по цели важнее общего % задач спринта.
7. Различай срочные и несрочные задачи в нагрузке человека:
   - urgent / on_goal_release — важны для цели/ближайшего релиза;
   - carryover (перенос из прошлых спринтов, tag «Задержка») — часто раздувают % нагрузки,
     но не все из них критичны для текущей цели;
   - если у человека высокая нагрузка при большом carryover_share — явно скажи, что перегруз
     во многом из накопительного хвоста прошлых спринтов, а не только из задач цели.
8. Рекомендации адресуй лидам направлений из блока leads (или по полю lead=true).
   Формулируй мягко и по-деловому: «стоит…», «важно…», «имеет смысл…», «лучше…».
   Не используй грубые императивы вроде «должен», «обязан», «немедленно», «срочно сделайте».
9. Пиши по-русски, спокойным деловым тоном, без воды и эмодзи.
10. Объём: примерно 120–220 слов или эквивалент в коротких буллетах.
11. В ответе пользователю НЕ используй сырые ключи JSON и англ. жаргон полей
    (запрещено: tasks_pct, time_pct, progress_pct, load_pct, at_risk, on_track, stale,
    active_tasks, carryover_share, on_goal_release и т.п.).
    Пиши по-русски: «выполнение задач», «прохождение спринта», «прогресс релиза», «нагрузка»,
    «риск срыва», «в графике», «неактивные задачи», «активные задачи»,
    «перенос из прошлых спринтов», «задачи цели/релиза», «задачи без релиза».
    Статусы риска переводи: at_risk → «риск срыва», on_track → «в графике», overdue → «просрочен».
    no_release → «задачи без релиза» (нет Fix Version).
12. Имена людей — только короткая форма «Имя Ф.» как в данных short («Роман Щ.», «Наталья Ш.»).
13. Задачу/эпик упоминай ТОЛЬКО ключом в бэктиках (`SPRINT-11708`).
    UI сам подставит название и сделает ссылку.
    ЗАПРЕЩЕНО писать название после ключа, в скобках или повторять summary.
    НЕ выделяй ключи задач жирным (`**…**`).

Формат ответа строго такой:

## Вердикт
1–2 предложения: в графике / отстаём / на грани.
Сравни «выполнение задач» и «прохождение спринта» обычным языком, например:
«задачи закрыты на 27%, а по календарю прошло 23%». Без имён полей JSON.

## Цель спринта
1. Цитата/пересказ цели.
2. Статус: достижима / под угрозой / маловероятна.
3. 2–4 факта по связанным релизам (progress/time/active/risk).
4. Что критичнее всего для выполнения цели.

## Сигналы
3–5 буллетов по фактам (прогресс, направления, ёмкость, релизы, срочность vs перенос).

## Риски
Только подтверждённые данными. Для каждого: что именно, почему это риск, на что влияет.
Если явных рисков нет — напиши «Явных критичных рисков в снимке нет».

## Рекомендации
2–4 конкретных действия на 1–2 дня. Каждое действие привяжи к риску/сигналу из данных.
Минимум одна рекомендация должна бить в цель спринта.
Адресуй действия лидам направлений мягкими формулировками
(пример: «Лидеру бэкенда стоит уже сегодня выделить…», «Важно перенести часть задач от …»).
"""

USER_PROMPT_PREFIX = """Проанализируй спринт по JSON-снимку ниже.

Особое внимание:
- разница между выполнением задач и прохождением спринта по календарю;
- цель спринта и связанные релизы;
- релизы в зоне риска;
- факторы настроения команды;
- задачи без релиза (risk_counts.no_release) — если их много, это сигнал;
- перегруз/недогруз людей с учётом срочных задач vs переноса из прошлых спринтов;
- кого из лидов (leads) стоит адресовать в рекомендациях;
- отстающие направления.

Отдельно оцени достижение цели спринта.
Если цель — релиз, сначала оцени готовность этих релизов, затем общий ход спринта.
В тексте ответа не используй имена полей JSON — только понятный русский.
Тон рекомендаций — мягкий и уважительный, без приказов.
Ключи задач — только в бэктиках без названий рядом; люди — «Имя Ф.» («Роман Щ.»).

JSON:
"""


def short_model_name(model: str | None) -> str:
    text = (model or "").strip()
    if not text:
        return "unknown"
    text = re.sub(r"^/+models/+", "", text, flags=re.I)
    text = text.split("/")[-1].strip() or text
    return text or "unknown"


def corporate_author(model: str | None) -> str:
    return f"corporate {short_model_name(model)}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_hash(snapshot: dict) -> str:
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _goal_token(goal: str) -> str | None:
    text = (goal or "").strip()
    if not text:
        return None
    # Prefer date-like tokens: 10.08, 10.08.26
    m = re.search(r"\b(\d{1,2}\.\d{2}(?:\.\d{2,4})?)\b", text)
    if m:
        return m.group(1)
    # Fallback: last meaningful word
    parts = [p for p in re.split(r"\s+", text) if len(p) >= 3]
    return parts[-1] if parts else text


def _release_row(rel: dict) -> dict:
    return {
        "name": rel.get("name"),
        "date": rel.get("release_date"),
        "released": bool(rel.get("released")),
        "progress_pct": rel.get("progress_pct"),
        "time_pct": rel.get("time_pct"),
        "done": rel.get("tasks_done") if rel.get("tasks_done") is not None else rel.get("done"),
        "active": rel.get("tasks_active")
        if rel.get("tasks_active") is not None
        else rel.get("active_tasks"),
        "total": rel.get("tasks_total") if rel.get("tasks_total") is not None else rel.get("total"),
        "risk": rel.get("risk"),
        "label": rel.get("risk_label") or rel.get("label"),
        "days_left": rel.get("days_left"),
    }


def _short_person_name(name: str | None) -> str | None:
    """«Роман Щ.» from «Щукин Роман …» (given name + surname initial)."""
    if not name:
        return None
    parts = str(name).strip().split()
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return f"{parts[1]} {parts[0][0]}."


def _tag_ids(task: dict) -> set[str]:
    out: set[str] = set()
    for tag in task.get("tags") or []:
        if isinstance(tag, dict) and tag.get("id"):
            out.add(str(tag.get("id")))
    return out


def _release_labels(task: dict) -> list[str]:
    labels: list[str] = []
    for tag in task.get("tags") or []:
        if isinstance(tag, dict) and tag.get("id") == "release":
            label = str(tag.get("label") or "").strip()
            if label:
                labels.append(label)
    return labels


def _is_hidden_task(task: dict | None, issues: dict | None = None) -> bool:
    """True for auxiliary tasks matching display_task_filters — skip in AI snapshots."""
    if not isinstance(task, dict):
        return False
    if task.get("hidden_from_display"):
        return True
    summary = task.get("summary")
    key = str(task.get("key") or "").strip().upper()
    if issues and key:
        dossier = issues.get(key)
        if isinstance(dossier, dict):
            if dossier.get("hidden_from_display"):
                return True
            summary = summary or dossier.get("summary")
    return get_team_config().is_hidden_from_display(
        str(summary) if summary is not None else None
    )


def _task_sample(task: dict, *, urgency: str) -> dict:
    rem = task.get("remaining_hours")
    try:
        rem_f = float(rem) if rem is not None else None
    except (TypeError, ValueError):
        rem_f = None
    return {
        "key": task.get("key"),
        "status": task.get("status"),
        "remaining_hours": rem_f,
        "estimate_hours": task.get("estimate_hours"),
        "releases": _release_labels(task)[:2],
        "urgency": urgency,
        "summary": (str(task.get("summary") or "")[:80] or None),
    }


def _person_task_pressure(profile: dict | None, goal_token: str | None) -> dict | None:
    """Summarize urgent vs carryover pressure for one person."""
    if not isinstance(profile, dict):
        return None
    raw_active = [
        t for t in (profile.get("tasks_active") or []) if isinstance(t, dict)
    ]
    active = [t for t in raw_active if not _is_hidden_task(t)]
    if not active:
        load = profile.get("load") or {}
        # Only-auxiliary backlog must not inflate AI pressure via load counters.
        only_hidden = bool(raw_active)
        return {
            "active_tasks": 0 if only_hidden else int(load.get("active_tasks") or 0),
            "carryover_tasks": 0,
            "carryover_not_goal": 0,
            "urgent_tasks": 0,
            "on_goal_release": 0,
            "no_release_tasks": 0,
            "with_remaining": 0
            if only_hidden
            else int(load.get("tasks_with_remaining") or 0),
            "carryover_share": 0.0,
            "note": None,
            "urgent_samples": [],
            "carryover_samples": [],
        }

    carryover = 0
    carryover_not_goal = 0
    urgent = 0
    on_goal = 0
    with_remaining = 0
    no_release = 0
    urgent_samples: list[dict] = []
    carryover_samples: list[dict] = []

    for task in active:
        tags = _tag_ids(task)
        is_carryover = "delay" in tags
        releases = _release_labels(task)
        is_goal = bool(goal_token) and any(goal_token in label for label in releases)
        try:
            rem = float(task.get("remaining_hours") or 0)
        except (TypeError, ValueError):
            rem = 0.0
        if rem > 0:
            with_remaining += 1
        if "no_release" in tags or not releases:
            no_release += 1
        # Do NOT treat issue.risk as urgency: delay/carryover tasks are often risk=true.
        is_urgent = bool(is_goal or rem > 0)
        if is_carryover:
            carryover += 1
            if not is_goal:
                carryover_not_goal += 1
        if is_goal:
            on_goal += 1
        if is_urgent:
            urgent += 1
            if len(urgent_samples) < 4:
                kind = "goal" if is_goal else "remaining"
                urgent_samples.append(_task_sample(task, urgency=kind))
        if is_carryover and not is_goal and len(carryover_samples) < 4:
            carryover_samples.append(_task_sample(task, urgency="carryover"))

    total = len(active)
    share = round(100.0 * carryover / total, 1) if total else 0.0
    note = None
    if share >= 40:
        note = (
            "Большая доля активных задач — перенос из прошлых спринтов (метка «Задержка»). "
            "Высокая нагрузка может быть накопительной, а не только из задач текущей цели."
        )
    elif on_goal and on_goal < max(total // 3, 1) and carryover:
        note = (
            "Часть нагрузки — хвост прошлых спринтов; задачи цели/ближайшего релиза — меньшая доля."
        )
    elif no_release and no_release >= max(2, total // 2):
        note = (
            "Заметная доля активных задач без Fix Version (метка «Без релиза») — "
            "сложнее связать работу с целью и релизами."
        )

    return {
        "active_tasks": total,
        "carryover_tasks": carryover,
        "carryover_not_goal": carryover_not_goal,
        "urgent_tasks": urgent,
        "on_goal_release": on_goal,
        "no_release_tasks": no_release,
        "with_remaining": with_remaining,
        "carryover_share": share,
        "note": note,
        "urgent_samples": urgent_samples,
        "carryover_samples": carryover_samples,
    }


def build_ai_snapshot(sprint_report: dict) -> dict:
    sprint = sprint_report.get("sprint") or {}
    mood = sprint_report.get("team_mood") or {}
    risks = sprint_report.get("risks") or {}
    releases = sprint_report.get("releases") or []
    directions = sprint_report.get("directions") or []
    team = sprint_report.get("team") or []

    goal_text = (sprint.get("goal") or "").strip()
    token = _goal_token(goal_text)
    goal_releases = []
    if token:
        for rel in releases:
            name = str(rel.get("name") or "")
            if token in name:
                goal_releases.append(_release_row(rel))

    risky_releases = [
        _release_row(rel)
        for rel in releases
        if (rel.get("risk") in {"at_risk", "overdue", "slip"} or rel.get("released") is False)
        and (
            rel.get("risk") in {"at_risk", "overdue", "slip"}
            or float(rel.get("progress_pct") or 0) + 12 < float(rel.get("time_pct") or 0)
        )
    ]
    # Keep compact: prefer explicit risk, max 8
    risky_releases = sorted(
        risky_releases,
        key=lambda r: (
            0 if r.get("risk") in {"at_risk", "overdue"} else 1,
            float(r.get("progress_pct") or 0),
        ),
    )[:8]

    dir_rows = []
    for d in directions:
        done = int(d.get("tasks_done") or 0)
        total = int(d.get("tasks_total") or 0)
        dir_rows.append(
            {
                "name": d.get("name"),
                "progress_pct": d.get("tasks_progress_pct"),
                "done": done,
                "open": max(total - done, 0),
                "people": d.get("people_count"),
            }
        )

    people = sprint_report.get("people") or {}
    capacity_rows = []
    for person in team:
        load = person.get("load") or {}
        if load.get("load_pct") is None and load.get("level") in (None, "unknown"):
            continue
        name = person.get("name")
        profile = people.get(name) if isinstance(people, dict) else None
        pressure = _person_task_pressure(profile, token)
        row = {
            "name": name,
            "short": _short_person_name(name),
            "direction": person.get("direction"),
            "lead": bool(person.get("lead")),
            "load_pct": load.get("load_pct"),
            "level": load.get("level"),
            "hours_sprint": person.get("hours_sprint"),
            "tasks_open": person.get("tasks_open"),
            "remaining_hours": load.get("remaining_hours"),
            "capacity_hours": load.get("capacity_hours"),
        }
        if pressure:
            row["task_pressure"] = {
                "active_tasks": pressure["active_tasks"],
                "carryover_tasks": pressure["carryover_tasks"],
                "carryover_not_goal": pressure["carryover_not_goal"],
                "urgent_tasks": pressure["urgent_tasks"],
                "on_goal_release": pressure["on_goal_release"],
                "no_release_tasks": pressure["no_release_tasks"],
                "with_remaining": pressure["with_remaining"],
                "carryover_share": pressure["carryover_share"],
                "note": pressure["note"],
            }
            # Keep samples only for overloaded people to limit token size
            if load.get("level") in {"over", "tight"}:
                row["urgent_samples"] = pressure["urgent_samples"]
                row["carryover_samples"] = pressure["carryover_samples"]
        capacity_rows.append(row)
    overloaded = sorted(
        [r for r in capacity_rows if r.get("level") in {"over", "tight"}],
        key=lambda r: -(float(r.get("load_pct") or 0)),
    )[:5]
    underloaded = sorted(
        [
            r
            for r in capacity_rows
            if r.get("level") in {"empty", "ok"} and float(r.get("load_pct") or 0) < 40
        ],
        key=lambda r: (float(r.get("load_pct") or 0), str(r.get("name") or "")),
    )[:3]

    leads = []
    for person in team:
        if not person.get("lead"):
            continue
        leads.append(
            {
                "name": person.get("name"),
                "short": _short_person_name(person.get("name")),
                "direction": person.get("direction"),
            }
        )
    leads.sort(key=lambda r: (str(r.get("direction") or ""), str(r.get("name") or "")))

    def _keys(items: list | None, limit: int = 8) -> list[str]:
        out = []
        for item in items or []:
            if isinstance(item, dict):
                key = item.get("key") or item.get("issue_key")
            else:
                key = item
            if key:
                out.append(str(key))
            if len(out) >= limit:
                break
        return out

    target_date = None
    days_left_to_goal = None
    if goal_releases:
        dates = [r.get("date") for r in goal_releases if r.get("date")]
        if dates:
            target_date = sorted(dates)[0]
            days = [r.get("days_left") for r in goal_releases if r.get("days_left") is not None]
            if days:
                days_left_to_goal = min(int(d) for d in days)

    drivers = []
    for d in (mood.get("drivers") or [])[:5]:
        drivers.append(
            {
                "id": d.get("id"),
                "title": d.get("title"),
                "summary": d.get("summary"),
                "severity": d.get("severity"),
                "impact": d.get("impact"),
            }
        )

    return {
        "sprint": {
            "name": sprint.get("name"),
            "start_date": sprint.get("start_date"),
            "end_date": sprint.get("end_date"),
            "day_index": sprint.get("day_index"),
            "day_total": sprint.get("day_total"),
            "days_left": sprint.get("days_left"),
            "tasks_pct": sprint.get("tasks_progress_pct"),
            "time_pct": sprint.get("time_progress_pct"),
            "done": sprint.get("done"),
            "open": sprint.get("open"),
            "total": sprint.get("total"),
        },
        "goal": {
            "text": goal_text or None,
            "target_date": target_date,
            "days_left_to_goal": days_left_to_goal,
            "releases": goal_releases,
        },
        "mood": {
            "score": mood.get("score"),
            "tone": mood.get("tone"),
            "recommendation": mood.get("recommendation"),
            "drivers": drivers,
        },
        "directions": dir_rows,
        "risky_releases": risky_releases,
        "risk_counts": {
            "at_risk": len(risks.get("at_risk") or []),
            "stale": len(risks.get("stale") or []),
            "no_worklogs": len(risks.get("no_worklogs") or []),
            "no_estimate": len(risks.get("no_estimate") or []),
            "no_release": len(risks.get("no_release") or []),
            "stale_days": risks.get("stale_days"),
        },
        "risk_examples": {
            "stale": _keys(risks.get("stale")),
            "no_estimate": _keys(risks.get("no_estimate")),
            "no_release": _keys(risks.get("no_release")),
            "at_risk": _keys(risks.get("at_risk")),
        },
        "leads": leads,
        "capacity": {
            "overloaded": overloaded,
            "underloaded": underloaded,
            "legend": {
                "carryover_tasks": "задачи с меткой «Задержка» — были в прошлых спринтах и перенесены",
                "carryover_not_goal": "перенос, не привязанный к релизам текущей цели",
                "urgent_tasks": "задачи цели/ближайшего релиза или с оставшимися часами",
                "on_goal_release": "задачи, привязанные к релизам цели спринта",
                "no_release_tasks": "активные задачи без Fix Version (метка «Без релиза»)",
            },
        },
    }


def humanize_ai_text(text: str | None) -> str:
    """Best-effort cleanup of model jargon for UI (does not replace a good prompt)."""
    if not text:
        return ""
    out = str(text)
    replacements = (
        (r"\btasks_pct\b", "выполнение задач"),
        (r"\btime_pct\b", "прохождение спринта"),
        (r"\bprogress_pct\b", "прогресс"),
        (r"\bload_pct\b", "нагрузка"),
        (r"\bdays_left\b", "дней осталось"),
        (r"\bactive_tasks\b", "активных задач"),
        (r"\bstale-задач", "неактивных задач"),
        (r"\bstale\b", "неактивные"),
        (r"`?at_risk`?", "риск срыва"),
        (r"`?on_track`?", "в графике"),
        (r"`?overdue`?", "просрочен"),
        (r"\brisky_releases\b", "релизы в зоне риска"),
        (r"\bno_release\b", "задачи без релиза"),
        (r"\bno_estimate\b", "задачи без оценки"),
        # People are not "resources"
        (r"\bчеловеческ(?:ие|их|ими)?\s+ресурсы?\b", "сотрудники"),
        (r"\bресурсы\s+команды\b", "сотрудники команды"),
        (r"\bнехватка\s+ресурсов\b", "нехватка людей"),
        (r"\bнехватк[аиуеой]*\s+ресурса\b", "нехватка людей"),
        (r"\bсвободн(?:ый|ые|ых|ого)\s+ресурсы?\b", "свободные сотрудники"),
        (r"\bперегруз(?:ка|ке|ки)?\s+ресурсов\b", "перегруз сотрудников"),
        (r"(?<![А-Яа-яA-Za-z])ресурсы(?![А-Яа-яA-Za-z])", "сотрудники"),
        (r"(?<![А-Яа-яA-Za-z])ресурс(?![А-Яа-яA-Za-z])", "сотрудник"),
    )
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.I)
    # Soften leftover "field 12% > field 34%" style if model still emits English keys
    out = re.sub(
        r"\(\s*выполнение задач\s+([0-9]+(?:[.,][0-9]+)?)\s*%\s*>\s*прохождение спринта\s+([0-9]+(?:[.,][0-9]+)?)\s*%\s*\)",
        r"(задачи закрыты на \1%, по календарю прошло \2%)",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\(\s*выполнение задач\s+([0-9]+(?:[.,][0-9]+)?)\s*%\s*<\s*прохождение спринта\s+([0-9]+(?:[.,][0-9]+)?)\s*%\s*\)",
        r"(задачи закрыты на \1%, по календарю прошло \2%)",
        out,
        flags=re.I,
    )
    return out


def extract_verdict(markdown: str | None) -> str | None:
    if not markdown:
        return None
    text = str(markdown).strip()
    if not text:
        return None
    match = re.search(
        r"##\s*Вердикт\s*\n+(.+?)(?=\n##\s|\Z)",
        text,
        flags=re.I | re.S,
    )
    chunk = (match.group(1) if match else text).strip()
    lines = [ln.strip(" -*\t") for ln in chunk.splitlines() if ln.strip()]
    if not lines:
        return None
    verdict = humanize_ai_text(" ".join(lines[:3]).strip())
    if len(verdict) > 220:
        verdict = verdict[:217].rstrip() + "…"
    return verdict or None


def _llm_settings() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    enabled_raw = (os.getenv("CORP_LLM_ENABLED") or "1").strip().lower()
    enabled = enabled_raw not in {"0", "false", "no", "off"}
    return {
        "enabled": enabled,
        "url": (os.getenv("CORP_LLM_URL") or "").strip(),
        "token": (os.getenv("CORP_LLM_TOKEN") or "").strip(),
        "model": (os.getenv("CORP_LLM_MODEL") or "/models/Qwen3.6").strip(),
        "temperature": float(os.getenv("CORP_LLM_TEMPERATURE") or "0.2"),
        "timeout": int(float(os.getenv("CORP_LLM_TIMEOUT_SEC") or "45")),
        "cache_sec": int(float(os.getenv("CORP_LLM_CACHE_SEC") or "3000")),
    }


def _call_litellm_chat(
    *,
    url: str,
    token: str,
    model: str,
    temperature: float,
    timeout: int,
    system: str,
    user: str,
) -> tuple[str, str]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not content or not str(content).strip():
        raise RuntimeError("empty model content")
    used_model = data.get("model") or model
    return str(content).strip(), str(used_model)


def _call_litellm(*, url: str, token: str, model: str, temperature: float, timeout: int, snapshot: dict) -> tuple[str, str]:
    return _call_litellm_chat(
        url=url,
        token=token,
        model=model,
        temperature=temperature,
        timeout=timeout,
        system=SYSTEM_PROMPT,
        user=USER_PROMPT_PREFIX + json.dumps(snapshot, ensure_ascii=False, indent=2),
    )


def generate_ai_brief(
    sprint_report: dict | None,
    *,
    previous: dict | None = None,
    mock: bool = False,
) -> dict:
    """
    Build/attach AI sprint brief. Never raises to caller for API failures.
    """
    settings = _llm_settings()
    model = settings["model"]
    base = {
        "status": "skipped",
        "generated_at": _iso_now(),
        "model": model,
        "model_label": short_model_name(model),
        "author": corporate_author(model),
        "prompt_version": PROMPT_VERSION,
        "snapshot_hash": None,
        "verdict": None,
        "markdown": None,
        "error": None,
        "reason": None,
    }

    if mock:
        base["reason"] = "mock"
        return base
    if not settings["enabled"]:
        base["reason"] = "disabled"
        return base
    if not sprint_report:
        base["reason"] = "no_sprint_report"
        return base
    if not settings["url"] or not settings["token"]:
        base["reason"] = "no_api_key"
        return base

    snapshot = build_ai_snapshot(sprint_report)
    snap_hash = _snapshot_hash(snapshot)
    base["snapshot_hash"] = snap_hash

    prev = previous if isinstance(previous, dict) else None
    if (
        prev
        and prev.get("status") == "ok"
        and prev.get("snapshot_hash") == snap_hash
        and prev.get("markdown")
        and prev.get("prompt_version") == PROMPT_VERSION
    ):
        # Reuse fresh enough previous brief for unchanged snapshot
        try:
            prev_at = datetime.fromisoformat(
                str(prev.get("generated_at") or "").replace("Z", "+00:00")
            )
            age = (datetime.now(timezone.utc) - prev_at.astimezone(timezone.utc)).total_seconds()
        except ValueError:
            age = None
        if age is not None and age < settings["cache_sec"]:
            reused = dict(prev)
            reused["status"] = "ok"
            reused["reason"] = "cache"
            reused["author"] = corporate_author(reused.get("model") or model)
            reused["model_label"] = short_model_name(reused.get("model") or model)
            if not reused.get("verdict"):
                reused["verdict"] = extract_verdict(reused.get("markdown"))
            return reused

    try:
        markdown, used_model = _call_litellm(
            url=settings["url"],
            token=settings["token"],
            model=model,
            temperature=settings["temperature"],
            timeout=settings["timeout"],
            snapshot=snapshot,
        )
        markdown = humanize_ai_text(markdown)
        return {
            "status": "ok",
            "generated_at": _iso_now(),
            "model": used_model,
            "model_label": short_model_name(used_model),
            "author": corporate_author(used_model),
            "prompt_version": PROMPT_VERSION,
            "snapshot_hash": snap_hash,
            "verdict": extract_verdict(markdown),
            "markdown": markdown,
            "error": None,
            "reason": None,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        base["status"] = "error"
        base["error"] = f"HTTP {exc.code}: {detail or exc.reason}"
        return base
    except Exception as exc:  # noqa: BLE001 - brief must not fail collect
        base["status"] = "error"
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base


def attach_ai_brief(report: dict, *, previous_report: dict | None = None, mock: bool = False) -> dict:
    if not isinstance(report, dict):
        return report
    sr = report.get("sprint_report")
    if not isinstance(sr, dict):
        return report
    prev_brief = None
    if isinstance(previous_report, dict):
        prev_sr = previous_report.get("sprint_report") or {}
        if isinstance(prev_sr, dict):
            prev_brief = prev_sr.get("ai_brief")
    sr["ai_brief"] = generate_ai_brief(sr, previous=prev_brief, mock=mock)
    report["sprint_report"] = sr
    return report

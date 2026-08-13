from __future__ import annotations

import json
import re
import urllib.error
from datetime import date, datetime, timezone
from typing import Any

from .activity import REPORT_TZ, report_today
from .ai_brief import (
    _call_litellm_chat,
    _goal_token,
    _iso_now,
    _is_hidden_task,
    _llm_settings,
    _person_task_pressure,
    _release_labels,
    _short_person_name,
    _snapshot_hash,
    corporate_author,
    extract_verdict,
    humanize_ai_text,
    short_model_name,
)
from .team_config import get_team_config

NOTES_PROMPT_VERSION = "person-notes-v10"
# Workday ends 18:00 MSK; mute "no hours today" while >2h remain → before 16:00.
TODAY_WORKLOG_JUDGE_AFTER_HOUR = 16

SYSTEM_PROMPT = """Ты пишешь короткие живые заметки по сотрудникам спринта — как коллега-аналитик, а не как HR-отчёт.

Тебе дают:
1) сжатый итог уже готовой AI-оценки спринта (brief_context);
2) компактные строки по людям (people).

Правила:
1. НЕ переоценивай спринт заново. Опирайся на brief_context (вердикт, риски, рекомендации, цель).
2. Заметка только про конкретного человека и его данные в people.
3. Пиши СТРОГО от третьего лица — как заметка О человеке для руководителя/команды,
   а не обращение К нему.
   Правильно: «у него», «у неё», «его нагрузка», «ей стоит», «держит ритм», «фокус на задаче».
   Запрещено: «у вас», «вам», «вы», «держите», «следите», «ваш/ваша», обращения на «ты/вы».
   Согласуй род по gender: m → он/его/нему/него; f → она/её/ней/неё.
   Если gender нет — нейтральные формулировки без «он/она» («нагрузка высокая», «фокус на…»).
4. 3–5 предложения, по-русски, в свободном мягком тоне («стоит», «важно», «имеет смысл», «похоже»).
   Не «отчитывай» сотрудника. Можно чуть теплее обычного делового стиля.
   В text НЕ называй человека по имени/фамилии — карточка уже про него.
5. Без грубых императивов («должен», «немедленно», «обязан»).
6. Запрещены обесценивающие слова про людей: «ресурс», «юнит», «голова», «FTE»,
   «простаивает как ресурс», «свободный ресурс». Пиши про человека/коллегу/ёмкость задач.
7. Не используй сырые ключи JSON (load_pct, carryover_share, on_goal_release, hours_level и т.п.).
8. Учитывай роль и направление:
   - role=lead — мягко про координацию направления / приоритеты команды;
   - role=member — про личную нагрузку и фокус;
   - direction — направления сотрудника.
9. Смотри не только на нагрузку. По возможности затронь 1–2 сигнала из:
   - worklog.sprint — ГЛАВНЫЙ критерий списаний (ритм за прошедшие рабочие дни спринта);
   - worklog.today — только если today.judge=true; если today.too_early=true,
     НЕ пиши, что человек «сегодня не списывает» / «мало списаний сегодня»
     если до конца рабочего дня 18:00 по МСК осталось более 2 часов;
   - ratings (стабильность списаний, закрытие задач, эффективность, недо/переоценка);
   - task_pressure (срочные задачи цели vs перенос из прошлых спринтов;
     no_release_tasks — активные задачи без Fix Version / «Без релиза»);
   - risk_tasks — персональные риски из панелей спринта:
     at_risk («могут не закрыться до конца спринта»),
     no_estimate («без оценки»),
     no_release («без релиза»).
     Если risk_tasks.at_risk / no_estimate / no_release не пусты — обязательно упомяни
     1–3 ключа задач (SPRINT-123) в тексте;
   - focus_tasks — срочные задачи цели спринта / ближайшего релиза.
     Если focus_tasks не пуст, обязательно мягко подсвети 1–3 ключа задач (SPRINT-123)
     в тексте заметки.
   Если задач нет — не своди всё к «свободен»; мягко отметь паузу по задачам и списаниям
   за спринт, и что можно подхватить работу направления/цели, если это уместно,
   можешь сделать шутку про отпуск.
10. Если высокий перенос из прошлых спринтов — скажи об этом явно.
11. Если у человека есть задачи без релиза (task_pressure.no_release_tasks или risk_tasks.no_release) —
    мягко отметь, что стоит привязать работу к Fix Version / релизу.
12. Форматирование в text (лёгкий markdown, без HTML и без ```fence```):
   - задачу/эпик упоминай ТОЛЬКО ключом в бэктиках: `SPRINT-123`;
     UI сам подставит «KEY | название» и сделает ссылку.
     ЗАПРЕЩЕНО писать название после ключа, в скобках или повторять summary из JSON;
   - НЕ выделяй ключи задач жирным (`**…**`) — обычный текст;
   - *курсив* допустим редко; **жирный** — только для коротких акцентов не про ключи задач;
   - не используй заголовки ## и длинные списки — заметка короткая.
13. tone: attention | ok | info
   - attention: перегруз / риск / сильный хвост переноса / заметный провал списаний /
     задачи под угрозой срока или без оценки;
   - ok: спокойная картина;
   - info: нейтральный нюанс без тревоги.
14. focus — массив из: carryover, overload, underload, goal, stale, worklog, ratings,
    no_release, at_risk, no_estimate (только релевантные).
15. Верни СТРОГО JSON без markdown-ограждений вокруг всего ответа:
{"notes":[{"name":"Имя Ф. или полное ФИО","text":"...","tone":"attention","focus":["carryover"]}]}
16. Нужна заметка на КАЖДОГО человека из people (по short или name; short вида «Роман Щ.»).
"""

USER_PROMPT_PREFIX = """Сформируй мини-заметки по всем людям из JSON ниже.
Учитывай brief_context, не повторяй полный анализ спринта.
Пиши от третьего лица (у него / у неё / его / её) по gender человека — не обращайся на «вы/вам».
В text не называй человека по имени — контекст карточки уже известен.
По списаниям опирайся на worklog.sprint (весь спринт), а не на утренний сегодняшний ноль.
Если worklog.today.too_early=true — не делай вывод «сегодня не списывает».
Если у человека есть focus_tasks или risk_tasks — упомяни только ключи в бэктиках
(`SPRINT-12345`), без названий задач рядом: UI добавит «KEY | название» сам.
Если task_pressure.no_release_tasks заметно > 0 — мягко отметь, что стоит привязать задачи к релизу.
Не делай ключи задач жирными.

JSON:
"""


def _extract_section(markdown: str | None, title: str) -> str | None:
    if not markdown:
        return None
    match = re.search(
        rf"##\s*{re.escape(title)}\s*\n+(.+?)(?=\n##\s|\Z)",
        str(markdown),
        flags=re.I | re.S,
    )
    if not match:
        return None
    chunk = match.group(1).strip()
    # Keep compact: first ~8 non-empty lines
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    if not lines:
        return None
    text = "\n".join(lines[:8]).strip()
    return humanize_ai_text(text) if text else None


def _parse_model_json(content: str) -> dict:
    text = (content or "").strip()
    if not text:
        raise RuntimeError("empty notes json")
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, flags=re.I)
    if fence:
        text = fence.group(1).strip()
    # Sometimes model prepends commentary — take outermost object
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("notes json is not an object")
    return data


def build_brief_context(brief: dict, sprint_report: dict) -> dict:
    sprint = sprint_report.get("sprint") or {}
    md = brief.get("markdown")
    leads = []
    for person in sprint_report.get("team") or []:
        if not person.get("lead"):
            continue
        leads.append(
            {
                "name": person.get("name"),
                "short": _short_person_name(person.get("name")),
                "direction": person.get("direction"),
            }
        )
    return {
        "verdict": brief.get("verdict") or extract_verdict(md),
        "risks": _extract_section(md, "Риски"),
        "recommendations": _extract_section(md, "Рекомендации"),
        "goal": {
            "text": (sprint.get("goal") or "").strip() or None,
            "days_left": sprint.get("days_left"),
        },
        "leads": leads,
        "source_brief_hash": brief.get("snapshot_hash"),
    }


_RATING_SIGNAL_IDS = (
    "stability",
    "efficiency",
    "closer",
    "underestimator",
    "overestimator",
    "truant",
)


def _index_rating_rows(ratings: list | None) -> dict[str, list[dict]]:
    """name -> compact rating signals from sprint rating boards."""
    by_name: dict[str, list[dict]] = {}
    for cat in ratings or []:
        if not isinstance(cat, dict):
            continue
        cid = str(cat.get("id") or "")
        if cid not in _RATING_SIGNAL_IDS:
            continue
        pool = cat.get("all_people") or cat.get("people") or []
        for entry in pool:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            try:
                score = float(entry.get("score")) if entry.get("score") is not None else None
            except (TypeError, ValueError):
                score = None
            place = entry.get("place")
            try:
                place_i = int(place) if place is not None else None
            except (TypeError, ValueError):
                place_i = None
            by_name.setdefault(name, []).append(
                {
                    "id": cid,
                    "title": cat.get("title"),
                    "place": place_i,
                    "value": entry.get("value"),
                    "score": round(score, 1) if score is not None else None,
                }
            )
    # Keep at most 5 signals per person, prefer better places / known ids order
    order = {cid: i for i, cid in enumerate(_RATING_SIGNAL_IDS)}
    for name, items in by_name.items():
        items.sort(
            key=lambda x: (
                order.get(str(x.get("id")), 99),
                x.get("place") if x.get("place") is not None else 999,
            )
        )
        by_name[name] = items[:5]
    return by_name


def soft_person_note_text(text: str | None) -> str:
    """Remove demeaning phrasing that models sometimes emit."""
    out = humanize_ai_text(text)
    if not out:
        return ""
    # Phrase-level first, then leftover tokens.
    replacements = (
        (
            r"\b[Рр]есурс свободен для новых задач\b",
            "сейчас можно подхватить новые задачи",
        ),
        (
            r"\b[Сс]вободн(?:ый|ая|ое)\s+[Рр]есурс(?:ы|а|ом|у)?\b",
            "есть свободная ёмкость по задачам",
        ),
        (
            r"\b[Рр]есурс(?:ы|а|ом|у)?\s+свободен(?:а|ы|о)?\b",
            "сейчас можно подхватить новые задачи",
        ),
        (r"\b[Зз]агрузить\s+[Рр]есурс(?:ы|а|ом|у)?\b", "дать задачи коллеге"),
        (r"\b[Рр]есурс(?:ы|а|ом|у)?\b", "коллега"),
        (r"\b[Зз]агрузить\s+коллег[ауеи]?\b", "дать задачи коллеге"),
        (r"\b[Юю]нит(?:ы|а|ом|у)?\b", "человек"),
        (r"\bFTE\b", "занятость"),
        (r"\bпростаивает\b", "пока без активных задач"),
    )
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out)
    # Keep markdown-ish notes readable: don't flatten newlines / backticks.
    if "\n" in out or re.search(r"[`*_]", out):
        out = re.sub(r"[^\S\n]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    # Capitalize sentence starts after replacements
    chunks = re.split(r"(?<=[.!?])\s+", out)
    out = " ".join(
        (chunk[:1].upper() + chunk[1:]) if chunk else chunk for chunk in chunks
    )
    return out


def _person_risk_tasks(
    sprint_report: dict,
    person_name: str,
    *,
    limit: int = 4,
) -> dict[str, list[dict]]:
    """Pick this person's tasks from sprint risk panels (at_risk / no_estimate / no_release)."""
    risks = sprint_report.get("risks") or {}
    if not isinstance(risks, dict):
        return {}
    cfg = get_team_config()
    canonical = cfg.canonical_name(person_name) or person_name
    aliases = {
        str(person_name or "").strip().lower(),
        str(canonical or "").strip().lower(),
    }
    short = _short_person_name(person_name)
    if short:
        aliases.add(short.lower())
    aliases.discard("")

    def _match(item: dict) -> bool:
        for field in ("assignee_canonical", "assignee"):
            raw = str(item.get(field) or "").strip()
            if not raw:
                continue
            if raw.lower() in aliases:
                return True
            other = cfg.canonical_name(raw)
            if other and other.lower() in aliases:
                return True
        return False

    def _samples(items: object) -> list[dict]:
        out: list[dict] = []
        if not isinstance(items, list):
            return out
        for item in items:
            if not isinstance(item, dict) or not _match(item):
                continue
            key = str(item.get("key") or "").strip().upper()
            if not key:
                continue
            out.append(
                {
                    "key": key,
                    "summary": (str(item.get("summary") or "")[:80] or None),
                    "status": item.get("status"),
                    "reason": item.get("reason"),
                }
            )
            if len(out) >= limit:
                break
        return out

    result: dict[str, list[dict]] = {}
    for kind in ("at_risk", "no_estimate", "no_release"):
        samples = _samples(risks.get(kind))
        if samples:
            result[kind] = samples
    return result


def _is_early_for_today_worklog(now: datetime | None = None) -> bool:
    current = now or datetime.now(REPORT_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=REPORT_TZ)
    else:
        current = current.astimezone(REPORT_TZ)
    return current.hour < TODAY_WORKLOG_JUDGE_AFTER_HOUR


def _parse_day(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _build_worklog_block(
    person: dict,
    profile: dict | None,
    *,
    expected: float,
) -> dict[str, Any]:
    """Sprint-first worklog signals; today muted until <2h left before 18:00 MSK."""
    expected_f = float(expected or 8.0) or 8.0
    warn_ratio = float(get_team_config().metrics.hours_warn_ratio or 0.6)
    today = report_today()
    early = _is_early_for_today_worklog()
    weekend_today = today.weekday() >= 5

    try:
        hours_today = float(person.get("hours_today") or 0.0)
    except (TypeError, ValueError):
        hours_today = 0.0
    try:
        hours_sprint = float(person.get("hours_sprint") or 0.0)
    except (TypeError, ValueError):
        hours_sprint = 0.0

    past_hours: list[float] = []
    day_hours = []
    if isinstance(profile, dict):
        day_hours = list(profile.get("day_hours") or [])

    for item in day_hours:
        if not isinstance(item, dict):
            continue
        day = _parse_day(item.get("date"))
        if day is None or day.weekday() >= 5:
            continue
        if item.get("is_future") or day > today:
            continue
        try:
            hours = float(item.get("hours") or 0.0)
        except (TypeError, ValueError):
            hours = 0.0
        if day == today:
            hours_today = hours
            continue
        # Completed past workdays only — primary sprint rhythm signal
        past_hours.append(hours)

    elapsed = len(past_hours)
    days_with_logs = sum(1 for h in past_hours if h > 0.05)
    days_near_norm = sum(1 for h in past_hours if h + 1e-6 >= expected_f * warn_ratio)
    days_full = sum(1 for h in past_hours if h + 1e-6 >= expected_f * 0.95)
    sum_past = sum(past_hours)
    avg_past = round(sum_past / elapsed, 2) if elapsed else 0.0
    fill_pct = round(100.0 * sum_past / (elapsed * expected_f), 1) if elapsed else None

    if elapsed == 0:
        sprint_level = "unknown"
        sprint_hint = "мало прошедших рабочих дней для оценки ритма списаний"
    elif days_with_logs == 0:
        sprint_level = "none"
        sprint_hint = "за прошедшие рабочие дни спринта списаний почти нет"
    elif fill_pct is not None and fill_pct >= 85 and days_near_norm >= max(1, elapsed - 1):
        sprint_level = "ok"
        sprint_hint = "ритм списаний за спринт выглядит ровным"
    elif fill_pct is not None and fill_pct >= 55:
        sprint_level = "mixed"
        sprint_hint = "списания за спринт есть, но день ото дня неровно"
    else:
        sprint_level = "low"
        sprint_hint = "за спринт списания заметно ниже нормы по рабочим дням"

    today_level = str(person.get("hours_level") or "skip")
    if weekend_today:
        today_judge = False
        today_hint = "сегодня выходной — списания за день не оцениваем"
        today_too_early = False
    elif early:
        today_judge = False
        today_too_early = True
        today_hint = (
            f"до {TODAY_WORKLOG_JUDGE_AFTER_HOUR}:00 МСК (>2 ч до конца дня 18:00) "
            "— рано судить о списаниях за сегодня"
        )
        # Don't leak a scary today_level to the model while it's early
        today_level = "pending"
    else:
        today_too_early = False
        today_judge = today_level not in {"skip"}
        if today_level in {"low", "bad", "warn"}:
            today_hint = "к этому моменту дня списания ниже обычной нормы"
        elif today_level == "ok":
            today_hint = "списания за сегодня около нормы"
        else:
            today_hint = None

    return {
        "hours_today": round(hours_today, 2),
        "hours_sprint": round(hours_sprint, 2),
        "expected_hours_per_day": expected_f,
        "primary": "sprint",
        "today": {
            "hours": round(hours_today, 2),
            "level": today_level,
            "judge": today_judge,
            "too_early": today_too_early,
            "hint": today_hint,
        },
        "sprint": {
            "elapsed_workdays": elapsed,
            "days_with_logs": days_with_logs,
            "days_near_norm": days_near_norm,
            "days_full_norm": days_full,
            "avg_hours_per_workday": avg_past,
            "fill_pct": fill_pct,
            "level": sprint_level,
            "hint": sprint_hint,
        },
    }


def _nearest_release_token(sprint_report: dict) -> str | None:
    """Token of the nearest unreleased release (for focus tasks beyond sprint goal)."""
    candidates: list[tuple[int, str]] = []
    for rel in sprint_report.get("releases") or []:
        if not isinstance(rel, dict) or rel.get("released"):
            continue
        days = rel.get("days_left")
        if days is None:
            continue
        try:
            days_i = int(days)
        except (TypeError, ValueError):
            continue
        if days_i < 0:
            continue
        name = str(rel.get("name") or "").strip()
        if not name:
            continue
        candidates.append((days_i, name))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return _goal_token(candidates[0][1])


def _focus_tasks_for_profile(
    profile: dict | None,
    *,
    goal_token: str | None,
    near_token: str | None,
    limit: int = 3,
) -> list[dict]:
    """Deterministic urgent tasks for goal / nearest release (for note + UI chips)."""
    if not isinstance(profile, dict):
        return []
    active = [
        t
        for t in (profile.get("tasks_active") or [])
        if isinstance(t, dict) and not _is_hidden_task(t)
    ]
    goal_items: list[dict] = []
    near_items: list[dict] = []
    for task in active:
        key = str(task.get("key") or "").strip()
        if not key:
            continue
        releases = _release_labels(task)
        on_goal = bool(goal_token) and any(goal_token in label for label in releases)
        on_near = bool(near_token) and near_token != goal_token and any(
            near_token in label for label in releases
        )
        if not on_goal and not on_near:
            continue
        try:
            rem = float(task.get("remaining_hours") or 0)
        except (TypeError, ValueError):
            rem = 0.0
        item = {
            "key": key,
            "summary": (str(task.get("summary") or "")[:80] or None),
            "status": task.get("status"),
            "remaining_hours": rem if rem > 0 else None,
            "releases": releases[:2],
            "reason": "goal" if on_goal else "nearest_release",
            "reason_label": (
                "цель спринта" if on_goal else "ближайший релиз"
            ),
        }
        if on_goal:
            goal_items.append(item)
        else:
            near_items.append(item)

    def _sort_key(item: dict) -> tuple:
        rem = item.get("remaining_hours")
        return (0 if rem else 1, -(float(rem or 0)), str(item.get("key") or ""))

    goal_items.sort(key=_sort_key)
    near_items.sort(key=_sort_key)
    out: list[dict] = []
    seen: set[str] = set()
    for item in goal_items + near_items:
        key = item["key"]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def build_person_rows(sprint_report: dict) -> list[dict]:
    team = sprint_report.get("team") or []
    people = sprint_report.get("people") or {}
    sprint = sprint_report.get("sprint") or {}
    worklogs = sprint_report.get("worklogs") or {}
    try:
        expected = float(
            worklogs.get("expected_hours_per_day")
            or (team[0].get("expected_hours_today") if team else None)
            or 8.0
        )
    except (TypeError, ValueError, IndexError):
        expected = 8.0
    rating_index = _index_rating_rows(sprint_report.get("ratings") or [])
    token = _goal_token((sprint.get("goal") or "").strip())
    near_token = _nearest_release_token(sprint_report)
    rows: list[dict] = []
    for person in team:
        name = person.get("name")
        if not name:
            continue
        load = person.get("load") or {}
        profile = people.get(name) if isinstance(people, dict) else None
        pressure = _person_task_pressure(profile, token)
        is_lead = bool(person.get("lead"))
        worklog = _build_worklog_block(person, profile, expected=expected)
        hours_sprint_f = float(worklog.get("hours_sprint") or 0.0)
        focus_tasks = _focus_tasks_for_profile(
            profile, goal_token=token, near_token=near_token, limit=3
        )
        risk_tasks = _person_risk_tasks(sprint_report, name, limit=4)
        gender = person.get("gender")
        if gender not in {"m", "f"}:
            gender = get_team_config().gender_for(name)
        row: dict[str, Any] = {
            "name": name,
            "short": _short_person_name(name),
            "direction": person.get("direction"),
            "role": "lead" if is_lead else "member",
            "lead": is_lead,
            "gender": gender if gender in {"m", "f"} else None,
            "load_pct": load.get("load_pct"),
            "level": load.get("level"),
            "hours_sprint": hours_sprint_f,
            "tasks_open": person.get("tasks_open"),
            "tasks_done": person.get("tasks_done"),
            "worklog": worklog,
        }
        if focus_tasks:
            row["focus_tasks"] = focus_tasks
        if risk_tasks:
            row["risk_tasks"] = risk_tasks
        hits = []
        if isinstance(profile, dict):
            for hit in profile.get("ratings") or []:
                if not isinstance(hit, dict):
                    continue
                hits.append(
                    {
                        "id": hit.get("id"),
                        "title": hit.get("title"),
                        "place": hit.get("place"),
                        "value": hit.get("value"),
                    }
                )
        signals = rating_index.get(name) or []
        if hits:
            row["rating_hits"] = hits[:4]
        if signals:
            row["rating_signals"] = signals
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
            hot = load.get("level") in {"over", "tight"} or float(
                pressure.get("carryover_share") or 0
            ) >= 40
            if hot:
                row["urgent_samples"] = pressure.get("urgent_samples") or []
                row["carryover_samples"] = pressure.get("carryover_samples") or []
        rows.append(row)
    return rows


def _fallback_note(row: dict) -> dict:
    load = row.get("load_pct")
    level = row.get("level")
    pressure = row.get("task_pressure") or {}
    worklog = row.get("worklog") or {}
    carry_share = float(pressure.get("carryover_share") or 0)
    role = row.get("role") or "member"
    direction = str(row.get("direction") or "").strip()
    dir_bit = f" в «{direction}»" if direction else ""
    focus: list[str] = []
    tone = "ok"
    parts: list[str] = []

    if level in {"over", "tight"}:
        tone = "attention"
        focus.append("overload")
        if load is not None:
            parts.append(
                f"Сейчас нагрузка около {float(load):.0f}%{dir_bit} — "
                "стоит аккуратно выровнять срочное и хвост прошлых спринтов."
            )
        else:
            parts.append(
                f"Нагрузка выглядит напряжённой{dir_bit} — "
                "имеет смысл уточнить приоритеты на ближайшие дни."
            )
    elif carry_share >= 40:
        tone = "attention"
        focus.append("carryover")
        parts.append(
            "Заметная доля задач тянется из прошлых спринтов — "
            "полезно отделить этот хвост от задач текущей цели."
        )
    elif int(row.get("tasks_open") or 0) == 0 and float(row.get("hours_sprint") or 0) <= 0.05:
        tone = "info"
        focus.append("underload")
        if role == "lead":
            parts.append(
                f"По задачам и списаниям пока тишина{dir_bit}. "
                "Как лиду направления, можно мягко подхватить приоритеты команды под цель спринта."
            )
        else:
            parts.append(
                f"Активных задач и списаний пока нет{dir_bit}. "
                "Если появится ёмкость — имеет смысл взять работу по цели направления."
            )
    elif level in {"empty", "ok"} and load is not None and float(load) < 40:
        tone = "info"
        focus.append("underload")
        parts.append(
            f"Нагрузка около {float(load):.0f}%{dir_bit} — "
            "при необходимости можно подхватить задачи цели или ближайшего релиза."
        )
    elif load is not None:
        parts.append(f"Нагрузка около {float(load):.0f}%{dir_bit}, по задачам картина спокойная.")
    else:
        parts.append("Явных тревожных сигналов по нагрузке в снимке нет.")

    sprint_wl = worklog.get("sprint") if isinstance(worklog.get("sprint"), dict) else {}
    today_wl = worklog.get("today") if isinstance(worklog.get("today"), dict) else {}
    sprint_level = str(sprint_wl.get("level") or "")
    if sprint_level == "ok":
        focus.append("worklog")
        parts.append("По списаниям за спринт ритм выглядит ровным.")
    elif sprint_level == "mixed":
        focus.append("worklog")
        if tone == "ok":
            tone = "info"
        parts.append("Списания за спринт есть, но день ото дня неровно — стоит чуть выровнять учёт времени.")
    elif sprint_level in {"low", "none"}:
        focus.append("worklog")
        if tone == "ok":
            tone = "info"
        if int(row.get("tasks_open") or 0) > 0 or float(row.get("hours_sprint") or 0) > 0.05:
            parts.append(
                "По прошедшим рабочим дням спринта списания пока слабоваты — "
                "имеет смысл аккуратнее вести учёт времени."
            )
    # Today only if the day is far enough along to judge
    if today_wl.get("judge") and str(today_wl.get("level") or "") in {"low", "bad", "warn"}:
        focus.append("worklog")
        if tone == "ok":
            tone = "info"
        parts.append("К этому моменту дня списания ниже обычной нормы.")
    elif today_wl.get("too_early"):
        # Explicitly avoid "no logs today" noise in fallbacks
        pass

    hits = row.get("rating_hits") or []
    signals = row.get("rating_signals") or []
    stability = next(
        (x for x in (hits + signals) if str(x.get("id")) == "stability"),
        None,
    )
    if stability and stability.get("place") is not None and int(stability["place"]) <= 5:
        focus.append("ratings")
        stab_val = stability.get("value")
        suffix = f" ({stab_val})" if stab_val else ""
        parts.append(f"По стабильности списаний выглядит уверенно{suffix}.")
    elif stability and stability.get("value") and str(stability.get("value")).startswith("0"):
        focus.append("ratings")
        if tone == "ok":
            tone = "info"
        parts.append("По стабильности списаний пока слабый сигнал — мало данных за спринт.")

    on_goal = int(pressure.get("on_goal_release") or 0)
    if on_goal > 0 and "goal" not in focus and tone == "attention":
        focus.append("goal")

    no_rel = int(pressure.get("no_release_tasks") or 0)
    active_n = int(pressure.get("active_tasks") or 0)
    if no_rel >= 2 and (active_n == 0 or no_rel >= max(2, active_n // 2)):
        focus.append("no_release")
        if tone == "ok":
            tone = "info"
        parts.append(
            f"У части активных задач ({no_rel}) нет релиза — "
            "стоит привязать их к Fix Version, чтобы работа шла в цель."
        )

    risk_tasks = row.get("risk_tasks") if isinstance(row.get("risk_tasks"), dict) else {}
    at_risk_items = [
        t for t in (risk_tasks.get("at_risk") or []) if isinstance(t, dict) and t.get("key")
    ][:3]
    if at_risk_items:
        focus.append("at_risk")
        tone = "attention"
        keys = ", ".join(f"`{t['key']}`" for t in at_risk_items)
        parts.append(f"Под риском не успеть к концу спринта: {keys}.")
    no_est_items = [
        t
        for t in (risk_tasks.get("no_estimate") or [])
        if isinstance(t, dict) and t.get("key")
    ][:3]
    if no_est_items:
        focus.append("no_estimate")
        if tone == "ok":
            tone = "info"
        keys = ", ".join(f"`{t['key']}`" for t in no_est_items)
        parts.append(f"Без Original Estimate: {keys} — стоит оценить объём.")
    no_rel_items = [
        t
        for t in (risk_tasks.get("no_release") or [])
        if isinstance(t, dict) and t.get("key")
    ][:3]
    if no_rel_items and "no_release" not in focus:
        focus.append("no_release")
        if tone == "ok":
            tone = "info"
        keys = ", ".join(f"`{t['key']}`" for t in no_rel_items)
        parts.append(f"Без релиза: {keys} — полезно привязать к Fix Version.")

    focus_tasks = [
        t for t in (row.get("focus_tasks") or []) if isinstance(t, dict) and t.get("key")
    ][:3]
    if focus_tasks:
        focus.append("goal")
        keys = ", ".join(f"`{t['key']}`" for t in focus_tasks)
        label = focus_tasks[0].get("reason_label") or "цель/релиз"
        parts.append(f"В фокусе на {label}: {keys}.")

    text = soft_person_note_text(" ".join(parts))
    # unique focus
    uniq_focus: list[str] = []
    for item in focus:
        if item not in uniq_focus:
            uniq_focus.append(item)
    return {"text": text, "tone": tone, "focus": uniq_focus}


def _build_name_index(rows: list[dict]) -> dict[str, str]:
    """Map various name forms → canonical roster name."""
    index: dict[str, str] = {}
    cfg = get_team_config()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        index[name.lower()] = name
        short = _short_person_name(name)
        if short:
            index[short.lower()] = name
            index[short.replace(" ", "").lower()] = name
        canonical = cfg.canonical_name(name)
        if canonical:
            index[canonical.lower()] = name
    return index


def _resolve_note_name(raw: str | None, name_index: dict[str, str]) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    key = text.lower()
    if key in name_index:
        return name_index[key]
    compact = re.sub(r"\s+", "", key)
    if compact in name_index:
        return name_index[compact]
    cfg = get_team_config()
    canonical = cfg.canonical_name(text)
    if canonical and canonical.lower() in name_index:
        return name_index[canonical.lower()]
    # Surname + initials fuzzy: "Ивановой И.И." vs "Иванова И.И."
    m = re.match(
        r"^([А-ЯЁA-Z][а-яёa-z-]+)\s+([A-Za-zА-ЯЁ])\.?\s*([A-Za-zА-ЯЁ])?\.?$",
        text,
        flags=re.I,
    )
    if m:
        sur = m.group(1).lower()
        i1 = (m.group(2) or "").lower()
        i2 = (m.group(3) or "").lower()
        for form, canonical_name in name_index.items():
            if not form.startswith(sur[: max(3, len(sur) - 2)]):
                continue
            short = (_short_person_name(canonical_name) or "").lower()
            if i1 and i1 in short and (not i2 or i2 in short):
                return canonical_name
    return None


def _normalize_tone(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"attention", "warn", "warning", "risk", "over"}:
        return "attention"
    if text in {"info", "neutral"}:
        return "info"
    return "ok"


def _normalize_focus(value: object) -> list[str]:
    allowed = {
        "carryover",
        "overload",
        "underload",
        "goal",
        "stale",
        "worklog",
        "ratings",
        "no_release",
        "at_risk",
        "no_estimate",
    }
    out: list[str] = []
    items = value if isinstance(value, list) else []
    for item in items:
        key = str(item or "").strip().lower()
        if key in allowed and key not in out:
            out.append(key)
    return out


def _apply_notes_to_people(sprint_report: dict, notes: dict[str, dict]) -> None:
    people = sprint_report.get("people")
    if not isinstance(people, dict):
        return
    for name, note in notes.items():
        profile = people.get(name)
        if isinstance(profile, dict):
            profile["ai_note"] = {
                "text": note.get("text"),
                "tone": note.get("tone") or "ok",
                "focus": list(note.get("focus") or []),
            }


def generate_ai_person_notes(
    sprint_report: dict | None,
    *,
    brief: dict | None = None,
    previous: dict | None = None,
    mock: bool = False,
) -> dict:
    settings = _llm_settings()
    model = settings["model"]
    base = {
        "status": "skipped",
        "generated_at": _iso_now(),
        "model": model,
        "model_label": short_model_name(model),
        "author": corporate_author(model),
        "prompt_version": NOTES_PROMPT_VERSION,
        "source_brief_hash": None,
        "notes_hash": None,
        "notes": {},
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
    if not isinstance(brief, dict) or brief.get("status") != "ok":
        base["reason"] = "depends_on_brief"
        return base
    if not settings["url"] or not settings["token"]:
        base["reason"] = "no_api_key"
        return base

    rows = build_person_rows(sprint_report)
    if not rows:
        base["reason"] = "no_people"
        return base

    brief_context = build_brief_context(brief, sprint_report)
    payload = {"brief_context": brief_context, "people": rows}
    notes_hash = _snapshot_hash(payload)
    base["source_brief_hash"] = brief.get("snapshot_hash")
    base["notes_hash"] = notes_hash

    prev = previous if isinstance(previous, dict) else None
    if (
        prev
        and prev.get("status") == "ok"
        and prev.get("notes_hash") == notes_hash
        and prev.get("source_brief_hash") == brief.get("snapshot_hash")
        and prev.get("prompt_version") == NOTES_PROMPT_VERSION
        and isinstance(prev.get("notes"), dict)
        and prev.get("notes")
    ):
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
            return reused

    timeout = max(int(settings["timeout"]), 90)
    try:
        content, used_model = _call_litellm_chat(
            url=settings["url"],
            token=settings["token"],
            model=model,
            temperature=min(float(settings["temperature"]), 0.3),
            timeout=timeout,
            system=SYSTEM_PROMPT,
            user=USER_PROMPT_PREFIX + json.dumps(payload, ensure_ascii=False, indent=2),
        )
        parsed = _parse_model_json(content)
        raw_notes = parsed.get("notes")
        if not isinstance(raw_notes, list):
            raise RuntimeError("notes list missing")

        name_index = _build_name_index(rows)
        notes: dict[str, dict] = {}
        for item in raw_notes:
            if not isinstance(item, dict):
                continue
            canonical = _resolve_note_name(item.get("name"), name_index)
            if not canonical or canonical in notes:
                continue
            text = soft_person_note_text(str(item.get("text") or "").strip())
            if not text:
                continue
            if len(text) > 480:
                text = text[:477].rstrip() + "…"
            notes[canonical] = {
                "text": text,
                "tone": _normalize_tone(item.get("tone")),
                "focus": _normalize_focus(item.get("focus")),
            }

        # Fill gaps with metric fallbacks so every roster person has a note
        for row in rows:
            name = row["name"]
            if name not in notes:
                notes[name] = _fallback_note(row)

        return {
            "status": "ok",
            "generated_at": _iso_now(),
            "model": used_model,
            "model_label": short_model_name(used_model),
            "author": corporate_author(used_model),
            "prompt_version": NOTES_PROMPT_VERSION,
            "source_brief_hash": brief.get("snapshot_hash"),
            "notes_hash": notes_hash,
            "notes": notes,
            "error": None,
            "reason": None,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        base["status"] = "error"
        base["error"] = f"HTTP {exc.code}: {detail or exc.reason}"
        return base
    except Exception as exc:  # noqa: BLE001 - notes must not fail collect
        base["status"] = "error"
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base


def attach_ai_person_notes(
    report: dict,
    *,
    previous_report: dict | None = None,
    mock: bool = False,
) -> dict:
    if not isinstance(report, dict):
        return report
    sr = report.get("sprint_report")
    if not isinstance(sr, dict):
        return report

    brief = sr.get("ai_brief") if isinstance(sr.get("ai_brief"), dict) else None
    prev_notes = None
    if isinstance(previous_report, dict):
        prev_sr = previous_report.get("sprint_report") or {}
        if isinstance(prev_sr, dict):
            prev_notes = prev_sr.get("ai_person_notes")

    bundle = generate_ai_person_notes(
        sr,
        brief=brief,
        previous=prev_notes if isinstance(prev_notes, dict) else None,
        mock=mock,
    )
    sr["ai_person_notes"] = bundle
    if bundle.get("status") == "ok" and isinstance(bundle.get("notes"), dict):
        _apply_notes_to_people(sr, bundle["notes"])
    else:
        # Clear stale per-person notes if generation skipped/failed
        people = sr.get("people")
        if isinstance(people, dict):
            for profile in people.values():
                if isinstance(profile, dict):
                    profile.pop("ai_note", None)
    report["sprint_report"] = sr
    return report

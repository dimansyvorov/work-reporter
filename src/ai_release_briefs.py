from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone
from typing import Any

from .ai_brief import (
    _call_litellm_chat,
    _goal_token,
    _iso_now,
    _is_hidden_task,
    _llm_settings,
    _short_person_name,
    _snapshot_hash,
    corporate_author,
    extract_verdict,
    humanize_ai_text,
    short_model_name,
)

PROMPT_VERSION = "release-group-brief-v2"

SYSTEM_PROMPT = """Ты — аналитик релизов в спринтовой разработке.
Тебе дают JSON-снимок ГРУППЫ релизов на ОДНУ дату релиза: несколько Fix Version
одной команды с общей датой, задачи, люди, эпики, цель спринта.

Задача: дать ОДНУ общую практическую оценку «успеваем ли к этой дате релиза»
и рекомендации на ближайшие 1–2 дня. Не оценивай каждый релиз отдельным вердиктом —
смотри на группу целиком (команда делает задачи всех релизов даты, не всегда по очереди).

Правила:
1. Опирайся ТОЛЬКО на факты из JSON. Не выдумывай задачи, людей, проценты, причины.
2. Новых запросов к Jira нет — данных в снимке достаточно; если чего-то нет, так и скажи.
3. Сравнивай суммарный прогресс задач группы с календарём до даты релиза.
4. Цель спринта (sprint_goal.text) может содержать дату, названия релизов ИЛИ бизнесовые
   формулировки без даты. Если группа похоже связана с целью — скажи явно; если нет —
   не натягивай. Не ожидай, что в цели всегда будет дата релиза.
5. Если в группе несколько релизов — кратко упомяни, где основной риск (по имени релиза),
   но вердикт один на всю дату.
6. Если риск низкий / всё в графике / уже выпущено — пиши КОРОТКО (3–6 предложений).
7. Если есть угроза сроку / отставание / перегруз — разверни риски и действия;
   задач упоминай точечно (обычно 2–5 ключей), не каталогом.
8. Рекомендации мягко («стоит», «важно», «имеет смысл»). Людей — «Имя Ф.» («Роман Щ.»).
   Про людей пиши «сотрудник», «коллега», «человек», «команда».
   ЗАПРЕЩЕНО называть людей «ресурсом» / «ресурсами» / «юнитом» / «FTE».
9. Задачу/эпик — ТОЛЬКО ключ в бэктиках (`SPRINT-11708`). UI подставит «KEY | название».
   НЕ пиши название после ключа. НЕ жирни ключи.
10. Лёгкий markdown: *курсив*; **жирный** только для акцентов не про ключи. Без HTML.
11. НЕ используй сырые ключи JSON (progress_pct, slip_gap_pp, at_risk…).
    Пиши по-русски: «прогресс задач», «календарь до релиза», «отставание», «нагрузка»,
    «риск срыва», «в графике», «просрочен».
12. Пиши по-русски, спокойным деловым тоном, без эмодзи.

Формат ответа:

## Вердикт
1–2 предложения про всю дату релиза: в графике / под угрозой / просрочено / выпущено.

## Сигналы
2–5 буллетов по фактам (прогресс vs календарь, ёмкость, цель, перегруз людей).
Если рисков почти нет — 1–2 буллета.

## Риски
Только подтверждённые данными. Если явных рисков нет — одна строка
«Явных критичных рисков в снимке нет».

## Рекомендации
1–4 действия на 1–2 дня (или «Достаточно держать текущий темп»).
При необходимости — сотрудники и ключи задач/эпиков в бэктиках.
"""

USER_PROMPT_PREFIX = """Оцени группу релизов на одну дату по JSON ниже.
Один общий вердикт на дату. Если спокойно — будь краток.
Если угроза сроку — практичные рекомендации и 2–5 ключей в бэктиках без названий рядом.
Имена людей — «Имя Ф.» («Роман Щ.»).

JSON:
"""


def release_group_key(release: dict | None) -> str:
    """YYYY-MM-DD from release_date, or 'undated'."""
    if not isinstance(release, dict):
        return "undated"
    raw = str(release.get("release_date") or "").strip()
    if not raw:
        return "undated"
    # Accept ISO date or datetime prefix
    return raw[:10] if len(raw) >= 10 else raw


def _clip(text: object, n: int = 160) -> str | None:
    value = str(text or "").strip()
    if not value:
        return None
    return value if len(value) <= n else value[: n - 1].rstrip() + "…"


def _comment_snippets(issue: dict, *, limit: int = 2) -> list[dict]:
    out: list[dict] = []
    for comment in (issue.get("comments") or [])[:8]:
        if not isinstance(comment, dict):
            continue
        body = _clip(comment.get("body"), 180)
        if not body:
            continue
        out.append(
            {
                "author": _short_person_name(comment.get("author_canonical") or comment.get("author")),
                "at": comment.get("at"),
                "body": body,
            }
        )
        if len(out) >= limit:
            break
    if out:
        return out
    # Fallback: worklog comments often carry the real status narrative
    for log in (issue.get("worklogs") or [])[:10]:
        if not isinstance(log, dict):
            continue
        body = _clip(log.get("comment"), 160)
        if not body:
            continue
        out.append(
            {
                "author": _short_person_name(log.get("author_canonical") or log.get("author")),
                "at": log.get("at"),
                "body": body,
                "via": "worklog",
            }
        )
        if len(out) >= limit:
            break
    return out


def _non_release_tag_labels(task: dict) -> list[str]:
    labels: list[str] = []
    for tag in task.get("tags") or []:
        if not isinstance(tag, dict) or tag.get("id") == "release":
            continue
        label = str(tag.get("label") or "").strip()
        if label:
            labels.append(label)
    return labels[:4]


def build_release_snapshot(sprint_report: dict, release: dict) -> dict:
    """Compact per-release snapshot for the LLM (no extra Jira calls)."""
    issues = sprint_report.get("issues") or {}
    people = sprint_report.get("people") or {}
    team = sprint_report.get("team") or []
    sprint = sprint_report.get("sprint") or {}
    all_releases = sprint_report.get("releases") or []
    epic_timeline = sprint_report.get("epic_timeline") or {}
    epics_by_key = {
        str(e.get("key") or "").upper(): e
        for e in (epic_timeline.get("epics") or [])
        if isinstance(e, dict) and e.get("key")
    }

    release_id = str(release.get("id") or "")
    release_name = str(release.get("name") or "")
    goal_text = (sprint.get("goal") or "").strip()
    goal_token = _goal_token(goal_text)
    on_goal = bool(goal_token) and (
        goal_token in release_name
        or any(goal_token in str(x) for x in (release.get("risk_reasons") or []))
    )

    tasks_raw = list(release.get("tasks") or [])
    if not tasks_raw:
        for section in release.get("sections") or []:
            tasks_raw.extend(section.get("tasks_detail") or [])

    task_rows: list[dict] = []
    epic_keys: set[str] = set()
    people_keys: set[str] = set()
    for task in tasks_raw[:60]:
        if not isinstance(task, dict):
            continue
        key = str(task.get("key") or "").upper()
        if not key:
            continue
        dossier = issues.get(key) if isinstance(issues, dict) else None
        if not isinstance(dossier, dict):
            dossier = {}
        if _is_hidden_task(task, issues if isinstance(issues, dict) else None):
            continue
        assignee = (
            task.get("assignee_canonical")
            or task.get("assignee")
            or dossier.get("assignee_canonical")
            or dossier.get("assignee")
        )
        if assignee:
            people_keys.add(str(assignee))
        epic_key = str(task.get("epic_key") or dossier.get("epic_key") or "").upper()
        if epic_key:
            epic_keys.add(epic_key)
        row = {
            "key": key,
            "summary": _clip(task.get("summary") or dossier.get("summary"), 90),
            "status": task.get("status") or dossier.get("status"),
            "direction": task.get("direction") or dossier.get("direction"),
            "direction_state": task.get("direction_state") or dossier.get("direction_state"),
            "assignee": _short_person_name(assignee),
            "estimate_hours": task.get("estimate_hours")
            if task.get("estimate_hours") is not None
            else dossier.get("estimate_hours"),
            "remaining_hours": dossier.get("remaining_hours"),
            "tags": _non_release_tag_labels(task) or _non_release_tag_labels(dossier),
            "epic_key": epic_key or None,
            "risk": bool(task.get("risk") or dossier.get("risk")),
        }
        snippets = _comment_snippets(dossier, limit=2)
        if snippets:
            row["notes"] = snippets
        task_rows.append(row)

    # Prefer active / risky tasks first for token budget
    task_rows.sort(
        key=lambda t: (
            0 if t.get("direction_state") == "active" else 1,
            0 if t.get("risk") else 1,
            str(t.get("key") or ""),
        )
    )
    task_rows = task_rows[:28]

    people_rows: list[dict] = []
    team_by_name = {
        str(p.get("name") or ""): p for p in team if isinstance(p, dict) and p.get("name")
    }
    for name in sorted(people_keys):
        profile = people.get(name) if isinstance(people, dict) else None
        person = team_by_name.get(name) or {}
        load = (person.get("load") if isinstance(person, dict) else None) or (
            (profile or {}).get("load") if isinstance(profile, dict) else None
        ) or {}
        on_release = sum(
            1
            for t in task_rows
            if t.get("assignee") and t.get("assignee") == _short_person_name(name)
        )
        # Count via canonical match on raw tasks too
        if on_release == 0:
            on_release = sum(
                1
                for t in tasks_raw
                if isinstance(t, dict)
                and str(t.get("assignee") or t.get("assignee_canonical") or "") == name
            )
        people_rows.append(
            {
                "name": name,
                "short": _short_person_name(name),
                "direction": person.get("direction")
                or (profile or {}).get("direction"),
                "lead": bool(person.get("lead")),
                "load_pct": load.get("load_pct"),
                "level": load.get("level"),
                "tasks_on_release": on_release,
            }
        )
    people_rows.sort(key=lambda p: (-(float(p.get("load_pct") or 0)), p.get("short") or ""))
    people_rows = people_rows[:12]

    epic_rows: list[dict] = []
    for ek in sorted(epic_keys):
        epic = epics_by_key.get(ek) or {}
        epic_rows.append(
            {
                "key": ek,
                "summary": _clip(epic.get("summary"), 80),
                "progress_pct": epic.get("progress_pct"),
                "tasks_open": epic.get("tasks_open") or epic.get("tasks_active"),
                "tasks_total": epic.get("tasks_total"),
            }
        )
    epic_rows = epic_rows[:8]

    # Overlaps with other releases: shared people / epics
    overlaps: list[dict] = []
    my_people = people_keys
    my_epics = epic_keys
    for other in all_releases:
        if not isinstance(other, dict):
            continue
        oid = str(other.get("id") or "")
        if oid and oid == release_id:
            continue
        if str(other.get("name") or "") == release_name and not oid:
            continue
        other_tasks = list(other.get("tasks") or [])
        if not other_tasks:
            for section in other.get("sections") or []:
                other_tasks.extend(section.get("tasks_detail") or [])
        other_people: set[str] = set()
        other_epics: set[str] = set()
        for t in other_tasks:
            if not isinstance(t, dict):
                continue
            a = str(t.get("assignee_canonical") or t.get("assignee") or "").strip()
            if a:
                other_people.add(a)
            ek = str(t.get("epic_key") or "").upper()
            if ek:
                other_epics.add(ek)
        shared_people = sorted(my_people & other_people)
        shared_epics = sorted(my_epics & other_epics)
        if not shared_people and not shared_epics:
            continue
        overlaps.append(
            {
                "name": other.get("name"),
                "date": other.get("release_date"),
                "risk": other.get("risk"),
                "shared_people": [_short_person_name(x) for x in shared_people[:6]],
                "shared_epics": shared_epics[:4],
            }
        )
    overlaps = overlaps[:6]

    return {
        "release": {
            "id": release_id or None,
            "name": release_name,
            "date": release.get("release_date"),
            "released": bool(release.get("released")),
            "description": _clip(release.get("description"), 280),
            "progress_pct": release.get("progress_pct"),
            "time_pct": release.get("time_pct"),
            "slip_gap_pp": release.get("slip_gap_pp"),
            "days_left": release.get("days_left"),
            "tasks_done": release.get("tasks_done"),
            "tasks_active": release.get("tasks_active"),
            "tasks_total": release.get("tasks_total"),
            "active_estimate_hours": release.get("active_estimate_hours"),
            "capacity_hours": release.get("capacity_hours"),
            "risk": release.get("risk"),
            "risk_label": release.get("risk_label"),
            "on_sprint_goal": on_goal,
        },
        "sprint_goal": {
            "text": goal_text or None,
            "days_left": sprint.get("days_left"),
        },
        "tasks": task_rows,
        "people": people_rows,
        "epics": epic_rows,
        "overlaps": overlaps,
        "legend": {
            "notes": "короткие комментарии сотрудников или комментарии к worklog по задачам релиза",
            "overlaps": "другие релизы с общими исполнителями или эпиками",
            "on_sprint_goal": "релиз похоже связан с целью текущего спринта",
        },
    }


def build_release_group_snapshot(sprint_report: dict, releases: list[dict]) -> dict:
    """One snapshot for all releases sharing a release_date."""
    items = [r for r in releases if isinstance(r, dict) and r.get("name")]
    if not items:
        return {"releases": [], "tasks": [], "people": [], "epics": []}
    date_key = release_group_key(items[0])
    per = [build_release_snapshot(sprint_report, r) for r in items]

    release_rows: list[dict] = []
    task_rows: list[dict] = []
    people_map: dict[str, dict] = {}
    epic_map: dict[str, dict] = {}
    seen_tasks: set[str] = set()
    risks: list[str] = []
    any_on_goal = False
    for snap, rel in zip(per, items):
        rmeta = snap.get("release") if isinstance(snap.get("release"), dict) else {}
        release_rows.append(
            {
                "id": rmeta.get("id") or rel.get("id"),
                "name": rmeta.get("name") or rel.get("name"),
                "progress_pct": rmeta.get("progress_pct"),
                "time_pct": rmeta.get("time_pct"),
                "slip_gap_pp": rmeta.get("slip_gap_pp"),
                "days_left": rmeta.get("days_left"),
                "tasks_done": rmeta.get("tasks_done"),
                "tasks_active": rmeta.get("tasks_active"),
                "tasks_total": rmeta.get("tasks_total"),
                "risk": rmeta.get("risk"),
                "risk_label": rmeta.get("risk_label"),
                "released": rmeta.get("released"),
                "on_sprint_goal": rmeta.get("on_sprint_goal"),
            }
        )
        if rmeta.get("on_sprint_goal"):
            any_on_goal = True
        risk = str(rmeta.get("risk") or "")
        if risk:
            risks.append(risk)
        for t in snap.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            key = str(t.get("key") or "").upper()
            if not key or key in seen_tasks:
                continue
            seen_tasks.add(key)
            row = dict(t)
            row["release_name"] = rmeta.get("name") or rel.get("name")
            task_rows.append(row)
        for p in snap.get("people") or []:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "")
            if not name:
                continue
            prev = people_map.get(name)
            if not prev:
                people_map[name] = dict(p)
            else:
                try:
                    prev["tasks_on_release"] = int(prev.get("tasks_on_release") or 0) + int(
                        p.get("tasks_on_release") or 0
                    )
                except (TypeError, ValueError):
                    pass
        for e in snap.get("epics") or []:
            if not isinstance(e, dict):
                continue
            ek = str(e.get("key") or "").upper()
            if ek and ek not in epic_map:
                epic_map[ek] = e

    task_rows.sort(
        key=lambda t: (
            0 if t.get("direction_state") == "active" else 1,
            0 if t.get("risk") else 1,
            str(t.get("key") or ""),
        )
    )
    people_rows = sorted(
        people_map.values(),
        key=lambda p: (-(float(p.get("load_pct") or 0)), p.get("short") or ""),
    )
    sprint = sprint_report.get("sprint") or {}
    goal_text = (sprint.get("goal") or "").strip()
    days_left = None
    for r in items:
        if r.get("days_left") is not None:
            try:
                d = int(r.get("days_left"))
            except (TypeError, ValueError):
                continue
            days_left = d if days_left is None else min(days_left, d)

    return {
        "release_date": None if date_key == "undated" else date_key,
        "group_key": date_key,
        "releases": release_rows,
        "days_left": days_left,
        "on_sprint_goal": any_on_goal,
        "sprint_goal": {
            "text": goal_text or None,
            "days_left": sprint.get("days_left"),
        },
        "tasks": task_rows[:36],
        "people": people_rows[:14],
        "epics": list(epic_map.values())[:10],
        "legend": {
            "group": "все релизы команды с одной датой Fix Version",
            "on_sprint_goal": "хотя бы один релиз группы похоже связан с целью спринта",
            "tasks.release_name": "к какому релизу группы относится задача",
        },
    }


def _empty_bundle(model: str, *, reason: str | None = None, error: str | None = None) -> dict:
    return {
        "status": "skipped" if not error else "error",
        "generated_at": _iso_now(),
        "model": model,
        "model_label": short_model_name(model),
        "author": corporate_author(model),
        "prompt_version": PROMPT_VERSION,
        "briefs": {},
        "error": error,
        "reason": reason,
        "mode": "date_groups",
    }


def _cache_fresh(prev: dict | None, *, snap_hash: str, cache_sec: int) -> dict | None:
    if not isinstance(prev, dict) or prev.get("status") != "ok":
        return None
    if prev.get("snapshot_hash") != snap_hash:
        return None
    if prev.get("prompt_version") != PROMPT_VERSION:
        return None
    if not prev.get("markdown"):
        return None
    try:
        prev_at = datetime.fromisoformat(
            str(prev.get("generated_at") or "").replace("Z", "+00:00")
        )
        age = (datetime.now(timezone.utc) - prev_at.astimezone(timezone.utc)).total_seconds()
    except ValueError:
        return None
    if age is None or age >= cache_sec:
        return None
    reused = dict(prev)
    reused["status"] = "ok"
    reused["reason"] = "cache"
    reused["author"] = corporate_author(reused.get("model"))
    reused["model_label"] = short_model_name(reused.get("model"))
    if not reused.get("verdict"):
        reused["verdict"] = extract_verdict(reused.get("markdown"))
    return reused


def _generate_group(
    sprint_report: dict,
    group_key: str,
    releases: list[dict],
    *,
    previous: dict | None,
    settings: dict[str, Any],
) -> dict:
    model = settings["model"]
    names = [str(r.get("name") or "") for r in releases if r.get("name")]
    base = {
        "status": "skipped",
        "generated_at": _iso_now(),
        "model": model,
        "model_label": short_model_name(model),
        "author": corporate_author(model),
        "prompt_version": PROMPT_VERSION,
        "group_key": group_key,
        "release_date": None if group_key == "undated" else group_key,
        "release_ids": [str(r.get("id") or "") for r in releases if r.get("id")],
        "release_names": names,
        "snapshot_hash": None,
        "verdict": None,
        "markdown": None,
        "tone": "ok",
        "error": None,
        "reason": None,
    }
    snapshot = build_release_group_snapshot(sprint_report, releases)
    risks = [str(r.get("risk") or "") for r in releases]
    calm = all(r in {"ok", "on_track", "done", ""} for r in risks) and not any(
        float(r.get("slip_gap_pp") or 0) > 12 for r in releases
    )
    if calm:
        activeish = [
            t
            for t in (snapshot.get("tasks") or [])
            if isinstance(t, dict)
            and (t.get("direction_state") == "active" or t.get("risk"))
        ]
        snapshot["tasks"] = (activeish or list(snapshot.get("tasks") or []))[:8]
        snapshot["people"] = list(snapshot.get("people") or [])[:8]
        for task in snapshot["tasks"]:
            if isinstance(task, dict):
                task.pop("notes", None)
    snap_hash = _snapshot_hash(snapshot)
    base["snapshot_hash"] = snap_hash

    cached = _cache_fresh(previous, snap_hash=snap_hash, cache_sec=settings["cache_sec"])
    if cached:
        cached["group_key"] = group_key
        cached["release_date"] = base["release_date"]
        cached["release_ids"] = base["release_ids"]
        cached["release_names"] = names
        return cached

    user_extra = (
        "Группа релизов выглядит спокойной — ответь по-минимуму, почти без списка задач.\n\n"
        if calm
        else ""
    )
    try:
        markdown, used_model = _call_litellm_chat(
            url=settings["url"],
            token=settings["token"],
            model=model,
            temperature=settings["temperature"],
            timeout=settings["timeout"],
            system=SYSTEM_PROMPT,
            user=user_extra
            + USER_PROMPT_PREFIX
            + json.dumps(snapshot, ensure_ascii=False, indent=2),
        )
        markdown = humanize_ai_text(markdown)
        tone = "attention" if any(r in {"at_risk", "overdue"} for r in risks) else "ok"
        if risks and all(r == "done" for r in risks):
            tone = "info"
        return {
            "status": "ok",
            "generated_at": _iso_now(),
            "model": used_model,
            "model_label": short_model_name(used_model),
            "author": corporate_author(used_model),
            "prompt_version": PROMPT_VERSION,
            "group_key": group_key,
            "release_date": base["release_date"],
            "release_ids": base["release_ids"],
            "release_names": names,
            "snapshot_hash": snap_hash,
            "verdict": extract_verdict(markdown),
            "markdown": markdown,
            "tone": tone,
            "error": None,
            "reason": None,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        base["status"] = "error"
        base["error"] = f"HTTP {exc.code}: {detail or exc.reason}"
        return base
    except Exception as exc:  # noqa: BLE001 - must not fail collect
        base["status"] = "error"
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base


def generate_ai_release_briefs(
    sprint_report: dict | None,
    *,
    previous: dict | None = None,
    mock: bool = False,
    on_progress: Any | None = None,
) -> dict:
    settings = _llm_settings()
    model = settings["model"]
    if mock:
        return _empty_bundle(model, reason="mock")
    if not settings["enabled"]:
        return _empty_bundle(model, reason="disabled")
    if not sprint_report:
        return _empty_bundle(model, reason="no_sprint_report")
    if not settings["url"] or not settings["token"]:
        return _empty_bundle(model, reason="no_api_key")

    releases = [
        r for r in (sprint_report.get("releases") or []) if isinstance(r, dict) and r.get("name")
    ]
    if not releases:
        return _empty_bundle(model, reason="no_releases")

    groups: dict[str, list[dict]] = {}
    for release in releases:
        groups.setdefault(release_group_key(release), []).append(release)
    # Soonest dates first; undated last
    ordered_keys = sorted(groups.keys(), key=lambda k: (k == "undated", k))

    prev_briefs = {}
    if isinstance(previous, dict):
        raw = previous.get("briefs")
        if isinstance(raw, dict):
            prev_briefs = raw

    briefs: dict[str, dict] = {}
    errors: list[str] = []
    ok_n = 0
    cache_n = 0
    total = len(ordered_keys)
    for idx, gkey in enumerate(ordered_keys, start=1):
        group_releases = groups[gkey]
        label = gkey if gkey != "undated" else "без даты"
        if callable(on_progress):
            try:
                on_progress(idx, total, label)
            except Exception:  # noqa: BLE001 - progress must not break collect
                pass
        prev_one = prev_briefs.get(gkey)
        result = _generate_group(
            sprint_report,
            gkey,
            group_releases,
            previous=prev_one if isinstance(prev_one, dict) else None,
            settings=settings,
        )
        briefs[gkey] = result
        if result.get("status") == "ok":
            ok_n += 1
            if result.get("reason") == "cache":
                cache_n += 1
        elif result.get("status") == "error":
            errors.append(f"{gkey}: {result.get('error')}")

    if ok_n == 0 and errors:
        bundle = _empty_bundle(model, error="; ".join(errors[:3]), reason="error")
        bundle["briefs"] = briefs
        bundle["status"] = "error"
        return bundle
    if ok_n == 0:
        return _empty_bundle(model, reason="all_skipped")

    return {
        "status": "ok",
        "generated_at": _iso_now(),
        "model": model,
        "model_label": short_model_name(model),
        "author": corporate_author(model),
        "prompt_version": PROMPT_VERSION,
        "briefs": briefs,
        "error": "; ".join(errors[:3]) if errors else None,
        "reason": "cache" if cache_n and cache_n == ok_n else None,
        "ok_count": ok_n,
        "cache_count": cache_n,
        "total": len(briefs),
        "mode": "date_groups",
    }


def _apply_briefs_to_releases(sprint_report: dict, briefs: dict[str, dict]) -> None:
    """Attach the same date-group brief to every release in that group."""
    for release in sprint_report.get("releases") or []:
        if not isinstance(release, dict):
            continue
        gkey = release_group_key(release)
        brief = briefs.get(gkey)
        if isinstance(brief, dict) and brief.get("status") == "ok" and brief.get("markdown"):
            release["ai_brief"] = {
                "text": brief.get("markdown"),
                "markdown": brief.get("markdown"),
                "verdict": brief.get("verdict"),
                "tone": brief.get("tone") or "ok",
                "author": brief.get("author"),
                "model_label": brief.get("model_label"),
                "generated_at": brief.get("generated_at"),
                "reason": brief.get("reason"),
                "group_key": gkey,
                "release_date": brief.get("release_date"),
                "scope": "date_group",
            }
        else:
            release.pop("ai_brief", None)


def attach_ai_release_briefs(
    report: dict,
    *,
    previous_report: dict | None = None,
    mock: bool = False,
    on_progress: Any | None = None,
) -> dict:
    if not isinstance(report, dict):
        return report
    sr = report.get("sprint_report")
    if not isinstance(sr, dict):
        return report

    prev_bundle = None
    if isinstance(previous_report, dict):
        prev_sr = previous_report.get("sprint_report") or {}
        if isinstance(prev_sr, dict):
            prev_bundle = prev_sr.get("ai_release_briefs")

    bundle = generate_ai_release_briefs(
        sr,
        previous=prev_bundle if isinstance(prev_bundle, dict) else None,
        mock=mock,
        on_progress=on_progress,
    )
    sr["ai_release_briefs"] = bundle
    if bundle.get("status") == "ok" and isinstance(bundle.get("briefs"), dict):
        _apply_briefs_to_releases(sr, bundle["briefs"])
    else:
        for release in sr.get("releases") or []:
            if isinstance(release, dict):
                release.pop("ai_brief", None)
    report["sprint_report"] = sr
    return report

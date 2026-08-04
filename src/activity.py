from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .team import TEAM_ROSTER, canonical_team_name
from .team_config import get_team_config

REPORT_TZ = ZoneInfo("Europe/Moscow")

# Higher = more important when collapsing per issue/day.
_EVENT_PRIORITY = {
    "closed": 100,
    "handed_off": 90,
    "to_review": 80,
    "received": 70,
    "started": 60,
    "progress": 40,
}

_MAX_EVENTS = 8

_REVIEW_STATUS_MARKERS = (
    "in review",
    "code review",
    "ревью",
    "review",
)

_CLOSED_STATUS_MARKERS = (
    "closed",
    "done",
    "готово",
    "resolved",
    "verified",
)

_STARTED_STATUS_MARKERS = (
    "in progress",
    "в работе",
    "doing",
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    if len(text) >= 5 and text[-5] in "+-" and ":" not in text[-5:]:
        text = text[:-2] + ":" + text[-2:]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _local_day(value: str | None) -> date | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(REPORT_TZ).date()


def report_today() -> date:
    return datetime.now(REPORT_TZ).date()


def previous_workday(day: date) -> date:
    cur = day - timedelta(days=1)
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


def _norm(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _is_review_status(status: str | None) -> bool:
    key = _norm(status)
    if not key:
        return False
    return any(m in key for m in _REVIEW_STATUS_MARKERS)


def _is_closed_status(status: str | None) -> bool:
    key = _norm(status)
    if not key:
        return False
    return key in _CLOSED_STATUS_MARKERS or any(
        key == m or key.startswith(m + " ") for m in _CLOSED_STATUS_MARKERS
    )


def _is_started_status(status: str | None) -> bool:
    key = _norm(status)
    if not key:
        return False
    return any(m == key or m in key for m in _STARTED_STATUS_MARKERS)


def _short_name(name: str | None) -> str | None:
    if not name:
        return None
    parts = name.strip().split()
    if not parts:
        return None
    # Prefer "Имя" from "Фамилия Имя Отчество" when 2+ parts and first looks like surname
    if len(parts) >= 2:
        return parts[1] if len(parts[0]) > 2 else parts[0]
    return parts[0]


def _verb(person: str | None, male: str, female: str) -> str:
    """Pick masculine/feminine verb form from team.json gender."""
    gender = get_team_config().gender_for(person)
    if gender == "f":
        return female
    return male


def _fmt_hours_minutes(hours: float) -> str:
    total_min = int(round(float(hours) * 60))
    if total_min < 0:
        total_min = 0
    h, m = divmod(total_min, 60)
    return f"{h}ч {m}м"


_EVENT_GROUPS = (
    ("movement", "Движение задач", frozenset({"handed_off", "to_review", "closed", "started"})),
    ("incoming", "Приход задач", frozenset({"received"})),
    ("progress", "Списания", frozenset({"progress"})),
)


def _event(
    *,
    at: str | None,
    etype: str,
    issue_key: str,
    summary: str | None,
    text: str,
    from_status: str | None = None,
    to_status: str | None = None,
    from_assignee: str | None = None,
    to_assignee: str | None = None,
    hours: float | None = None,
    web_url: str | None = None,
    action: str | None = None,
) -> dict:
    return {
        "at": at,
        "type": etype,
        "issue_key": issue_key,
        "summary": summary or issue_key,
        "from_status": from_status,
        "to_status": to_status,
        "from_assignee": from_assignee,
        "to_assignee": to_assignee,
        "hours": hours,
        "text": text,
        "action": action,
        "web_url": web_url,
        "key": issue_key,
    }


def _classify_history_for_person(
    hist: dict,
    *,
    person: str,
    direction: str | None,
    browse_base: str,
) -> dict | None:
    team_cfg = get_team_config()
    key = (hist.get("issue_key") or "").upper()
    if not key:
        return None

    summary = hist.get("issue_summary")
    at = hist.get("at")
    status_from = hist.get("status_from")
    status_to = hist.get("status_to")
    assignee_from_raw = hist.get("assignee_from")
    assignee_to_raw = hist.get("assignee_to")
    from_c = canonical_team_name(assignee_from_raw)
    to_c = canonical_team_name(assignee_to_raw)

    was_assignee = from_c == person
    became_assignee = to_c == person and from_c != person
    still_or_was = was_assignee or to_c == person
    if not still_or_was:
        return None

    web_url = f"{browse_base}/browse/{key}" if browse_base and key else None
    to_short = _short_name(to_c or assignee_to_raw)
    from_short = _short_name(from_c or assignee_from_raw)

    jira_done = _is_closed_status(status_to)
    to_dir_done = bool(status_to) and team_cfg.is_direction_done(
        direction, status_to, jira_done=jira_done
    )
    assignee_left = was_assignee and to_c is not None and to_c != person

    common = dict(
        at=at,
        issue_key=key,
        summary=summary,
        from_status=status_from,
        to_status=status_to,
        from_assignee=from_c or assignee_from_raw,
        to_assignee=to_c or assignee_to_raw,
        web_url=web_url,
    )

    # Closed — attribute to the person who owned it going into the close.
    if status_to and jira_done and (was_assignee or (to_c == person and not from_c)):
        return _event(
            etype="closed",
            text=f"Закрыта ({status_to})",
            **common,
        )

    # Hand-off: left the person's plate (direction done and/or reassigned away).
    if was_assignee and (to_dir_done or assignee_left) and not jira_done:
        if assignee_left and to_short:
            action = _verb(person, "Передал", "Передала")
            text = f"{action} → {to_short}"
            if to_dir_done and status_to:
                text = f"{action} ({status_to}) → {to_short}"
        else:
            action = _verb(person, "Перевёл дальше", "Перевела дальше")
            text = f"{action} · {status_to}" if status_to else action
        return _event(etype="handed_off", text=text, action=action, **common)

    # Review — "Ушла" refers to the task (задача), not the person.
    if status_to and _is_review_status(status_to) and was_assignee:
        action = "Ушла в ревью"
        if to_short and to_c and to_c != person:
            text = f"{action} → {to_short}"
        else:
            text = f"{action} ({status_to})"
        return _event(etype="to_review", text=text, action=action, **common)

    # Received
    if became_assignee:
        action = _verb(person, "Получил", "Получила")
        if from_short and from_c:
            text = f"{action} от {from_short}"
            if status_to:
                text += f" · {status_to}"
        elif status_to:
            text = f"{action} задачу · {status_to}"
        else:
            text = f"{action} задачу"
        return _event(etype="received", text=text, action=action, **common)

    # Started work
    if status_to and _is_started_status(status_to) and (
        was_assignee or became_assignee or to_c == person
    ):
        action = _verb(person, "Взял в работу", "Взяла в работу")
        return _event(etype="started", text=action, action=action, **common)

    return None


def _event_group_id(etype: str | None) -> str:
    for gid, _label, types in _EVENT_GROUPS:
        if etype in types:
            return gid
    return "other"


def _collapse_day_events(events: list[dict]) -> list[dict]:
    """
    Keep strongest event per issue within each group.

    Progress can coexist with movement/incoming for the same issue.
    """
    best: dict[tuple[str, str], dict] = {}
    for ev in events:
        key = (ev.get("issue_key") or "").upper()
        if not key:
            continue
        etype = ev.get("type") or ""
        slot = (key, _event_group_id(etype))
        prev = best.get(slot)
        if prev is None:
            best[slot] = ev
            continue
        p_new = _EVENT_PRIORITY.get(etype, 0)
        p_old = _EVENT_PRIORITY.get(prev.get("type") or "", 0)
        if p_new > p_old or (
            p_new == p_old and (ev.get("at") or "") > (prev.get("at") or "")
        ):
            best[slot] = ev

    ordered = sorted(
        best.values(),
        key=lambda e: (
            _EVENT_PRIORITY.get(e.get("type") or "", 0),
            e.get("at") or "",
        ),
        reverse=True,
    )

    if len(ordered) <= _MAX_EVENTS:
        return ordered
    head = ordered[:_MAX_EVENTS]
    rest = len(ordered) - _MAX_EVENTS
    head.append(
        {
            "at": None,
            "type": "more",
            "issue_key": "",
            "summary": "",
            "text": f"ещё {rest}",
            "key": "",
            "web_url": None,
        }
    )
    return head


def _group_day_events(events: list[dict]) -> list[dict]:
    """Split collapsed events into standup groups; keep order of _EVENT_GROUPS."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    more_events: list[dict] = []
    for ev in events:
        etype = ev.get("type") or ""
        if etype == "more":
            more_events.append(ev)
            continue
        by_type[etype].append(ev)

    groups: list[dict] = []
    for gid, label, types in _EVENT_GROUPS:
        chunk: list[dict] = []
        for etype in types:
            chunk.extend(by_type.get(etype) or [])
        if not chunk:
            continue
        # Preserve priority/time order from collapsed list
        rank = {id(e): i for i, e in enumerate(events)}
        chunk.sort(key=lambda e: rank.get(id(e), 999))
        groups.append({"id": gid, "label": label, "events": chunk})

    if more_events and groups:
        groups[-1]["events"].extend(more_events)
    elif more_events:
        groups.append(
            {"id": "more", "label": "Ещё", "events": more_events}
        )
    return groups


def _day_bucket(
    day: date,
    *,
    title: str,
    events: list[dict],
) -> dict:
    collapsed = _collapse_day_events(events)
    groups = _group_day_events(collapsed)
    return {
        "date": day.isoformat(),
        "label": title,
        "events": collapsed,
        "groups": groups,
        "empty": not any(e.get("type") != "more" for e in collapsed),
    }


def _enrich_changelogs_with_assignee(
    changelogs: list[dict],
    current_assignee_by_key: dict[str, str],
) -> list[dict]:
    """
    Fill assignee on status-only changelog rows by walking history newest→oldest
    from the issue's current assignee.
    """
    by_issue: dict[str, list[dict]] = defaultdict(list)
    for hist in changelogs or []:
        key = (hist.get("issue_key") or "").upper()
        if key:
            by_issue[key].append(hist)

    enriched: list[dict] = []
    for key, rows in by_issue.items():
        ordered = sorted(rows, key=lambda r: r.get("at") or "")
        # Assignee after the newest event == current issue assignee
        cur = current_assignee_by_key.get(key)
        rebuilt: list[dict] = []
        for hist in reversed(ordered):
            row = dict(hist)
            af = row.get("assignee_from")
            at_ = row.get("assignee_to")
            if af is not None or at_ is not None:
                # Step back to assignee before this change
                cur = af
            elif cur:
                row["assignee_from"] = cur
                row["assignee_to"] = cur
            rebuilt.append(row)
        enriched.extend(reversed(rebuilt))
    return enriched


def build_people_activity(
    *,
    people_names: list[str],
    changelogs: list[dict],
    hours_by_person_day_issue: dict[str, dict[str, dict[str, float]]],
    issue_summary_by_key: dict[str, str],
    browse_base: str,
    current_assignee_by_key: dict[str, str] | None = None,
    today: date | None = None,
) -> dict[str, dict]:
    """
    Build yesterday/today activity blocks keyed by roster display name.
    """
    today = today or report_today()
    yesterday = previous_workday(today)
    target_days = {yesterday, today}
    browse_base = (browse_base or "").rstrip("/")

    histories = _enrich_changelogs_with_assignee(
        changelogs or [],
        current_assignee_by_key or {},
    )

    # person -> day -> list[events]
    buckets: dict[str, dict[date, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for hist in histories:
        day = _local_day(hist.get("at"))
        if day not in target_days:
            continue
        candidates: set[str] = set()
        for raw in (hist.get("assignee_from"), hist.get("assignee_to")):
            c = canonical_team_name(raw)
            if c and c in TEAM_ROSTER:
                candidates.add(c)
        if not candidates:
            continue
        for person in candidates:
            direction = TEAM_ROSTER.get(person)
            ev = _classify_history_for_person(
                hist,
                person=person,
                direction=direction,
                browse_base=browse_base,
            )
            if ev:
                buckets[person][day].append(ev)

    # Progress from worklogs when no stronger event for that issue/day
    for person, by_day in (hours_by_person_day_issue or {}).items():
        if person not in TEAM_ROSTER:
            continue
        for day_s, by_issue in by_day.items():
            try:
                day = date.fromisoformat(day_s)
            except ValueError:
                continue
            if day not in target_days:
                continue
            for issue_key, hours in by_issue.items():
                key = (issue_key or "").upper()
                if not key or hours <= 0:
                    continue
                web_url = f"{browse_base}/browse/{key}" if browse_base else None
                action = _verb(person, "Списал", "Списала")
                duration = _fmt_hours_minutes(float(hours))
                buckets[person][day].append(
                    _event(
                        at=f"{day_s}T12:00:00+03:00",
                        etype="progress",
                        issue_key=key,
                        summary=issue_summary_by_key.get(key),
                        text=f"{action} {duration}",
                        action=action,
                        hours=round(float(hours), 2),
                        web_url=web_url,
                    )
                )

    out: dict[str, dict] = {}
    for name in people_names:
        y_events = buckets.get(name, {}).get(yesterday, [])
        t_events = buckets.get(name, {}).get(today, [])
        out[name] = {
            "yesterday": _day_bucket(
                yesterday,
                title="Вчера",
                events=y_events,
            ),
            "today": _day_bucket(
                today,
                title="Сегодня",
                events=t_events,
            ),
        }
    return out

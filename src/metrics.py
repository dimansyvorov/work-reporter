from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .activity import build_people_activity, report_today
from .linking import link_sprint_issues
from .people import best_gitlab_avatar, best_gitlab_person, collect_gitlab_people
from .ratings import compute_ratings
from .team import (
    DEV_DIRECTIONS,
    DIRECTION_ORDER,
    TEAM_ROSTER,
    canonical_team_name,
)
from .team_config import get_team_config


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _round(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _jira_person_name(person: dict | None) -> str:
    if not person:
        return "Без исполнителя"
    for key in ("displayName", "name", "emailAddress"):
        value = (person.get(key) or "").strip()
        if value:
            return value
    return "Без исполнителя"


def _jira_avatar(person: dict | None) -> str | None:
    if not person:
        return None
    urls = person.get("avatarUrls") or {}
    return urls.get("48x48") or urls.get("32x32") or urls.get("24x24")


def _jira_username(person: dict | None) -> str | None:
    if not person:
        return None
    for key in ("name", "key", "accountId"):
        value = (person.get(key) or "").strip()
        if value:
            return value
    return None


def _jira_profile_url(browse_base: str | None, username: str | None) -> str | None:
    base = (browse_base or "").rstrip("/")
    user = (username or "").strip()
    if not base or not user:
        return None
    # Jira Server/DC profile page
    from urllib.parse import quote

    return f"{base}/secure/ViewProfile.jspa?name={quote(user)}"


def _is_done(fields: dict) -> bool:
    if fields.get("resolution"):
        return True
    category = ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
    return category == "done"


def _is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def _sprint_day_progress(sprint: dict) -> tuple[int | None, int | None, float | None]:
    start = sprint.get("start_date")
    end = sprint.get("end_date")
    if not start or not end:
        return None, None, None
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    total = max((end_d - start_d).days + 1, 1)
    today = datetime.now(timezone.utc).date()
    if today < start_d:
        idx = 0
    elif today > end_d:
        idx = total
    else:
        idx = (today - start_d).days + 1
    return idx, total, _round(idx / total * 100.0)


def _daterange(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _hours_level(hours: float, expected: float, warn_ratio: float | None = None) -> str:
    if expected <= 0:
        return "ok"
    if hours >= expected:
        return "ok"
    ratio = (
        warn_ratio
        if warn_ratio is not None
        else get_team_config().metrics.hours_warn_ratio
    )
    if hours >= expected * ratio:
        return "warn"
    return "bad"


def _report_local_now() -> datetime:
    timezone_name = get_team_config().ratings.timezone
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Europe/Moscow")
    return datetime.now(tz)


def _expected_hours_at(
    day: date, *, now: datetime, expected_hours: float, start_hour: float
) -> float:
    if day < now.date():
        return expected_hours
    if day > now.date():
        return 0.0
    hour = int(start_hour)
    minute = int(round((start_hour - hour) * 60))
    if minute >= 60:
        hour += 1
        minute = 0
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo)
    elapsed = (now - start).total_seconds() / 3600.0
    return min(expected_hours, max(0.0, elapsed))


def _looks_like_default_avatar(url: str | None) -> bool:
    """Heuristic: Jira/GitLab placeholder avatars that are not real photos."""
    if not url:
        return True
    text = str(url).strip().lower()
    if not text:
        return True
    # Local cached copies are already vetted at download time
    if text.startswith("/avatars/") or text.startswith("/report/avatars/"):
        return False
    # Note: Gravatar `d=identicon` is a valid unique avatar used by GitLab — do not
    # treat it as empty. Reject only clearly empty/mystery fallbacks.
    markers = (
        "avatar-default",
        "default_avatar",
        "default-avatar",
        "/anonymous/",
        "no_avatar",
        "mystery",
        "mp.png",  # gravatar mystery person
        "d=mp",
        "d=retro",
        "d=wavatar",
        "d=monsterid",
        "d=blank",
    )
    if any(m in text for m in markers):
        return True
    # Classic empty Jira silhouette: ownerId present, but no avatarId
    if "useravatar" in text and "ownerid=" in text and "avatarid=" not in text:
        return True
    return False


def _avatar_for(name: str, jira_avatar: str | None, gitlab_people: list[dict]) -> str | None:
    team_cfg = get_team_config()
    # 1) Explicit URL override from team.json
    team_avatar = team_cfg.avatar_for(name)
    if team_avatar:
        return team_avatar

    source = team_cfg.avatar_source_for(name)  # "jira" | "gitlab" | None
    gitlab_avatar = (
        best_gitlab_avatar(name, gitlab_people)
        if source in (None, "gitlab")
        else None
    )
    jira_only = jira_avatar if source in (None, "jira") else None

    def _real(url: str | None) -> str | None:
        if url and not _looks_like_default_avatar(url):
            return url
        return None

    # Locked source: never fall back to the other platform or to placeholders
    if source == "jira":
        return _real(jira_only)
    if source == "gitlab":
        return _real(gitlab_avatar)

    # Prefer real photos; if none — leave empty (UI shows initials)
    return _real(gitlab_avatar) or _real(jira_only)


def _profile_links_for(
    name: str,
    *,
    browse_base: str | None,
    jira_username: str | None,
    gitlab_people: list[dict],
) -> dict[str, str | None]:
    team_cfg = get_team_config()
    jira_url = team_cfg.jira_profile_for(name)
    if not jira_url:
        user = team_cfg.jira_username_for(name) or jira_username
        jira_url = _jira_profile_url(browse_base, user)

    gitlab_url = team_cfg.gitlab_profile_for(name)
    if not gitlab_url:
        gl_user = team_cfg.gitlab_username_for(name)
        gl_person = best_gitlab_person(name, gitlab_people) or {}
        gitlab_url = gl_person.get("web_url") or None
        if not gitlab_url and gl_user:
            # Fallback: construct from matched host if we ever only have username
            host = None
            for person in gitlab_people:
                web = person.get("web_url") or ""
                if web.startswith("http"):
                    from urllib.parse import urlparse

                    parsed = urlparse(web)
                    if parsed.scheme and parsed.netloc:
                        host = f"{parsed.scheme}://{parsed.netloc}"
                        break
            if host:
                gitlab_url = f"{host.rstrip('/')}/{gl_user.lstrip('/')}"
    return {"jira_url": jira_url, "gitlab_url": gitlab_url}


def _estimate_hours(fields: dict) -> float | None:
    seconds = (
        fields.get("timeoriginalestimate")
        or fields.get("aggregatetimeoriginalestimate")
    )
    if seconds is None:
        return None
    return float(seconds) / 3600.0


def _remaining_estimate_hours(fields: dict) -> float | None:
    seconds = fields.get("timeestimate")
    if seconds is None:
        seconds = fields.get("aggregatetimeestimate")
    if seconds is None:
        return None
    return float(seconds) / 3600.0


def _parse_greenhopper_sprint(value: str) -> dict | None:
    """Parse Jira Server GreenHopper Sprint.toString() values from custom fields."""
    if "Sprint@" not in value and "greenhopper.service.sprint" not in value:
        return None
    sid = re.search(r"\bid=(\d+)", value)
    if not sid:
        return None
    name_m = re.search(r"\bname=([^,\]]+)", value)
    state_m = re.search(r"\bstate=([^,\]]+)", value)
    out: dict = {"id": int(sid.group(1))}
    if name_m:
        out["name"] = name_m.group(1).strip()
    if state_m:
        out["state"] = state_m.group(1).strip().lower()
    return out


def _coerce_sprint_item(item) -> dict | None:
    if isinstance(item, dict) and item.get("id") is not None:
        return item
    if isinstance(item, str):
        return _parse_greenhopper_sprint(item)
    return None


def _iter_issue_sprints(fields: dict) -> list[dict]:
    """Collect sprint links from Agile fields and GreenHopper custom fields."""
    found: list[dict] = []
    seen: set[str] = set()

    def add(item) -> None:
        sprint = _coerce_sprint_item(item)
        if not sprint:
            return
        sid = str(sprint.get("id"))
        if sid in seen:
            return
        seen.add(sid)
        found.append(sprint)

    for key in ("sprint", "closedSprints"):
        value = fields.get(key)
        if isinstance(value, list):
            for item in value:
                add(item)
        else:
            add(value)

    # REST search often omits fields.sprint and only returns gh-sprint customfield.
    for key, value in fields.items():
        if not str(key).startswith("customfield_"):
            continue
        if isinstance(value, list):
            for item in value:
                add(item)
        else:
            add(value)
    return found


def _has_sprint_delay(fields: dict, current_sprint_id) -> bool:
    sprints = _iter_issue_sprints(fields)
    if not sprints:
        return False
    ids = {str(s.get("id")) for s in sprints if s.get("id") is not None}
    if len(ids) > 1:
        return True
    if any((s.get("state") or "").lower() == "closed" for s in sprints):
        # Was in a closed sprint and still open in current → carried over
        if current_sprint_id is None:
            return True
        return any(str(s.get("id")) != str(current_sprint_id) for s in sprints) or any(
            (s.get("state") or "").lower() == "closed" for s in sprints
        )
    if current_sprint_id is not None:
        return any(str(s.get("id")) != str(current_sprint_id) for s in sprints)
    return False


def _issue_in_sprint(fields: dict, current_sprint_id) -> bool:
    """True if issue is linked to the current sprint (active sprint field)."""
    if current_sprint_id is None:
        return True
    sid = str(current_sprint_id)
    for sprint in _iter_issue_sprints(fields):
        if str(sprint.get("id")) == sid and (sprint.get("state") or "").lower() != "closed":
            return True
    # Some payloads only put current sprint into fields.sprint without state
    for sprint in _iter_issue_sprints(fields):
        if str(sprint.get("id")) == sid:
            return True
    return False


def _working_days_between(start: date, end: date) -> int:
    if end < start:
        return 0
    n = 0
    cur = start
    while cur <= end:
        if not _is_weekend(cur):
            n += 1
        cur += timedelta(days=1)
    return n


def _effective_load_hours(task: dict) -> tuple[float, str]:
    """Use only an explicit positive Jira Remaining Estimate for future load."""
    rem = task.get("remaining_hours")
    if rem is not None and float(rem) > 0:
        return float(rem), "jira"
    return 0.0, "zero" if rem is not None else "missing"


def _build_person_load(
    *,
    active_tasks: list[dict],
    sprint: dict,
    expected_hours: float,
    today: date,
    hours_sprint: float = 0.0,
) -> dict:
    """
    Forward-looking load: remaining active plate vs leftover personal sprint budget.

    load_pct = remaining_hours / max(0, total_sprint_capacity − hours_sprint) × 100
    where total_sprint_capacity = workdays(start, end) × expected_hours.

    Active tasks with zero/missing Jira remaining do not invent future hours.
    """
    expected = float(expected_hours or 8.0)
    logged = max(float(hours_sprint or 0.0), 0.0)

    remain = 0.0
    with_remaining = 0
    with_zero_remaining = 0
    without_remaining = 0
    for task in active_tasks:
        hours, source = _effective_load_hours(task)
        remain += max(hours, 0.0)
        if source == "jira":
            with_remaining += 1
        elif source == "zero":
            with_zero_remaining += 1
        else:
            without_remaining += 1

    start_d = (
        date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else None
    )
    end_d = (
        date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    )
    days_left = _working_days_between(today, end_d) if end_d else 0
    sprint_workdays = (
        _working_days_between(start_d, end_d) if start_d and end_d else 0
    )
    total_capacity = sprint_workdays * expected
    capacity = max(0.0, total_capacity - logged)
    load_pct = None
    if capacity > 0:
        load_pct = _round((remain / capacity) * 100.0, 0)

    if not active_tasks:
        level = "empty"
    elif load_pct is None:
        level = "unknown"
    elif load_pct < 80:
        level = "ok"
    elif load_pct <= 110:
        level = "tight"
    else:
        level = "over"

    return {
        "remaining_hours": _round(remain, 1) or 0.0,
        "capacity_hours": _round(capacity, 1) or 0.0,
        "total_capacity_hours": _round(total_capacity, 1) or 0.0,
        "hours_sprint": _round(logged, 1) or 0.0,
        "days_left": days_left,
        "sprint_workdays": sprint_workdays,
        "expected_hours_per_day": expected,
        "load_pct": load_pct,
        "level": level,
        "active_tasks": len(active_tasks),
        "tasks_with_remaining": with_remaining,
        "tasks_with_zero_remaining": with_zero_remaining,
        "tasks_without_remaining": without_remaining,
    }


def _release_version_tags(fields: dict) -> list[dict]:
    tags: list[dict] = []
    seen: set[str] = set()
    for item in fields.get("fixVersions") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        released = bool(item.get("released"))
        vid = str(item.get("id") or "").strip() or None
        release_date = str(item.get("releaseDate") or "").strip() or None
        tags.append(
            {
                "id": "release",
                "label": name,
                "tone": "release",
                "release_id": vid,
                "release_date": release_date,
                "released": released,
                "hint": (
                    f"Fix Version «{name}»"
                    + (" · уже выпущена в Jira" if released else " · ещё не выпущена")
                ),
            }
        )
    return tags


def _risk_release_meta(fields: dict, sprint: dict) -> dict:
    """Classify unreleased Fix Versions relative to the current sprint window."""
    start = date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else None
    end = date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    versions = []
    outside = False
    without_date = False
    for item in fields.get("fixVersions") or []:
        if not isinstance(item, dict):
            continue
        raw_date = str(item.get("releaseDate") or "").strip()
        released = bool(item.get("released"))
        versions.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "name": str(item.get("name") or "").strip() or None,
                "release_date": raw_date or None,
                "released": released,
            }
        )
        if released:
            continue
        if not raw_date:
            without_date = True
            continue
        try:
            release_day = date.fromisoformat(raw_date[:10])
        except ValueError:
            without_date = True
            continue
        if start and end and (release_day < start or release_day > end):
            outside = True
    return {
        "release_outside_sprint": outside,
        "release_without_date": without_date,
        "fix_versions": versions,
    }


def _has_risk_tags(tags: list[dict] | None) -> bool:
    return any((tag or {}).get("id") != "release" for tag in (tags or []))


def _build_task_tags(
    *,
    fields: dict,
    direction: str,
    direction_state: str,
    sprint_id,
    today: date,
    end_d: date | None,
    expected_hours: float,
    inactive_days: int,
) -> list[dict]:
    tags: list[dict] = []
    tags.extend(_release_version_tags(fields))
    # Auxiliary tasks (display_task_filters) stay out of «Без релиза» risk.
    if (
        direction_state == "active"
        and not any(t.get("id") == "release" for t in tags)
        and not get_team_config().is_hidden_from_display(fields.get("summary"))
    ):
        tags.append(
            {
                "id": "no_release",
                "label": "Без релиза",
                "tone": "warn",
                "hint": "У задачи нет Fix Version — не привязана к релизу",
            }
        )
    if _has_sprint_delay(fields, sprint_id):
        tags.append(
            {
                "id": "delay",
                "label": "Задержка",
                "tone": "warn",
                "hint": "Задача связана не только с текущим спринтом (есть предыдущий/другой спринт)",
            }
        )

    updated = _parse_dt(fields.get("updated"))
    if (
        direction_state == "active"
        and updated
        and (today - updated.date()).days >= inactive_days
    ):
        days = (today - updated.date()).days
        day_word = "день" if days == 1 else "дня" if 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14 else "дней"
        tags.append(
            {
                "id": "inactive",
                "label": f"Неактивная {days} {day_word}",
                "tone": "muted",
                "hint": f"Нет обновлений {days} дн. (порог {inactive_days} дн.) при активном статусе направления",
                "inactive_days": days,
            }
        )

    # Risk: remaining estimate > remaining sprint capacity.
    # Skip if direction already done (e.g. Ready for testing for dev → remaining often 0/auto).
    rem = _remaining_estimate_hours(fields)
    if (
        direction_state == "active"
        and rem is not None
        and rem > 0
        and end_d is not None
    ):
        days_left = _working_days_between(today, end_d)
        capacity = days_left * expected_hours
        if rem > capacity:
            tags.append(
                {
                    "id": "at_risk",
                    "label": "Риск не успеть",
                    "tone": "danger",
                    "hint": (
                        f"Оставшаяся оценка {rem:.1f} ч больше ёмкости до конца спринта "
                        f"({days_left} раб. дн. × {expected_hours:.0f} ч = {capacity:.1f} ч). "
                        "Не ставится при done-статусе направления и нулевой оценке"
                    ),
                }
            )
    return tags


def _redistribute_section_widths(
    raw_shares: list[float], *, min_pct: float = 14.0
) -> list[float]:
    """Ensure each section has a readable minimum width, then renormalize to 100%."""
    if not raw_shares:
        return []
    n = len(raw_shares)
    floor = min(min_pct, 100.0 / n)
    widths = [max(s * 100.0, floor) for s in raw_shares]
    total = sum(widths) or 1.0
    return [w / total * 100.0 for w in widths]


def _annotate_issue_for_metrics(issue: dict) -> dict | None:
    """Attach roster assignee/direction; return a shallow copy or None if not in team."""
    fields = issue.get("fields") or {}
    assignee_name = _jira_person_name(fields.get("assignee"))
    canonical = canonical_team_name(assignee_name)
    if not canonical:
        return None
    out = dict(issue)
    out["_canonical_assignee"] = canonical
    out["_assignee_display"] = assignee_name
    out["_direction"] = TEAM_ROSTER[canonical]
    return out


def _build_epic_timeline(
    *,
    issues: list[dict],
    epics_meta: dict,
    sprint: dict,
    hours_by_issue: dict[str, float],
    browse_base: str,
    expected_hours: float = 8.0,
    release_epic_keys: list[str] | None = None,
    epic_scope_issues: list[dict] | None = None,
    sprint_issue_keys: set[str] | None = None,
) -> dict:
    """
    Epics as sausages split by all team directions.

    When report releases exist: only epics linked to those versions, progress from
    the full epic scope (not sprint-only tasks).
    Otherwise: sprint focus — epics with direction-active sprint work.
    """
    team_cfg = get_team_config()
    sprint_start = (
        date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else None
    )
    sprint_end = (
        date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    )
    if not sprint_start or not sprint_end:
        return {
            "range_start": None,
            "range_end": None,
            "epics": [],
            "focus": "active_sprint",
            "omitted_count": 0,
        }

    allowed = {(k or "").upper() for k in (release_epic_keys or []) if k}
    use_release_scope = bool(allowed)
    source_issues: list[dict] = []
    focus = "active_sprint"
    if use_release_scope:
        for raw in epic_scope_issues or []:
            annotated = _annotate_issue_for_metrics(raw)
            if annotated:
                source_issues.append(annotated)
        focus = "release_window"
        # Full epic fetch can fail/return empty; fall back to sprint issues
        # already linked to release epics so the timeline does not disappear.
        if not source_issues:
            for issue in issues:
                ek = (issue.get("_epic_key") or "").upper()
                if ek not in allowed:
                    continue
                if issue.get("_direction"):
                    source_issues.append(issue)
                else:
                    annotated = _annotate_issue_for_metrics(issue)
                    if annotated:
                        source_issues.append(annotated)
            focus = "release_window_sprint"
    else:
        source_issues = list(issues)
        focus = "active_sprint"

    # epic_key -> direction_name -> aggregates
    buckets: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "estimate": 0.0,
                "spent": 0.0,
                "tasks": 0,
                "done_tasks": 0,
                "active_tasks": 0,
            }
        )
    )
    epic_meta_local: dict[str, dict] = {}
    epic_has_active: set[str] = set()
    tasks_by_epic: dict[str, list[dict]] = defaultdict(list)
    today = datetime.now(timezone.utc).date()

    for issue in source_issues:
        fields = issue.get("fields") or {}
        epic_key = (issue.get("_epic_key") or "").upper()
        if not epic_key:
            continue
        if use_release_scope and epic_key not in allowed:
            continue
        direction = issue.get("_direction")
        if not direction:
            continue
        key = (issue.get("key") or "").upper()
        estimate = _estimate_hours(fields)
        # fallback unit so zero-estimate tasks still create a visible section
        est = float(estimate) if estimate and estimate > 0 else 1.0
        spent = float(hours_by_issue.get(key, 0.0))
        if spent <= 0:
            spent = float(fields.get("timespent") or 0) / 3600.0
        status_name = ((fields.get("status") or {}).get("name"))
        kind = team_cfg.classify_status(
            direction, status_name, jira_done=_is_done(fields)
        )
        rem = _remaining_estimate_hours(fields)
        tags = _build_task_tags(
            fields=fields,
            direction=direction,
            direction_state=kind,
            sprint_id=sprint.get("id"),
            today=today,
            end_d=(
                date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
            ),
            expected_hours=expected_hours,
            inactive_days=team_cfg.inactive_days,
        )
        in_sprint = _issue_in_sprint(fields, sprint.get("id")) or (
            bool(sprint_issue_keys) and key in sprint_issue_keys
        )
        if not in_sprint:
            tags = [
                *tags,
                {
                    "id": "out_of_sprint",
                    "label": "Не в спринте",
                    "tone": "muted",
                    "hint": "Задача связана с эпиком, но не входит в текущий спринт отчёта",
                },
            ]
        agg = buckets[epic_key][direction]
        agg["estimate"] += est
        agg["spent"] += spent
        agg["tasks"] += 1
        if kind == "done":
            agg["done_tasks"] += 1
        elif kind == "active":
            agg["active_tasks"] += 1
            epic_has_active.add(epic_key)

        version_names = []
        version_ids = []
        for ver in fields.get("fixVersions") or []:
            if not isinstance(ver, dict):
                continue
            vid = str(ver.get("id") or "").strip()
            vname = (ver.get("name") or "").strip()
            if vid:
                version_ids.append(vid)
            if vname:
                version_names.append(vname)

        summary = fields.get("summary") or key
        task_row = {
            "key": key,
            "summary": summary,
            "status": status_name,
            "status_category": (
                ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
            ),
            "direction": direction,
            "direction_state": kind,
            "assignee": issue.get("_canonical_assignee")
            or _jira_person_name(fields.get("assignee")),
            "estimate_hours": _round(estimate, 2) if estimate is not None else None,
            "remaining_hours": _round(rem, 2) if rem is not None else None,
            "hours": _round(spent, 2) or 0.0,
            "web_url": f"{browse_base}/browse/{key}" if browse_base and key else None,
            "epic_key": epic_key,
            "tags": tags,
            "risk": _has_risk_tags(tags),
            "hidden_from_display": team_cfg.is_hidden_from_display(summary),
            "in_sprint": in_sprint,
            "version_ids": version_ids,
            "version_names": version_names,
            "updated": fields.get("updated"),
            "created": fields.get("created"),
        }
        tasks_by_epic[epic_key].append(task_row)

        meta_summary = (epics_meta.get(epic_key) or {}).get("summary")
        issue_summary = issue.get("_epic_summary")
        # Prefer real epic title; ignore placeholders equal to the key
        best_summary = None
        for candidate in (meta_summary, issue_summary):
            if not candidate:
                continue
            if str(candidate).strip().upper() == epic_key:
                continue
            best_summary = candidate
            break
        if epic_key not in epic_meta_local:
            epic_meta_local[epic_key] = {
                "summary": best_summary or meta_summary or issue_summary or epic_key,
                "created": _parse_dt(fields.get("created")),
                "updated": _parse_dt(fields.get("updated")),
            }
        elif best_summary and (
            not epic_meta_local[epic_key].get("summary")
            or str(epic_meta_local[epic_key].get("summary") or "").strip().upper()
            == epic_key
        ):
            epic_meta_local[epic_key]["summary"] = best_summary
        created = _parse_dt(fields.get("created"))
        updated = _parse_dt(fields.get("updated"))
        if created and (
            epic_meta_local[epic_key]["created"] is None
            or created < epic_meta_local[epic_key]["created"]
        ):
            epic_meta_local[epic_key]["created"] = created
        if updated and (
            epic_meta_local[epic_key].get("updated") is None
            or updated > epic_meta_local[epic_key]["updated"]
        ):
            epic_meta_local[epic_key]["updated"] = updated

    direction_order = [
        team_cfg.directions[k].name for k in team_cfg.direction_keys_order
    ]

    epics_out = []
    candidate_keys = allowed if use_release_scope else set(buckets.keys())
    for epic_key in candidate_keys:
        by_dir = buckets.get(epic_key) or {}
        if not by_dir:
            continue
        # Without release scope keep sprint-active filter
        if not use_release_scope and epic_key not in epic_has_active:
            continue
        ordered = [(d, by_dir[d]) for d in direction_order if d in by_dir]
        if not ordered:
            continue
        total_estimate = sum(agg["estimate"] for _, agg in ordered) or 0.0
        if total_estimate <= 0:
            continue
        raw_shares = [agg["estimate"] / total_estimate for _, agg in ordered]
        widths = _redistribute_section_widths(
            raw_shares, min_pct=team_cfg.metrics.epic_section_min_pct
        )
        sections = []
        for (direction, agg), width_pct in zip(ordered, widths):
            closed_pct = (
                (agg["done_tasks"] / agg["tasks"] * 100.0) if agg["tasks"] else 0.0
            )
            # Fill and label use the same metric: % closed tasks for the direction
            closed_pct = max(0.0, min(100.0, closed_pct))
            dir_tasks = [
                t
                for t in (tasks_by_epic.get(epic_key) or [])
                if t.get("direction") == direction
            ]
            open_detail = sorted(
                [t for t in dir_tasks if t.get("direction_state") != "done"],
                key=lambda t: (
                    0 if t.get("in_sprint", True) else 1,
                    0 if t.get("direction_state") == "active" else 1,
                    0 if t.get("risk") else 1,
                    t.get("key") or "",
                ),
            )
            sections.append(
                {
                    "direction": direction,
                    "direction_key": team_cfg.resolve_direction_key(direction),
                    "color": team_cfg.direction_color(direction),
                    "estimate_hours": _round(agg["estimate"], 1) or 0.0,
                    "spent_hours": _round(agg["spent"], 1) or 0.0,
                    "tasks": agg["tasks"],
                    "done_tasks": agg["done_tasks"],
                    "active_tasks": agg["active_tasks"],
                    "open_tasks": len(open_detail),
                    "flex_grow": _round(agg["estimate"], 2) or 1.0,
                    "width_pct": _round(width_pct, 2) or 0.0,
                    "progress_pct": _round(closed_pct, 0) or 0.0,
                    "closed_pct": _round(closed_pct, 0) or 0.0,
                    "tasks_detail": open_detail[:30],
                }
            )

        meta = epics_meta.get(epic_key) or {}
        local = epic_meta_local.get(epic_key) or {}
        total_tasks = sum(s["tasks"] for s in sections)
        total_done = sum(s["done_tasks"] for s in sections)
        total_active = sum(s["active_tasks"] for s in sections)
        overall = (total_done / total_tasks * 100.0) if total_tasks else 0.0
        created_dt = local.get("created")
        updated_dt = local.get("updated")
        created_day = created_dt.date() if created_dt else None
        updated_day = updated_dt.date() if updated_dt else None
        age_days = (today - created_day).days if created_day else None
        span_days = (
            (updated_day - created_day).days
            if created_day and updated_day
            else age_days
        )
        all_tasks = tasks_by_epic.get(epic_key) or []
        open_tasks = [t for t in all_tasks if t.get("direction_state") != "done"]
        risk_tasks = [t for t in open_tasks if t.get("risk")]
        version_ids = sorted(
            {
                vid
                for t in all_tasks
                for vid in (t.get("version_ids") or [])
                if vid
            }
        )
        version_names = sorted(
            {
                name
                for t in all_tasks
                for name in (t.get("version_names") or [])
                if name
            }
        )
        epic_summary = None
        for candidate in (meta.get("summary"), local.get("summary")):
            if candidate and str(candidate).strip().upper() != epic_key:
                epic_summary = candidate
                break
        epic_summary = epic_summary or meta.get("summary") or local.get("summary") or epic_key

        epics_out.append(
            {
                "key": epic_key,
                "summary": epic_summary,
                "web_url": f"{browse_base}/browse/{epic_key}" if browse_base else None,
                "estimate_hours": _round(total_estimate, 1) or 0.0,
                "spent_hours": _round(
                    sum(float(t.get("hours") or 0) for t in all_tasks), 1
                )
                or 0.0,
                "progress_pct": _round(overall, 0) or 0.0,
                "tasks_total": total_tasks,
                "tasks_done": total_done,
                "tasks_active": total_active,
                "tasks_open": len(open_tasks),
                "tasks_risk": len(risk_tasks),
                "created": created_day.isoformat() if created_day else None,
                "updated": updated_day.isoformat() if updated_day else None,
                "age_days": age_days,
                "span_days": span_days,
                "version_ids": version_ids,
                "version_names": version_names,
                "has_release": bool(version_ids or version_names),
                "sections": sections,
                "open_task_list": sorted(
                    open_tasks,
                    key=lambda t: (
                        0 if t.get("in_sprint", True) else 1,
                        0 if t.get("direction_state") == "active" else 1,
                        0 if t.get("risk") else 1,
                        t.get("direction") or "",
                        t.get("key") or "",
                    ),
                )[:60],
                "releases": [],
                "conflicts": [],
                "scope": (
                    "full_epic"
                    if focus == "release_window"
                    else "sprint"
                ),
                "is_epic": True,
            }
        )

    max_estimate = max((e["estimate_hours"] for e in epics_out), default=0.0) or 1.0
    bar_min = team_cfg.metrics.epic_bar_min_pct
    for epic in epics_out:
        epic["bar_width_pct"] = _round(
            max(bar_min, (epic["estimate_hours"] / max_estimate) * 100.0), 1
        )

    # If release-scoped filter still yielded nothing, rebuild on sprint-active epics
    if use_release_scope and not epics_out and issues:
        return _build_epic_timeline(
            issues=issues,
            epics_meta=epics_meta,
            sprint=sprint,
            hours_by_issue=hours_by_issue,
            browse_base=browse_base,
            expected_hours=expected_hours,
            release_epic_keys=None,
            epic_scope_issues=None,
        )

    # Tentative order; final sort after release enrichment (has_release + progress)
    epics_out.sort(
        key=lambda e: (
            0 if e.get("has_release") else 1,
            float(e.get("progress_pct") or 0),
            e.get("key") or "",
        )
    )
    omitted = max(0, len(buckets) - len(epics_out)) if not use_release_scope else 0
    return {
        "legend": [
            {
                "key": k,
                "name": team_cfg.directions[k].name,
                "color": team_cfg.directions[k].color,
            }
            for k in team_cfg.direction_keys_order
        ],
        "epics": epics_out,
        "focus": focus,
        "omitted_count": omitted,
        "sort": {
            "group_by": "has_release",
            "within_group": [
                "nearest_release_date_asc",
                "release_link_strength_desc",
                "progress_pct_asc",
            ],
        },
    }


def _enrich_epics_with_releases(epic_timeline: dict, releases: list[dict]) -> None:
    """Attach release links + conflict hints to epic timeline cards."""
    epics = epic_timeline.get("epics") or []
    if not epics:
        return
    by_id = {str(r.get("id") or ""): r for r in (releases or []) if r.get("id") is not None}
    by_name = {
        str(r.get("name") or "").strip().lower(): r
        for r in (releases or [])
        if (r.get("name") or "").strip()
    }

    for epic in epics:
        linked: list[dict] = []
        seen: set[str] = set()
        for vid in epic.get("version_ids") or []:
            rel = by_id.get(str(vid))
            if rel and str(rel.get("id")) not in seen:
                seen.add(str(rel.get("id")))
                linked.append(rel)
        if not linked:
            for vname in epic.get("version_names") or []:
                rel = by_name.get(str(vname).strip().lower())
                if rel and str(rel.get("id")) not in seen:
                    seen.add(str(rel.get("id")))
                    linked.append(rel)

        epic["releases"] = [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "release_date": r.get("release_date"),
                "released": bool(r.get("released")),
                "risk": r.get("risk"),
                "risk_label": r.get("risk_label"),
                "progress_pct": r.get("progress_pct"),
                "days_left": r.get("days_left"),
                "tasks_active": r.get("tasks_active"),
            }
            for r in linked
        ]

        conflicts: list[dict] = []
        unreleased = [r for r in linked if not r.get("released")]
        if len(unreleased) > 1:
            names = ", ".join(r.get("name") or "?" for r in unreleased[:4])
            conflicts.append(
                {
                    "id": "multi_release",
                    "severity": "warn",
                    "title": "Несколько невыпущенных релизов",
                    "summary": (
                        f"Эпик одновременно в {len(unreleased)} версиях: {names}. "
                        "Возможны пересечения сроков и приоритетов."
                    ),
                }
            )

        # Overlapping unreleased windows (by release_date proximity)
        dated = [
            r
            for r in unreleased
            if r.get("release_date")
        ]
        dated.sort(key=lambda r: r.get("release_date") or "")
        for i in range(len(dated) - 1):
            a, b = dated[i], dated[i + 1]
            try:
                da = date.fromisoformat(a["release_date"])
                db = date.fromisoformat(b["release_date"])
            except ValueError:
                continue
            gap = abs((db - da).days)
            if gap <= 14:
                conflicts.append(
                    {
                        "id": f"overlap_{a.get('id')}_{b.get('id')}",
                        "severity": "warn",
                        "title": "Близкие даты релизов",
                        "summary": (
                            f"«{a.get('name')}» ({a.get('release_date')}) и "
                            f"«{b.get('name')}» ({b.get('release_date')}) "
                            f"в пределах {gap} дн. — риск конкуренции за ёмкость."
                        ),
                    }
                )

        for r in linked:
            if r.get("risk") in {"at_risk", "overdue"}:
                conflicts.append(
                    {
                        "id": f"release_risk_{r.get('id')}",
                        "severity": "danger" if r.get("risk") == "overdue" else "warn",
                        "title": f"Риск релиза «{r.get('name')}»",
                        "summary": (
                            f"{r.get('risk_label') or r.get('risk')}: "
                            f"прогресс {r.get('progress_pct')}%, "
                            f"активных {r.get('tasks_active')}"
                        ),
                    }
                )
            if r.get("released") and (epic.get("tasks_active") or 0) > 0:
                conflicts.append(
                    {
                        "id": f"released_leftover_{r.get('id')}",
                        "severity": "warn",
                        "title": "Релиз выпущен, эпик ещё активен",
                        "summary": (
                            f"Версия «{r.get('name')}» уже released, "
                            f"но в эпике осталось {epic.get('tasks_active')} active-задач."
                        ),
                    }
                )

        if (epic.get("tasks_risk") or 0) > 0:
            conflicts.append(
                {
                    "id": "task_tags",
                    "severity": "warn",
                    "title": "Задачи с тегами риска",
                    "summary": (
                        f"{epic.get('tasks_risk')} задач эпика с задержкой / "
                        "неактивностью / риском не успеть."
                    ),
                }
            )

        if (epic.get("age_days") or 0) >= 90 and (epic.get("tasks_active") or 0) > 0:
            conflicts.append(
                {
                    "id": "long_running",
                    "severity": "warn",
                    "title": "Долгоживущий эпик",
                    "summary": (
                        f"Эпик тянется уже {epic.get('age_days')} дн. "
                        f"с {epic.get('tasks_active')} active-задачами."
                    ),
                }
            )

        # de-dupe by id
        uniq = {}
        for item in conflicts:
            uniq[item["id"]] = item
        epic["conflicts"] = list(uniq.values())
        epic["has_release"] = bool(epic.get("releases"))

        # Nearest release date (unreleased first, else any) — aligns with release list order.
        date_candidates: list[str] = []
        for r in epic.get("releases") or []:
            if r.get("released"):
                continue
            raw = str(r.get("release_date") or "").strip()
            if raw:
                date_candidates.append(raw[:10])
        if not date_candidates:
            for r in epic.get("releases") or []:
                raw = str(r.get("release_date") or "").strip()
                if raw:
                    date_candidates.append(raw[:10])
        epic["nearest_release_date"] = min(date_candidates) if date_candidates else None

        # How strongly epic tasks bind to report releases (open tasks with Fix Version).
        report_ids = {
            str(r.get("id"))
            for r in (releases or [])
            if r.get("id") is not None and str(r.get("id")).strip()
        }
        report_names = {
            str(r.get("name") or "").strip().lower()
            for r in (releases or [])
            if str(r.get("name") or "").strip()
        }
        task_rows: list[dict] = []
        seen_task_keys: set[str] = set()
        for t in epic.get("open_task_list") or []:
            if not isinstance(t, dict):
                continue
            tk = str(t.get("key") or "").upper()
            if not tk or tk in seen_task_keys:
                continue
            seen_task_keys.add(tk)
            task_rows.append(t)
        for section in epic.get("sections") or []:
            for t in section.get("tasks_detail") or []:
                if not isinstance(t, dict):
                    continue
                tk = str(t.get("key") or "").upper()
                if not tk or tk in seen_task_keys:
                    continue
                seen_task_keys.add(tk)
                task_rows.append(t)
        linked_open = 0
        for t in task_rows:
            vids = {str(x) for x in (t.get("version_ids") or []) if x}
            vnames = {
                str(x).strip().lower() for x in (t.get("version_names") or []) if x
            }
            if (vids & report_ids) or (vnames & report_names):
                linked_open += 1
        open_n = int(epic.get("tasks_open") or 0) or len(task_rows)
        if open_n <= 0 and epic.get("has_release"):
            strength = 1.0
        elif open_n <= 0:
            strength = 0.0
        else:
            strength = linked_open / float(open_n)
        epic["release_linked_open_tasks"] = linked_open
        epic["release_link_strength"] = _round(strength, 3) or 0.0

    epics = epic_timeline.get("epics") or []
    epics.sort(
        key=lambda e: (
            0 if e.get("has_release") else 1,
            e.get("nearest_release_date") or "9999-12-31",
            -float(e.get("release_link_strength") or 0.0),
            float(e.get("progress_pct") or 0),
            e.get("key") or "",
        )
    )
    epic_timeline["epics"] = epics
    epic_timeline["sort"] = {
        "group_by": "has_release",
        "within_group": [
            "nearest_release_date_asc",
            "release_link_strength_desc",
            "progress_pct_asc",
        ],
    }


def _issue_fix_version_ids(fields: dict) -> list[str]:
    ids: list[str] = []
    for item in fields.get("fixVersions") or []:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("id") or "").strip()
        if vid:
            ids.append(vid)
    return ids


def _build_releases(
    *,
    releases_meta: list[dict],
    release_issues: list[dict],
    sprint: dict,
    expected_hours: float,
    browse_base: str,
) -> list[dict]:
    """Release cards: direction progress + slip risk vs calendar to releaseDate."""
    if not releases_meta:
        return []

    team_cfg = get_team_config()
    today = datetime.now(timezone.utc).date()
    sprint_start = (
        date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else today
    )
    sprint_end = (
        date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    )
    sprint_id = sprint.get("id")

    # Annotate release issues with roster direction (best-effort)
    annotated: list[dict] = []
    for issue in release_issues:
        fields = issue.get("fields") or {}
        assignee_name = _jira_person_name(fields.get("assignee"))
        canonical = canonical_team_name(assignee_name)
        direction = TEAM_ROSTER.get(canonical) if canonical else None
        annotated.append(
            {
                "issue": issue,
                "fields": fields,
                "canonical": canonical,
                "display": assignee_name or canonical,
                "direction": direction,
                "version_ids": _issue_fix_version_ids(fields),
            }
        )

    direction_order = [
        team_cfg.directions[k].name for k in team_cfg.direction_keys_order
    ]
    out: list[dict] = []

    for meta in sorted(
        releases_meta,
        key=lambda r: (r.get("release_date") or "", r.get("name") or ""),
    ):
        vid = str(meta.get("id") or "")
        release_day = (
            date.fromisoformat(meta["release_date"])
            if meta.get("release_date")
            else None
        )
        if not vid or not release_day:
            continue

        by_dir: dict[str, dict] = defaultdict(
            lambda: {
                "tasks": 0,
                "done_tasks": 0,
                "active_tasks": 0,
                "estimate": 0.0,
                "active_estimate": 0.0,
            }
        )
        total = done = active = 0
        estimate = active_estimate = 0.0
        version_tasks: list[dict] = []
        tasks_by_dir: dict[str, list[dict]] = defaultdict(list)

        for row in annotated:
            if vid not in row["version_ids"]:
                continue
            direction = row["direction"]
            if not direction:
                continue
            fields = row["fields"]
            kind = team_cfg.classify_status(
                direction,
                ((fields.get("status") or {}).get("name")),
                jira_done=_is_done(fields),
            )
            est = _estimate_hours(fields) or 0.0
            if est <= 0:
                est = 1.0 if kind != "other" else 0.0
            agg = by_dir[direction]
            agg["tasks"] += 1
            agg["estimate"] += est
            total += 1
            estimate += est
            if kind == "done":
                agg["done_tasks"] += 1
                done += 1
            elif kind == "active":
                agg["active_tasks"] += 1
                agg["active_estimate"] += est
                active += 1
                active_estimate += est
            key = (row["issue"].get("key") or "").upper()
            tags = _build_task_tags(
                fields=fields,
                direction=direction,
                direction_state=kind,
                sprint_id=sprint_id,
                today=today,
                end_d=release_day,
                expected_hours=expected_hours,
                inactive_days=team_cfg.inactive_days,
            )
            summary = fields.get("summary") or key
            task_row = {
                "key": key,
                "summary": summary,
                "status": ((fields.get("status") or {}).get("name")),
                "status_category": (
                    ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
                ),
                "direction": direction,
                "direction_state": kind,
                "assignee": row.get("display") or row.get("canonical"),
                "assignee_canonical": row.get("canonical"),
                "estimate_hours": _round(_estimate_hours(fields), 2),
                "hours": _round(float(fields.get("timespent") or 0) / 3600.0, 2) or 0.0,
                "web_url": f"{browse_base}/browse/{key}" if browse_base and key else None,
                "epic_key": row["issue"].get("_epic_key"),
                "tags": tags,
                "risk": _has_risk_tags(tags),
                "hidden_from_display": team_cfg.is_hidden_from_display(summary),
            }
            version_tasks.append(task_row)
            tasks_by_dir[direction].append(task_row)

        sections = []
        for direction in direction_order:
            if direction not in by_dir:
                continue
            agg = by_dir[direction]
            closed_pct = (
                (agg["done_tasks"] / agg["tasks"] * 100.0) if agg["tasks"] else 0.0
            )
            sections.append(
                {
                    "direction": direction,
                    "direction_key": team_cfg.resolve_direction_key(direction),
                    "color": team_cfg.direction_color(direction),
                    "tasks": agg["tasks"],
                    "done_tasks": agg["done_tasks"],
                    "active_tasks": agg["active_tasks"],
                    "estimate_hours": _round(agg["estimate"], 1) or 0.0,
                    "active_estimate_hours": _round(agg["active_estimate"], 1) or 0.0,
                    "progress_pct": _round(closed_pct, 0) or 0.0,
                }
            )

        progress_pct = (done / total * 100.0) if total else 0.0
        window_days = max((release_day - sprint_start).days, 1)
        elapsed_days = max(min((today - sprint_start).days, window_days), 0)
        time_pct = (elapsed_days / window_days * 100.0) if window_days else 0.0
        days_left = (release_day - today).days
        workdays_left = (
            _working_days_between(today, release_day) if days_left >= 0 else 0
        )
        # Rough capacity: active assignees across release × workdays × expected
        active_people = {
            row["canonical"]
            for row in annotated
            if vid in row["version_ids"]
            and row["canonical"]
            and row["direction"]
            and team_cfg.classify_status(
                row["direction"],
                ((row["fields"].get("status") or {}).get("name")),
                jira_done=_is_done(row["fields"]),
            )
            == "active"
        }
        capacity = workdays_left * expected_hours * max(len(active_people), 1)
        slip_gap = time_pct - progress_pct
        slip_tol = team_cfg.metrics.slip_tolerance_pp
        slip = progress_pct + slip_tol < time_pct
        overload = active_estimate > capacity and days_left >= 0 and active > 0
        released = bool(meta.get("released"))
        risk_reasons: list[str] = []
        risk_items: list[dict] = []

        tagged = sum(
            1
            for t in version_tasks
            if any(tag.get("id") != "release" for tag in (t.get("tags") or []))
        )
        for section in sections:
            section["tasks_detail"] = sorted(
                tasks_by_dir.get(section["direction"]) or [],
                key=lambda t: (
                    0 if t.get("direction_state") == "active" else 1,
                    -(
                        len(
                            [
                                tag
                                for tag in (t.get("tags") or [])
                                if tag.get("id") != "release"
                            ]
                        )
                    ),
                    t.get("key") or "",
                ),
            )
            section["lag_pp"] = _round(time_pct - (section.get("progress_pct") or 0), 0)
            section["is_lagging"] = bool(
                (section.get("progress_pct") or 0) + slip_tol < time_pct
                and (section.get("active_tasks") or 0) > 0
            )

        lagging_dirs = [s for s in sections if s.get("is_lagging")]
        risk_tasks = [
            t
            for t in version_tasks
            if t.get("direction_state") == "active"
            and any(tag.get("id") != "release" for tag in (t.get("tags") or []))
        ][:8]
        overdue_active = [
            t for t in version_tasks if t.get("direction_state") == "active"
        ][:8]

        def _task_refs(tasks: list[dict]) -> list[dict]:
            return [
                {
                    "key": t.get("key"),
                    "summary": t.get("summary"),
                    "direction": t.get("direction"),
                    "assignee": t.get("assignee"),
                    "web_url": t.get("web_url"),
                    "tags": t.get("tags") or [],
                }
                for t in tasks
            ]

        def _dir_refs(dirs: list[dict]) -> list[dict]:
            return [
                {
                    "direction": s.get("direction"),
                    "color": s.get("color"),
                    "progress_pct": s.get("progress_pct"),
                    "active_tasks": s.get("active_tasks"),
                    "lag_pp": s.get("lag_pp"),
                }
                for s in dirs
            ]

        if released and active == 0:
            risk = "done"
            risk_label = "Выпущен"
        elif days_left < 0 and active > 0:
            risk = "overdue"
            risk_label = "Просрочен"
            summary = (
                f"Дата выпуска прошла {abs(days_left)} дн. назад, "
                f"ещё {active} active-задач"
            )
            risk_reasons.append(summary)
            risk_items.append(
                {
                    "id": "overdue",
                    "severity": "danger",
                    "title": "Релиз просрочен",
                    "summary": summary,
                    "detail": (
                        f"Плановая дата {meta.get('release_date')}. "
                        f"Закрыто {done} из {total} задач ({progress_pct:.0f}%)."
                    ),
                    "directions": _dir_refs(
                        sorted(
                            sections,
                            key=lambda s: (-(s.get("active_tasks") or 0), s.get("direction") or ""),
                        )[:5]
                    ),
                    "tasks": _task_refs(overdue_active),
                }
            )
        elif overload or slip or meta.get("overdue"):
            risk = "at_risk"
            risk_label = "Риск срыва"
            if slip:
                summary = (
                    f"Задачи закрыты на {progress_pct:.0f}%, а по календарю до релиза "
                    f"уже прошло {time_pct:.0f}% — отставание {slip_gap:.0f} п.п."
                )
                risk_reasons.append(summary)
                dir_hint = ""
                if lagging_dirs:
                    names = ", ".join(s["direction"] for s in lagging_dirs[:3])
                    dir_hint = f" Сильнее всего отстают: {names}."
                risk_items.append(
                    {
                        "id": "slip",
                        "severity": "danger",
                        "title": "Отставание от календаря",
                        "summary": summary,
                        "detail": (
                            "Календарь — доля времени от старта спринта до даты релиза. "
                            f"Чтобы быть в графике, прогресс задач должен быть около {time_pct:.0f}% "
                            f"(допуск −{slip_tol:.0f} п.п.). Сейчас {progress_pct:.0f}%."
                            + dir_hint
                        ),
                        "directions": _dir_refs(
                            sorted(
                                lagging_dirs or sections,
                                key=lambda s: (-(s.get("lag_pp") or 0), s.get("direction") or ""),
                            )[:5]
                        ),
                        "tasks": _task_refs(risk_tasks or overdue_active),
                    }
                )
            if overload:
                over_h = active_estimate - capacity
                summary = (
                    f"Оценка active {active_estimate:.1f} ч выше ёмкости до релиза "
                    f"{capacity:.1f} ч на {over_h:.1f} ч"
                )
                risk_reasons.append(summary)
                risk_items.append(
                    {
                        "id": "overload",
                        "severity": "warn",
                        "title": "Не хватает ёмкости",
                        "summary": summary,
                        "detail": (
                            f"Ёмкость ≈ {workdays_left} раб. дн. × {expected_hours:.0f} ч "
                            f"× {max(len(active_people), 1)} исп. с active-задачами."
                        ),
                        "directions": _dir_refs(
                            sorted(
                                sections,
                                key=lambda s: (
                                    -(s.get("active_estimate_hours") or 0),
                                    s.get("direction") or "",
                                ),
                            )[:5]
                        ),
                        "tasks": _task_refs(overdue_active),
                    }
                )
            if meta.get("overdue"):
                summary = "В Jira версия отмечена как overdue"
                risk_reasons.append(summary)
                risk_items.append(
                    {
                        "id": "jira_overdue",
                        "severity": "warn",
                        "title": "Флаг overdue в Jira",
                        "summary": summary,
                        "detail": "Статус выставлен в Jira независимо от расчёта отчёта.",
                        "directions": [],
                        "tasks": [],
                    }
                )
        elif active == 0 and total > 0:
            risk = "ok"
            risk_label = "Готов"
        else:
            risk = "on_track"
            risk_label = "В графике"

        out.append(
            {
                "id": vid,
                "name": meta.get("name") or vid,
                "project": meta.get("project"),
                "description": meta.get("description") or "",
                "released": released,
                "release_date": meta.get("release_date"),
                "start_date": meta.get("start_date"),
                "web_url": meta.get("web_url")
                or (f"{browse_base}/browse/{vid}" if browse_base else None),
                "tasks_total": total,
                "tasks_done": done,
                "tasks_active": active,
                "tasks_tagged": tagged,
                "progress_pct": _round(progress_pct, 0) or 0.0,
                "time_pct": _round(time_pct, 0) or 0.0,
                "slip_gap_pp": _round(slip_gap, 0) or 0.0,
                "days_left": days_left,
                "estimate_hours": _round(estimate, 1) or 0.0,
                "active_estimate_hours": _round(active_estimate, 1) or 0.0,
                "capacity_hours": _round(capacity, 1) or 0.0,
                "risk": risk,
                "risk_label": risk_label,
                "risk_reasons": risk_reasons,
                "risk_items": risk_items,
                "sections": sections,
                "tasks": sorted(
                    version_tasks,
                    key=lambda t: (
                        0 if t.get("direction_state") == "active" else 1,
                        -(
                            len(
                                [
                                    tag
                                    for tag in (t.get("tags") or [])
                                    if tag.get("id") != "release"
                                ]
                            )
                        ),
                        t.get("key") or "",
                    ),
                ),
            }
        )
    return out


def _build_people_profiles(
    *,
    issues: list[dict],
    team_rows: list[dict],
    hours_by_person_day: dict[str, dict[str, float]],
    hours_by_person_day_issue: dict[str, dict[str, dict[str, float]]],
    issue_summary_by_key: dict[str, str],
    hours_by_issue: dict[str, float],
    links: dict,
    ratings: list[dict],
    browse_base: str,
    sprint_days: list[str],
    sprint: dict,
    expected_hours: float,
    changelogs: list[dict] | None = None,
    all_issues: list[dict] | None = None,
) -> dict[str, dict]:
    """Compact per-person dossier for the UI modal."""
    team_cfg = get_team_config()
    by_name = {r["name"]: dict(r) for r in team_rows}
    tasks_by_person: dict[str, list[dict]] = defaultdict(list)
    today = datetime.now(timezone.utc).date()
    end_d = date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    sprint_id = sprint.get("id")
    current_assignee_by_key: dict[str, str] = {}
    for issue in all_issues or issues:
        fields = issue.get("fields") or {}
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        assignee_display = _jira_person_name(fields.get("assignee"))
        if assignee_display and assignee_display != "Без исполнителя":
            current_assignee_by_key[key] = assignee_display

    for issue in issues:
        name = issue.get("_canonical_assignee")
        fields = issue.get("fields") or {}
        key = (issue.get("key") or "").upper()
        if not name or name not in by_name:
            continue
        status_name = ((fields.get("status") or {}).get("name"))
        direction = issue.get("_direction")
        kind = team_cfg.classify_status(
            direction,
            status_name,
            jira_done=_is_done(fields),
        )
        linked = (links.get("issues_by_key") or {}).get(key) or {}
        tags = _build_task_tags(
            fields=fields,
            direction=direction,
            direction_state=kind,
            sprint_id=sprint_id,
            today=today,
            end_d=end_d,
            expected_hours=expected_hours,
            inactive_days=team_cfg.inactive_days,
        )
        rem = _remaining_estimate_hours(fields)
        summary = fields.get("summary") or key
        tasks_by_person[name].append(
            {
                "key": key,
                "summary": summary,
                "status": status_name,
                "status_category": ((fields.get("status") or {}).get("statusCategory") or {}).get(
                    "key"
                ),
                "direction": direction,
                "direction_state": kind,
                "hours": _round(hours_by_issue.get(key, 0.0), 2) or 0.0,
                "estimate_hours": _round(_estimate_hours(fields), 2),
                "remaining_hours": _round(rem, 2) if rem is not None else None,
                "mr_count": len(linked.get("mrs") or []),
                "web_url": f"{browse_base}/browse/{key}" if browse_base and key else None,
                "epic_key": issue.get("_epic_key"),
                "tags": tags,
                "risk": _has_risk_tags(tags),
                "hidden_from_display": team_cfg.is_hidden_from_display(summary),
            }
        )

    activity_by_person = build_people_activity(
        people_names=list(by_name.keys()),
        changelogs=changelogs or [],
        hours_by_person_day_issue=hours_by_person_day_issue,
        issue_summary_by_key=issue_summary_by_key,
        browse_base=browse_base,
        current_assignee_by_key=current_assignee_by_key,
    )

    rating_hits: dict[str, list[dict]] = defaultdict(list)
    for cat in ratings or []:
        if not cat.get("enabled"):
            continue
        for person in cat.get("people") or []:
            pname = person.get("name")
            if not pname:
                continue
            rating_hits[pname].append(
                {
                    "id": cat.get("id"),
                    "title": cat.get("title"),
                    "place": person.get("place"),
                    "value": person.get("value"),
                    "detail": person.get("detail"),
                }
            )

    profiles: dict[str, dict] = {}
    for name, base in by_name.items():
        tasks = sorted(
            tasks_by_person.get(name) or [],
            key=lambda t: (
                0 if t.get("direction_state") == "active" else 1,
                -(t.get("hours") or 0),
                t.get("key") or "",
            ),
        )
        # Full sprint calendar for the hours chart (not truncated to "today").
        if sprint.get("start_date") and sprint.get("end_date"):
            chart_days = [
                d.isoformat()
                for d in _daterange(
                    date.fromisoformat(sprint["start_date"]),
                    date.fromisoformat(sprint["end_date"]),
                )
            ]
        else:
            chart_days = list(sprint_days)
        today_s = report_today().isoformat()
        day_hours = []
        for day in chart_days:
            day_total = _round(hours_by_person_day.get(name, {}).get(day, 0.0), 2) or 0.0
            by_issue = hours_by_person_day_issue.get(name, {}).get(day) or {}
            issues_day = [
                {
                    "key": key,
                    "summary": issue_summary_by_key.get(key) or key,
                    "hours": _round(hours, 2) or 0.0,
                }
                for key, hours in sorted(
                    by_issue.items(), key=lambda item: (-item[1], item[0])
                )
                if hours > 0
            ]
            day_hours.append(
                {
                    "date": day,
                    "hours": day_total,
                    "issues": issues_day,
                    "is_today": day == today_s,
                    "is_future": day > today_s,
                }
            )
        active_tasks = [t for t in tasks if t.get("direction_state") == "active"]
        risk_tasks = [t for t in active_tasks if t.get("risk") or _has_risk_tags(t.get("tags"))]
        load = _build_person_load(
            active_tasks=active_tasks,
            sprint=sprint,
            expected_hours=expected_hours,
            today=report_today(),
            hours_sprint=float(base.get("hours_sprint") or 0.0),
        )
        m = team_cfg.metrics
        profiles[name] = {
            **base,
            "tasks": tasks[: m.person_tasks_limit],
            "tasks_active": active_tasks[: m.person_active_tasks_limit],
            "risk_count": len(risk_tasks),
            "remaining_hours": load.get("remaining_hours") or 0.0,
            "load": load,
            "day_hours": day_hours,
            "ratings": rating_hits.get(name) or [],
            "activity": activity_by_person.get(name)
            or {
                "yesterday": {
                    "date": None,
                    "label": "Вчера",
                    "events": [],
                    "groups": [],
                    "empty": True,
                },
                "today": {
                    "date": None,
                    "label": "Сегодня",
                    "events": [],
                    "groups": [],
                    "empty": True,
                },
            },
        }
    return profiles


def _worklog_comment_text(comment: Any) -> str:
    if comment is None:
        return ""
    if isinstance(comment, str):
        return comment.strip()
    if isinstance(comment, dict):
        # Jira Server sometimes wraps worklog comment similarly to ADF
        parts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and node.get("text"):
                    parts.append(str(node.get("text")))
                for child in node.get("content") or []:
                    walk(child)
                if node.get("body") and isinstance(node.get("body"), str):
                    parts.append(node["body"])
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(comment)
        return " ".join(parts).strip()
    return str(comment).strip()


def _build_issues_index(
    *,
    issues: list[dict],
    all_issues: list[dict],
    hours_by_issue: dict[str, float],
    links: dict,
    browse_base: str,
    sprint: dict,
    expected_hours: float,
    changelogs: list[dict],
    worklogs: list[dict],
    comments: list[dict],
    epics_meta: dict,
    gitlab_people: dict,
    jira_avatar_by_person: dict,
) -> dict[str, dict]:
    """Per-issue dossier for the task modal."""
    team_cfg = get_team_config()
    today = report_today()
    end_d = date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    sprint_id = sprint.get("id")
    browse_base = (browse_base or "").rstrip("/")

    by_key: dict[str, dict] = {}
    source = list(all_issues or []) + list(issues or [])
    for issue in source:
        key = (issue.get("key") or "").upper()
        if not key or key in by_key:
            continue
        fields = issue.get("fields") or {}
        status_obj = fields.get("status") or {}
        status_name = status_obj.get("name")
        direction = issue.get("_direction")
        if not direction:
            assignee_name = _jira_person_name(fields.get("assignee"))
            canonical_tmp = canonical_team_name(assignee_name)
            direction = TEAM_ROSTER.get(canonical_tmp) if canonical_tmp else None
        jira_done = _is_done(fields)
        kind = (
            team_cfg.classify_status(direction, status_name, jira_done=jira_done)
            if direction
            else ("done" if jira_done else "other")
        )
        rem = _remaining_estimate_hours(fields)
        est = _estimate_hours(fields)
        spent = float(hours_by_issue.get(key, 0.0))
        if spent <= 0:
            spent = float(fields.get("timespent") or 0) / 3600.0
        tags = _build_task_tags(
            fields=fields,
            direction=direction or "",
            direction_state=kind,
            sprint_id=sprint_id,
            today=today,
            end_d=end_d,
            expected_hours=expected_hours,
            inactive_days=team_cfg.inactive_days,
        )
        assignee_display = issue.get("_assignee_display") or _jira_person_name(
            fields.get("assignee")
        )
        canonical = issue.get("_canonical_assignee") or canonical_team_name(
            assignee_display
        )
        reporter_display = _jira_person_name(fields.get("reporter"))
        if reporter_display == "Без исполнителя":
            reporter_display = None
        reporter_canonical = canonical_team_name(reporter_display) if reporter_display else None
        linked = (links.get("issues_by_key") or {}).get(key) or {}
        mrs = linked.get("mrs") or []
        progress_pct = None
        if est and est > 0:
            progress_pct = _round(min(100.0, (spent / float(est)) * 100.0), 0)

        by_key[key] = {
            "key": key,
            "summary": fields.get("summary") or key,
            "status": status_name,
            "status_category": (status_obj.get("statusCategory") or {}).get("key"),
            "direction": direction,
            "direction_state": kind,
            "assignee": assignee_display,
            "assignee_canonical": canonical,
            "reporter": reporter_display,
            "reporter_canonical": reporter_canonical,
            "avatar_url": _avatar_for(
                canonical,
                jira_avatar_by_person.get(canonical or ""),
                gitlab_people,
            )
            if canonical
            else None,
            "hours": _round(spent, 2) or 0.0,
            "estimate_hours": _round(est, 2) if est is not None else None,
            "remaining_hours": _round(rem, 2) if rem is not None else None,
            "progress_pct": progress_pct,
            "web_url": f"{browse_base}/browse/{key}" if browse_base else None,
            "epic_key": issue.get("_epic_key"),
            "epic_summary": issue.get("_epic_summary")
            or ((epics_meta.get((issue.get("_epic_key") or "").upper()) or {}).get("summary")),
            "tags": tags,
            "risk": _has_risk_tags(tags),
            "hidden_from_display": team_cfg.is_hidden_from_display(
                fields.get("summary")
            ),
            "version_ids": [
                str(tag.get("release_id"))
                for tag in tags
                if tag.get("id") == "release" and tag.get("release_id")
            ],
            "mr_count": len(mrs),
            "mrs": [
                {
                    "title": mr.get("title"),
                    "web_url": mr.get("web_url"),
                    "state": mr.get("state"),
                    "iid": mr.get("iid"),
                }
                for mr in mrs[:8]
            ],
            "history": [],
            "worklogs": [],
            "comments": [],
        }

    for hist in changelogs or []:
        key = (hist.get("issue_key") or "").upper()
        dossier = by_key.get(key)
        if not dossier:
            continue
        dossier["history"].append(
            {
                "at": hist.get("at"),
                "author": hist.get("author"),
                "status_from": hist.get("status_from"),
                "status_to": hist.get("status_to"),
                "assignee_from": hist.get("assignee_from"),
                "assignee_to": hist.get("assignee_to"),
            }
        )

    for log in worklogs or []:
        key = (log.get("issue_key") or "").upper()
        dossier = by_key.get(key)
        if not dossier:
            continue
        author = log.get("author") or {}
        author_name = (
            author.get("displayName") or author.get("name") or ""
        ).strip() or None
        seconds = float(log.get("time_spent_seconds") or 0)
        dossier["worklogs"].append(
            {
                "at": log.get("started"),
                "hours": _round(seconds / 3600.0, 2) or 0.0,
                "author": author_name,
                "author_canonical": canonical_team_name(author_name),
                "comment": _worklog_comment_text(log.get("comment")) or None,
            }
        )

    for comment in comments or []:
        key = (comment.get("issue_key") or "").upper()
        dossier = by_key.get(key)
        if not dossier:
            continue
        dossier["comments"].append(
            {
                "at": comment.get("at"),
                "author": comment.get("author"),
                "author_canonical": canonical_team_name(comment.get("author")),
                "body": comment.get("body"),
            }
        )

    for dossier in by_key.values():
        dossier["history"].sort(key=lambda x: x.get("at") or "", reverse=True)
        dossier["history"] = dossier["history"][:40]
        dossier["worklogs"].sort(key=lambda x: x.get("at") or "", reverse=True)
        dossier["comments"].sort(key=lambda x: x.get("at") or "", reverse=True)

    return by_key


def compute_sprint_report(jira_raw: dict, gitlab_raw: dict | None) -> dict:
    sprint = jira_raw.get("sprint") or {}
    all_issues = jira_raw.get("issues") or []
    expected = float(jira_raw.get("expected_hours_per_day") or 8)
    browse_base = (jira_raw.get("browse_base") or "").rstrip("/")
    epics_meta = jira_raw.get("epics") or {}

    gitlab_people = collect_gitlab_people(gitlab_raw)
    # Roster GitLab profiles (avatar_source=gitlab) even without MRs in window.
    # Upsert by roster name and prefer a real avatar_url over empty MR stubs.
    for name, profile in (gitlab_raw.get("roster_profiles") or {}).items():
        if not isinstance(profile, dict):
            continue
        roster_name = (name or "").strip()
        if not roster_name:
            continue
        avatar = profile.get("avatar_url")
        username = profile.get("username")
        web_url = profile.get("web_url")
        existing = best_gitlab_person(roster_name, gitlab_people)
        if existing is not None:
            if avatar and (
                not existing.get("avatar_url")
                or _looks_like_default_avatar(existing.get("avatar_url"))
            ):
                existing["avatar_url"] = avatar
            if username and not existing.get("username"):
                existing["username"] = username
            if web_url and not existing.get("web_url"):
                existing["web_url"] = web_url
            continue
        gitlab_people.append(
            {
                "name": roster_name,
                "username": username,
                "web_url": web_url,
                "avatar_url": avatar,
                "projects": [],
                "mr_count": 0,
            }
        )

    # Keep only issues assigned to roster members
    issues = []
    for issue in all_issues:
        fields = issue.get("fields") or {}
        assignee_name = _jira_person_name(fields.get("assignee"))
        canonical = canonical_team_name(assignee_name)
        if not canonical:
            continue
        issue = dict(issue)
        issue["_canonical_assignee"] = canonical
        # Keep Jira display name for UI — never substitute another person's FIO
        issue["_assignee_display"] = assignee_name
        issue["_direction"] = TEAM_ROSTER[canonical]
        issues.append(issue)

    links = link_sprint_issues(issues, gitlab_raw, browse_base=browse_base)
    # Attach commit counts from enriched MRs into issues_by_key mrs
    for key, issue_link in (links.get("issues_by_key") or {}).items():
        for mr in issue_link.get("mrs") or []:
            # commit_count already on raw MR objects if enriched; copy if present in iter
            pass

    # Rebuild commit counts from gitlab raw into links (match by ref/name/iid)
    mr_commits: dict[tuple, int] = {}
    if gitlab_raw:
        for project in gitlab_raw.get("projects") or []:
            for bucket in (
                project.get("merge_requests_merged") or [],
                project.get("merge_requests_open") or [],
            ):
                for mr in bucket:
                    count = int(mr.get("commit_count") or 0)
                    iid = mr.get("iid")
                    for proj_key in (
                        project.get("ref"),
                        project.get("name"),
                        project.get("web_url"),
                    ):
                        if proj_key:
                            mr_commits[(proj_key, iid)] = count
                    # also by normalized web_url
                    if mr.get("web_url"):
                        mr_commits[(str(mr["web_url"]).rstrip("/").lower(), iid)] = count
    for issue_link in (links.get("issues_by_key") or {}).values():
        for mr in issue_link.get("mrs") or []:
            iid = mr.get("iid")
            count = None
            for proj_key in (
                mr.get("project_ref"),
                mr.get("project"),
                mr.get("web_url"),
            ):
                if not proj_key:
                    continue
                key = (
                    str(proj_key).rstrip("/").lower()
                    if "http" in str(proj_key).lower()
                    else proj_key
                )
                if (proj_key, iid) in mr_commits:
                    count = mr_commits[(proj_key, iid)]
                    break
                if (key, iid) in mr_commits:
                    count = mr_commits[(key, iid)]
                    break
            if count is None and mr.get("commit_count") is not None:
                count = int(mr.get("commit_count") or 0)
            mr["commit_count"] = int(count or 0)

    done_count = sum(1 for issue in issues if _is_done(issue.get("fields") or {}))
    total = len(issues)
    open_count = total - done_count
    day_index, day_total, time_progress_pct = _sprint_day_progress(sprint)
    tasks_progress_pct = _round((done_count / total * 100.0) if total else 0.0)

    start_d = date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else None
    end_d = date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    local_now = _report_local_now()
    today = local_now.date()
    sprint_calendar_dates = _daterange(start_d, end_d) if start_d and end_d else []
    sprint_day_dates = [day for day in sprint_calendar_dates if day <= today]
    sprint_work_dates = [day for day in sprint_calendar_dates if not _is_weekend(day)]

    # Worklogs only from roster
    hours_by_person_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    hours_by_person_day_issue: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    hours_by_issue: dict[str, float] = defaultdict(float)
    hours_by_person_issue: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    issue_summary_by_key: dict[str, str] = {}
    jira_avatar_by_person: dict[str, str | None] = {}
    jira_username_by_person: dict[str, str | None] = {}

    for log in jira_raw.get("worklogs") or []:
        author_name = _jira_person_name(log.get("author"))
        canonical = canonical_team_name(author_name)
        if not canonical:
            continue
        started = _parse_dt(log.get("started"))
        if not started:
            continue
        day = started.date().isoformat()
        hours = float(log.get("time_spent_seconds") or 0) / 3600.0
        hours_by_person_day[canonical][day] += hours
        if log.get("issue_key"):
            key = str(log["issue_key"]).upper()
            hours_by_issue[key] += hours
            hours_by_person_issue[canonical][key] += hours
            hours_by_person_day_issue[canonical][day][key] += hours
            summary = (log.get("issue_summary") or "").strip()
            if summary:
                issue_summary_by_key.setdefault(key, summary)
        jira_avatar_by_person.setdefault(canonical, _jira_avatar(log.get("author")))
        jira_username_by_person.setdefault(canonical, _jira_username(log.get("author")))

    for issue in issues:
        fields = issue.get("fields") or {}
        canonical = issue["_canonical_assignee"]
        jira_avatar_by_person.setdefault(canonical, _jira_avatar(fields.get("assignee")))
        jira_username_by_person.setdefault(canonical, _jira_username(fields.get("assignee")))
        reporter_name = _jira_person_name(fields.get("reporter"))
        reporter_canonical = canonical_team_name(reporter_name)
        if reporter_canonical:
            jira_avatar_by_person.setdefault(
                reporter_canonical, _jira_avatar(fields.get("reporter"))
            )
            jira_username_by_person.setdefault(
                reporter_canonical, _jira_username(fields.get("reporter"))
            )
        key = (issue.get("key") or "").upper()
        if key:
            summary = (fields.get("summary") or "").strip()
            if summary:
                issue_summary_by_key.setdefault(key, summary)

    # Roster profiles from /user API (covers people absent from sprint issues)
    for name, profile in (jira_raw.get("roster_profiles") or {}).items():
        if not isinstance(profile, dict):
            continue
        canonical = canonical_team_name(name) or name
        roster_avatar = profile.get("avatar_url")
        if roster_avatar:
            current = jira_avatar_by_person.get(canonical)
            if not current or _looks_like_default_avatar(current):
                jira_avatar_by_person[canonical] = roster_avatar
        if profile.get("username"):
            jira_username_by_person.setdefault(canonical, profile.get("username"))

    selected_day_date = today if today in sprint_day_dates else (
        sprint_day_dates[-1] if sprint_day_dates else today
    )
    selected_day = selected_day_date.isoformat()
    selected_is_weekend = _is_weekend(selected_day_date)

    # Ensure all roster members appear (even with 0 tasks)
    people = set(TEAM_ROSTER.keys())

    team_cfg = get_team_config()
    tasks_by_person: dict[str, dict[str, int]] = defaultdict(
        lambda: {"done": 0, "open": 0, "mr_count": 0}
    )
    for issue in issues:
        canonical = issue["_canonical_assignee"]
        fields = issue.get("fields") or {}
        key = (issue.get("key") or "").upper()
        status_name = ((fields.get("status") or {}).get("name"))
        kind = team_cfg.classify_status(
            issue["_direction"],
            status_name,
            jira_done=_is_done(fields),
        )
        if kind == "active":
            tasks_by_person[canonical]["open"] += 1
        else:
            # done for direction or handed-off/other — not on person's active plate
            tasks_by_person[canonical]["done"] += 1
        linked = (links.get("issues_by_key") or {}).get(key, {})
        tasks_by_person[canonical]["mr_count"] += len(linked.get("mrs") or [])

    direction_buckets: dict[str, list[dict]] = defaultdict(list)
    team_rows = []
    for name in sorted(people, key=lambda x: x.lower()):
        direction = TEAM_ROSTER[name]
        tasks = tasks_by_person.get(name) or {"done": 0, "open": 0, "mr_count": 0}
        hours_today = _round(hours_by_person_day.get(name, {}).get(selected_day, 0.0), 2) or 0.0
        hours_sprint = _round(sum(hours_by_person_day.get(name, {}).values()), 2) or 0.0
        level = "skip" if selected_is_weekend else _hours_level(hours_today, expected)
        gender = team_cfg.gender_for(name)
        links_for = _profile_links_for(
            name,
            browse_base=browse_base,
            jira_username=jira_username_by_person.get(name),
            gitlab_people=gitlab_people,
        )
        row = {
            "name": name,
            "avatar_url": _avatar_for(name, jira_avatar_by_person.get(name), gitlab_people),
            "direction": direction,
            "jira_url": links_for.get("jira_url"),
            "gitlab_url": links_for.get("gitlab_url"),
            "messenger_url": team_cfg.messenger_url_for(name),
            "gender": gender,
            "lead": team_cfg.is_lead(name),
            "tasks_done": tasks["done"],
            "tasks_open": tasks["open"],
            "tasks_total": tasks["done"] + tasks["open"],
            "hours_today": hours_today,
            "hours_sprint": hours_sprint,
            "expected_hours_today": expected,
            "hours_level": level,
            "worklog_ok": level in {"ok", "skip"},
            "tracked": True,
            "mr_count": tasks["mr_count"],
        }
        team_rows.append(row)
        # risk_count / remaining filled after profiles; placeholder for table
        direction_buckets[direction].append(row)

    # Per-direction task analytics + epics
    directions = []
    all_direction_epics: dict[str, dict] = {}

    for title in DIRECTION_ORDER:
        members = direction_buckets.get(title) or []
        member_names = {m["name"] for m in members}
        dir_issues = [i for i in issues if i["_direction"] == title]

        task_stats = []
        for issue in dir_issues:
            fields = issue.get("fields") or {}
            key = (issue.get("key") or "").upper()
            linked = (links.get("issues_by_key") or {}).get(key, {})
            commits = sum(int(mr.get("commit_count") or 0) for mr in (linked.get("mrs") or []))
            hours = _round(hours_by_issue.get(key, 0.0), 2) or 0.0
            estimate = _estimate_hours(fields)
            status_obj = fields.get("status") or {}
            status_name = status_obj.get("name")
            summary = fields.get("summary")
            jira_done = _is_done(fields)
            kind = team_cfg.classify_status(
                title, status_name, jira_done=jira_done
            )
            tags = _build_task_tags(
                fields=fields,
                direction=title,
                direction_state=kind,
                sprint_id=sprint.get("id"),
                today=today,
                end_d=end_d,
                expected_hours=expected,
                inactive_days=team_cfg.inactive_days,
            )
            assignee_canonical = issue["_canonical_assignee"]
            task_stats.append(
                {
                    "key": key,
                    "summary": summary,
                    "status": status_name,
                    "status_category": ((status_obj.get("statusCategory") or {}).get("key")),
                    "assignee": issue.get("_assignee_display")
                    or _jira_person_name(fields.get("assignee")),
                    "assignee_canonical": assignee_canonical,
                    "avatar_url": _avatar_for(
                        assignee_canonical,
                        jira_avatar_by_person.get(assignee_canonical),
                        gitlab_people,
                    ),
                    "done": kind == "done",
                    "jira_done": jira_done,
                    "direction_state": kind,
                    "hours": hours,
                    "estimate_hours": _round(estimate, 2) if estimate is not None else None,
                    "commit_count": commits if title in DEV_DIRECTIONS else None,
                    "mr_count": len(linked.get("mrs") or []),
                    "web_url": f"{browse_base}/browse/{key}" if browse_base and key else None,
                    "epic_key": issue.get("_epic_key"),
                    "hidden_from_display": team_cfg.is_hidden_from_display(summary),
                    "tags": tags,
                }
            )

        visible_tasks = [t for t in task_stats if not t.get("hidden_from_display")]
        # Remaining work for this direction (active by status_rules)
        remaining_tasks = sorted(
            [t for t in visible_tasks if t.get("direction_state") == "active"],
            key=lambda t: (
                (t.get("status") or "").lower(),
                -(t.get("hours") or 0),
                t.get("key") or "",
            ),
        )
        top_by_commits = (
            sorted(
                [t for t in visible_tasks if (t.get("commit_count") or 0) > 0],
                key=lambda t: (-(t["commit_count"] or 0), t["key"]),
            )[:8]
            if title in DEV_DIRECTIONS
            else []
        )

        # Epics: progress by direction-specific done/active (ignore "other")
        epic_agg: dict[str, dict] = {}
        for task in task_stats:
            epic_key = task.get("epic_key")
            if not epic_key:
                continue
            state = task.get("direction_state")
            if state == "other":
                continue
            bucket = epic_agg.setdefault(
                epic_key,
                {
                    "key": epic_key,
                    "done": 0,
                    "total": 0,
                    "hours": 0.0,
                    "estimate_hours": 0.0,
                    "open": 0,
                },
            )
            bucket["total"] += 1
            bucket["hours"] += task["hours"] or 0
            if task.get("estimate_hours") is not None:
                bucket["estimate_hours"] += task["estimate_hours"] or 0
            if state == "done":
                bucket["done"] += 1
            else:
                bucket["open"] += 1

        epic_rows = []
        epic_summaries = {
            (i.get("_epic_key") or ""): i.get("_epic_summary")
            for i in dir_issues
            if i.get("_epic_key") and i.get("_epic_summary")
        }
        for epic_key, agg in epic_agg.items():
            # Sprint focus: only epics with direction-active work left in sprint
            if (agg.get("open") or 0) <= 0:
                continue
            meta = epics_meta.get(epic_key) or {"key": epic_key, "summary": epic_key}
            summary = (
                meta.get("summary")
                or epic_summaries.get(epic_key)
                or epic_key
            )
            progress = _round((agg["done"] / agg["total"] * 100.0) if agg["total"] else 0.0)
            row = {
                "key": epic_key,
                "summary": summary,
                "status": meta.get("status"),
                "done": agg["done"],
                "total": agg["total"],
                "open": agg["open"],
                "progress_pct": progress,
                "hours": _round(agg["hours"], 2) or 0.0,
                "estimate_hours": _round(agg["estimate_hours"], 2) or 0.0,
                "web_url": f"{browse_base}/browse/{epic_key}" if browse_base else None,
                "direction": title,
                "is_epic": True,
            }
            epic_rows.append(row)
            all_direction_epics[epic_key] = row
        epic_rows.sort(key=lambda e: (-e["open"], -e["total"], e["key"]))

        relevant = [t for t in visible_tasks if t.get("direction_state") in {"active", "done"}]
        tasks_done = sum(1 for t in relevant if t.get("direction_state") == "done")
        tasks_total = len(relevant)
        directions.append(
            {
                "name": title,
                "key": team_cfg.resolve_direction_key(title),
                "short": team_cfg.direction_short(title),
                "color": team_cfg.direction_color(title),
                "people_count": len(members),
                "tasks_done": tasks_done,
                "tasks_total": tasks_total,
                "tasks_remaining": len(remaining_tasks),
                "tasks_progress_pct": _round(
                    (tasks_done / tasks_total * 100.0) if tasks_total else 0.0
                ),
                "hours_sprint": _round(sum(m["hours_sprint"] for m in members), 2) or 0.0,
                "members": sorted(
                    members,
                    key=lambda r: (r["worklog_ok"], -(r["tasks_open"]), r["name"].lower()),
                ),
                "remaining_tasks": remaining_tasks,
                "top_tasks_by_commits": top_by_commits,
                "epics": epic_rows,
                "is_dev": title in DEV_DIRECTIONS,
            }
        )

    by_day = []
    week_starts: dict[date, int] = {}
    expected_to_date = 0.0
    for day in sprint_work_dates:
        day_key = day.isoformat()
        future = day > today
        target_hours = _expected_hours_at(
            day,
            now=local_now,
            expected_hours=expected,
            start_hour=team_cfg.ratings.workday_start_hour,
        )
        expected_to_date += target_hours
        monday = day - timedelta(days=day.weekday())
        if monday not in week_starts:
            week_starts[monday] = len(week_starts) + 1
        rows = []
        for name in sorted(people, key=lambda x: x.lower()):
            hours = _round(hours_by_person_day.get(name, {}).get(day_key, 0.0), 2) or 0.0
            if future or target_hours <= 0:
                level = "future"
            elif hours <= 0:
                level = "bad"
            elif hours + 0.01 >= target_hours:
                level = "ok"
            else:
                level = "warn"
            rows.append(
                {
                    "name": name,
                    "avatar_url": _avatar_for(
                        name, jira_avatar_by_person.get(name), gitlab_people
                    ),
                    "direction": TEAM_ROSTER[name],
                    "hours": hours,
                    "expected_hours": target_hours,
                    "level": level,
                    "ok": level in {"ok", "future"},
                    "delta_hours": _round(hours - target_hours, 2),
                }
            )
        rows.sort(key=lambda r: (r["ok"], r["hours"], r["name"].lower()))
        by_day.append(
            {
                "date": day_key,
                "is_weekend": False,
                "is_future": future,
                "is_today": day == today,
                "week_index": week_starts[monday],
                "expected_hours": _round(target_hours, 2) or 0.0,
                "people": rows,
                "underlogged_count": sum(
                    1 for r in rows if r["level"] in {"warn", "bad"}
                ),
                "ok_count": sum(1 for r in rows if r["level"] == "ok"),
            }
        )

    # Risks: finish threat / stale / no recent worklogs / estimates / releases
    days_left = max((end_d - today).days, 0) if end_d else None
    at_risk = []
    stale = []
    no_recent_logs = []
    no_estimate = []
    no_release = []
    released_not_closed = []

    for issue in issues:
        fields = issue.get("fields") or {}
        if _is_done(fields):
            continue
        key = (issue.get("key") or "").upper()
        status = ((fields.get("status") or {}).get("name")) or "—"
        updated = _parse_dt(fields.get("updated"))
        assignee_canonical = issue["_canonical_assignee"]
        assignee_display = issue.get("_assignee_display") or assignee_canonical
        category = ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
        summary = fields.get("summary")
        if get_team_config().is_hidden_from_display(summary):
            continue
        direction = issue["_direction"]
        direction_state = team_cfg.classify_status(direction, status, jira_done=False)
        tags = _build_task_tags(
            fields=fields,
            direction=direction,
            direction_state=direction_state,
            sprint_id=sprint.get("id"),
            today=today,
            end_d=end_d,
            expected_hours=expected,
            inactive_days=team_cfg.inactive_days,
        )
        item = {
            "key": key,
            "summary": summary,
            "status": status,
            "status_category": category,
            "assignee": assignee_display,
            "assignee_canonical": assignee_canonical,
            "direction": direction,
            "avatar_url": _avatar_for(
                assignee_canonical,
                jira_avatar_by_person.get(assignee_canonical),
                gitlab_people,
            ),
            "web_url": f"{browse_base}/browse/{key}" if browse_base and key else None,
            "updated": fields.get("updated"),
            "hours": _round(hours_by_issue.get(key, 0.0), 2) or 0.0,
            "estimate_hours": None,
            "tags": tags,
            **_risk_release_meta(fields, sprint),
        }
        est_h = _estimate_hours(fields)
        if est_h is not None:
            item["estimate_hours"] = _round(est_h, 2)
        m = team_cfg.metrics
        late_sprint = (time_progress_pct or 0) >= m.risk_sprint_time_pct or (
            days_left is not None and days_left <= m.risk_days_left
        )
        still_todo = category == "new" or "выполн" in status.lower() or status.lower() in {
            "to do",
            "open",
            "открыт",
            "к выполнению",
        }
        if late_sprint and (still_todo or category != "done"):
            reason = []
            if days_left is not None and days_left <= m.risk_days_left:
                reason.append(f"до конца {days_left} дн.")
            if (time_progress_pct or 0) >= m.risk_sprint_time_pct:
                reason.append(f"спринт {time_progress_pct}%")
            if still_todo:
                reason.append("ещё не в работе / рано по статусу")
            item["reason"] = "; ".join(reason) or "риск не закрыть в срок"
            at_risk.append(item)

        if updated and (today - updated.date()).days >= m.stale_days:
            stale_item = dict(item)
            stale_item["reason"] = f"нет обновлений {(today - updated.date()).days} дн."
            stale.append(stale_item)

        # no worklogs in last 2 working days
        recent_hours = 0.0
        cursor = today
        checked = 0
        while checked < 2:
            if not _is_weekend(cursor):
                recent_hours += hours_by_person_day.get(assignee_canonical, {}).get(
                    cursor.isoformat(), 0.0
                )
                # also issue-level: approximate via person day is weaker; use issue hours only total
                checked += 1
            cursor -= timedelta(days=1)
            if (today - cursor).days > 10:
                break
        if hours_by_issue.get(key, 0.0) <= 0 and late_sprint:
            idle = dict(item)
            idle["reason"] = "нет списаний по задаче"
            no_recent_logs.append(idle)

        if est_h is None or est_h <= 0:
            missing_est = dict(item)
            missing_est["reason"] = "нет оценки (Original Estimate)"
            no_estimate.append(missing_est)

        if not (fields.get("fixVersions") or []):
            missing_rel = dict(item)
            missing_rel["reason"] = "нет Fix Version (релиза)"
            no_release.append(missing_rel)

        released_versions = [
            version
            for version in (item.get("fix_versions") or [])
            if version.get("released")
        ]
        if released_versions:
            released_item = dict(item)
            released_item["released_versions"] = released_versions
            names = ", ".join(
                f"«{version.get('name') or version.get('id') or '?'}»"
                for version in released_versions
            )
            released_item["reason"] = (
                f"Релиз {names} выпущен, задача осталась "
                f"в статусе «{status}»"
            )
            released_not_closed.append(released_item)

    at_risk.sort(key=lambda x: x["assignee"])
    stale.sort(key=lambda x: x["assignee"])
    no_recent_logs.sort(key=lambda x: x["assignee"])
    no_estimate.sort(key=lambda x: (-(x.get("hours") or 0.0), x.get("key") or ""))
    no_release.sort(key=lambda x: (-(x.get("hours") or 0.0), x.get("key") or ""))
    released_not_closed.sort(
        key=lambda x: (x.get("status") or "", x.get("assignee") or "", x.get("key") or "")
    )

    state_label = {
        "active": "Активный",
        "closed": "Закрыт",
        "future": "Будущий",
    }.get(sprint.get("state") or "", sprint.get("state") or "—")

    # Attach avatars to rating candidates later in UI via team map
    avatar_map = {r["name"]: r.get("avatar_url") for r in team_rows}
    ratings = compute_ratings(
        sprint=sprint,
        team_rows=team_rows,
        issues=issues,
        hours_by_person_day=hours_by_person_day,
        hours_by_issue=hours_by_issue,
        hours_by_person_issue=hours_by_person_issue,
        worklogs=jira_raw.get("worklogs") or [],
        links=links,
        gitlab_raw=gitlab_raw,
        expected_hours=expected,
    )
    for category in ratings:
        for bucket in ("people", "all_people"):
            for person in category.get(bucket) or []:
                person["avatar_url"] = avatar_map.get(person["name"])

    releases = _build_releases(
        releases_meta=jira_raw.get("releases") or [],
        release_issues=jira_raw.get("release_issues") or [],
        sprint=sprint,
        expected_hours=expected,
        browse_base=browse_base,
    )

    sprint_issue_keys = {
        (i.get("key") or "").upper()
        for i in (jira_raw.get("issues") or [])
        if i.get("key")
    }
    epic_timeline = _build_epic_timeline(
        issues=issues,
        epics_meta=epics_meta,
        sprint=sprint,
        hours_by_issue=hours_by_issue,
        browse_base=browse_base,
        expected_hours=expected,
        release_epic_keys=jira_raw.get("release_epic_keys") or [],
        epic_scope_issues=jira_raw.get("epic_scope_issues") or [],
        sprint_issue_keys=sprint_issue_keys,
    )
    _enrich_epics_with_releases(epic_timeline, releases)

    people_profiles = _build_people_profiles(
        issues=issues,
        team_rows=team_rows,
        hours_by_person_day=hours_by_person_day,
        hours_by_person_day_issue=hours_by_person_day_issue,
        issue_summary_by_key=issue_summary_by_key,
        hours_by_issue=hours_by_issue,
        links=links,
        ratings=ratings,
        browse_base=browse_base,
        sprint_days=[d.isoformat() for d in sprint_day_dates],
        sprint=sprint,
        expected_hours=expected,
        changelogs=jira_raw.get("changelogs") or [],
        all_issues=all_issues,
    )

    for row in team_rows:
        profile = people_profiles.get(row["name"]) or {}
        row["risk_count"] = profile.get("risk_count") or 0
        row["remaining_hours"] = profile.get("remaining_hours") or 0
        row["load"] = profile.get("load")

    issues_index = _build_issues_index(
        issues=issues,
        all_issues=all_issues,
        hours_by_issue=hours_by_issue,
        links=links,
        browse_base=browse_base,
        sprint=sprint,
        expected_hours=expected,
        changelogs=jira_raw.get("changelogs") or [],
        worklogs=jira_raw.get("worklogs") or [],
        comments=jira_raw.get("comments") or [],
        epics_meta=epics_meta,
        gitlab_people=gitlab_people,
        jira_avatar_by_person=jira_avatar_by_person,
    )

    sprint_payload = {
        "id": sprint.get("id"),
        "name": sprint.get("name"),
        "state": sprint.get("state"),
        "state_label": state_label,
        "start_date": sprint.get("start_date"),
        "end_date": sprint.get("end_date"),
        "goal": sprint.get("goal"),
        "day_index": day_index,
        "day_total": day_total,
        "days_left": days_left,
        "done": done_count,
        "open": open_count,
        "total": total,
        "tasks_progress_pct": tasks_progress_pct,
        "time_progress_pct": time_progress_pct,
        "progress_pct": tasks_progress_pct,
    }
    return {
        "sprint": sprint_payload,
        "direction_order": list(team_cfg.direction_order),
        "directions": directions,
        "team": team_rows,
        "people": people_profiles,
        "issues": issues_index,
        "ratings": ratings,
        "releases": releases,
        "epic_timeline": epic_timeline,
        "epics": sorted(
            all_direction_epics.values(),
            key=lambda e: (-e["total"], e["key"]),
        ),
        "worklogs": {
            "expected_hours_per_day": expected,
            "plan_hours": _round(len(sprint_work_dates) * expected, 2) or 0.0,
            "expected_hours_to_date": _round(expected_to_date, 2) or 0.0,
            "selected_date": selected_day,
            "selected_is_weekend": selected_is_weekend,
            "days": [d.isoformat() for d in sprint_work_dates],
            "by_day": by_day,
        },
        "risks": {
            "at_risk": at_risk[: team_cfg.metrics.risks_limit],
            "stale": stale[: team_cfg.metrics.risks_limit],
            "no_worklogs": no_recent_logs[: team_cfg.metrics.risks_limit],
            "no_estimate": no_estimate[: team_cfg.metrics.risks_limit],
            "no_release": no_release[: team_cfg.metrics.risks_limit],
            "released_not_closed": released_not_closed[: team_cfg.metrics.risks_limit],
            "selected_is_weekend": selected_is_weekend,
            "stale_days": team_cfg.metrics.stale_days,
        },
        "team_mood": _compute_team_mood(
            sprint=sprint_payload,
            risks={
                "at_risk": at_risk,
                "stale": stale,
                "no_worklogs": no_recent_logs,
                "no_estimate": no_estimate,
                "no_release": no_release,
                "stale_days": team_cfg.metrics.stale_days,
            },
            releases=releases,
            epic_timeline=epic_timeline,
        ),
        "jira_browse_base": browse_base or None,
        "settings": {
            **team_cfg.settings_public(),
            "jira_browse_base": browse_base or None,
        },
    }


def _clamp_score(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _mood_copy(
    *,
    score: float,
    time_pct: float,
    tasks_pct: float,
    drivers: list[dict],
) -> tuple[str, str, str]:
    """Human, phase-aware recommendation / tone / emoji for team mood."""
    top = drivers[0] if drivers else None
    top_id = (top or {}).get("id")

    if time_pct < 20:
        if drivers and score < 55:
            recommendation = "Старт с напряжением — лучше разобрать риски сразу"
            tone = "warn"
        elif drivers:
            recommendation = "Спринт только начался — есть точки внимания"
            tone = "ok"
        elif tasks_pct >= 15:
            recommendation = "Бойкий старт — так держать"
            tone = "good"
        else:
            recommendation = "Спринт только начался — самое время взять ритм"
            tone = "ok"
    elif time_pct < 50:
        if score >= 78 and not drivers:
            recommendation = "Идём ровно — держим темп"
            tone = "good"
        elif score >= 72:
            recommendation = "Середина спринта: темп есть, не расслабляемся"
            tone = "good"
        elif score >= 55:
            recommendation = "Есть напряжение — ещё можно выровнять курс"
            tone = "ok"
        else:
            recommendation = "Темп проседает — стоит подтянуть фокус"
            tone = "warn"
    elif score >= 88 and not drivers:
        recommendation = "Успели всё сделать"
        tone = "great"
    elif score >= 88:
        recommendation = "Хорошо потрудились"
        tone = "great"
    elif score >= 72:
        recommendation = "Идём по плану"
        tone = "good"
    elif score >= 55:
        recommendation = "Есть напряжение, но держимся"
        tone = "ok"
    else:
        if top_id == "releases_risk":
            recommendation = "Не успеваем в релиз"
        elif top_id == "tasks_stale":
            recommendation = "Много неактивных задач"
        elif top_id in {"schedule_slip", "low_completion_late"}:
            recommendation = "Отстаём от плана спринта"
        elif top_id == "tasks_at_risk":
            recommendation = "Много задач под риском"
        elif top_id == "tasks_no_worklogs":
            recommendation = "Мало списаний по задачам"
        else:
            recommendation = "Спринт под давлением"
        tone = "bad" if score < 40 else "warn"

    if tone == "great" or score >= 88:
        emoji = "🥳"
    elif tone == "good" or score >= 72:
        emoji = "😊"
    elif tone == "ok" or score >= 55:
        emoji = "🙂"
    elif score >= 40:
        emoji = "😐"
    elif score >= 25:
        emoji = "😟"
    else:
        emoji = "😫"

    # Early sprint: never celebrate like a finished race.
    if time_pct < 20 and tone == "great":
        tone = "good"
        emoji = "😊"

    return recommendation, tone, emoji


def _compute_team_mood(
    *,
    sprint: dict,
    risks: dict,
    releases: list[dict],
    epic_timeline: dict,
) -> dict:
    """
    Informational sprint health score 0..100 with emoji + recommendation.

    Combines plan vs fact, task risk panels, release risk, and in-sprint epic work.
    Out-of-sprint epic tasks are ignored for epic risk contribution.
    """
    tasks_pct = float(sprint.get("tasks_progress_pct") or 0.0)
    time_pct = float(sprint.get("time_progress_pct") or 0.0)
    total = int(sprint.get("total") or 0)
    open_n = int(sprint.get("open") or 0)
    done_n = int(sprint.get("done") or 0)
    days_left = sprint.get("days_left")
    urgency = 0.45 + 0.55 * (time_pct / 100.0)

    drivers: list[dict] = []

    # --- Plan vs fact (calendar vs closed tasks) ---
    slip = max(0.0, time_pct - tasks_pct)
    schedule_score = _clamp_score(100.0 - slip * 1.35 * urgency)
    if total > 0 and slip >= 12 and time_pct >= 35:
        drivers.append(
            {
                "id": "schedule_slip",
                "title": "Отстаём от плана спринта",
                "summary": (
                    f"Прошло {time_pct:.0f}% времени спринта, закрыто {tasks_pct:.0f}% "
                    f"задач (разрыв {slip:.0f} п.п., объём {done_n}/{total})."
                ),
                "severity": "danger" if slip >= 28 or time_pct >= 75 else "warn",
                "impact": _round(min(42.0, slip * 0.85 * urgency), 1) or 0.0,
            }
        )
    if time_pct >= 70 and tasks_pct < 30 and total > 0:
        drivers.append(
            {
                "id": "low_completion_late",
                "title": "Мало закрытых задач к концу спринта",
                "summary": (
                    f"К {time_pct:.0f}% прохождения закрыто только {tasks_pct:.0f}% "
                    f"({done_n} из {total})."
                ),
                "severity": "danger",
                "impact": _round(min(35.0, (70.0 - tasks_pct) * 0.45), 1) or 0.0,
            }
        )

    if time_pct < 20:
        completion_score = _clamp_score(55.0 + tasks_pct * 0.4)
    else:
        # Expected closed share ≈ calendar progress; reward catching up / finishing
        expected = time_pct
        completion_score = _clamp_score(
            0.5 * tasks_pct + 0.5 * (100.0 - max(0.0, expected - tasks_pct) * 1.25)
        )
    if time_pct >= 85 and tasks_pct >= 95 and open_n == 0:
        completion_score = 100.0

    # --- Task risk panels ---
    at_risk_n = len(risks.get("at_risk") or [])
    stale_n = len(risks.get("stale") or [])
    no_wl_n = len(risks.get("no_worklogs") or [])
    no_est_n = len(risks.get("no_estimate") or [])
    no_rel_n = len(risks.get("no_release") or [])
    denom = max(open_n, 1)
    risk_penalty = min(
        100.0,
        (at_risk_n / denom) * 55.0
        + (stale_n / denom) * 32.0
        + (no_wl_n / denom) * 28.0
        + min(1.0, no_est_n / denom) * 12.0
        + min(1.0, no_rel_n / denom) * 14.0,
    )
    risk_score = _clamp_score(100.0 - risk_penalty * urgency)
    if at_risk_n:
        drivers.append(
            {
                "id": "tasks_at_risk",
                "title": "Задачи под угрозой срыва срока",
                "summary": f"{at_risk_n} открытых задач могут не закрыться до конца спринта.",
                "severity": "danger" if at_risk_n >= 5 or (at_risk_n / denom) >= 0.35 else "warn",
                "impact": _round(min(30.0, at_risk_n * 2.2 * urgency), 1) or 0.0,
            }
        )
    if stale_n:
        drivers.append(
            {
                "id": "tasks_stale",
                "title": "Много неактивных задач",
                "summary": (
                    f"{stale_n} задач без обновлений "
                    f"≥ {risks.get('stale_days') or 5} дн."
                ),
                "severity": "warn" if (stale_n / denom) < 0.4 else "danger",
                "impact": _round(min(22.0, stale_n * 1.6 * urgency), 1) or 0.0,
            }
        )
    if no_wl_n:
        drivers.append(
            {
                "id": "tasks_no_worklogs",
                "title": "Открыты без списаний",
                "summary": f"{no_wl_n} задач без worklog при позднем спринте.",
                "severity": "warn",
                "impact": _round(min(16.0, no_wl_n * 1.3 * urgency), 1) or 0.0,
            }
        )
    if no_est_n and (no_est_n / denom) >= 0.25:
        drivers.append(
            {
                "id": "tasks_no_estimate",
                "title": "Слабая оценка объёма",
                "summary": (
                    f"{no_est_n} открытых задач без Original Estimate — "
                    "сложнее прогнозировать успеваемость."
                ),
                "severity": "warn",
                "impact": _round(min(12.0, no_est_n * 0.35), 1) or 0.0,
            }
        )
    if no_rel_n and (no_rel_n / denom) >= 0.2:
        drivers.append(
            {
                "id": "tasks_no_release",
                "title": "Задачи без релиза",
                "summary": (
                    f"{no_rel_n} открытых задач без Fix Version — "
                    "сложнее связать работу с целью и релизами."
                ),
                "severity": "warn",
                "impact": _round(min(14.0, no_rel_n * 0.4), 1) or 0.0,
            }
        )

    # --- Releases ---
    active_releases = [r for r in (releases or []) if not r.get("released")]
    risky_releases = [
        r
        for r in active_releases
        if r.get("risk") in {"at_risk", "overdue"}
    ]
    if not active_releases:
        release_score = 88.0 if time_pct < 90 else 92.0
    else:
        ok_share = 1.0 - (len(risky_releases) / len(active_releases))
        avg_slip = 0.0
        slips = [
            float(r.get("slip_gap_pp") or 0.0)
            for r in active_releases
            if (r.get("slip_gap_pp") or 0) > 0
        ]
        if slips:
            avg_slip = sum(slips) / len(slips)
        release_score = _clamp_score(100.0 * ok_share - min(28.0, avg_slip * 0.9))
        if risky_releases:
            names = ", ".join((r.get("name") or "?") for r in risky_releases[:3])
            drivers.append(
                {
                    "id": "releases_risk",
                    "title": "Риски по релизам",
                    "summary": (
                        f"{len(risky_releases)} из {len(active_releases)} активных релизов "
                        f"в зоне риска: {names}."
                    ),
                    "severity": "danger"
                    if any(r.get("risk") == "overdue" for r in risky_releases)
                    else "warn",
                    "impact": _round(
                        min(34.0, 10.0 + len(risky_releases) * 7.0 + avg_slip * 0.4), 1
                    )
                    or 0.0,
                }
            )

    # --- Epics: only in-sprint tasks for risk ---
    epics = (epic_timeline or {}).get("epics") or []
    epic_progress: list[float] = []
    sprint_open = 0
    sprint_risk = 0
    for epic in epics:
        has_sprint_task = False
        for section in epic.get("sections") or []:
            for task in section.get("tasks_detail") or []:
                if task.get("in_sprint") is False:
                    continue
                has_sprint_task = True
                if task.get("direction_state") == "done":
                    continue
                sprint_open += 1
                if task.get("risk"):
                    sprint_risk += 1
        if has_sprint_task:
            epic_progress.append(float(epic.get("progress_pct") or 0.0))
    if epic_progress:
        avg_epic = sum(epic_progress) / len(epic_progress)
        risk_ratio = (sprint_risk / sprint_open) if sprint_open else 0.0
        epic_score = _clamp_score(avg_epic * (1.0 - 0.45 * risk_ratio))
        if risk_ratio >= 0.35 and sprint_risk >= 2:
            drivers.append(
                {
                    "id": "epics_in_sprint_risk",
                    "title": "Риски в эпиках спринта",
                    "summary": (
                        f"{sprint_risk} из {sprint_open} открытых задач эпиков "
                        "в спринте с тегами риска "
                        f"(средний прогресс эпиков {avg_epic:.0f}%)."
                    ),
                    "severity": "warn",
                    "impact": _round(min(18.0, sprint_risk * 1.8), 1) or 0.0,
                }
            )
    else:
        epic_score = 70.0

    # Blend — completion weight grows as sprint advances
    w_schedule = 0.28
    w_completion = 0.14 + 0.16 * (time_pct / 100.0)
    w_risks = 0.24
    w_releases = 0.20
    w_epics = 0.10
    w_sum = w_schedule + w_completion + w_risks + w_releases + w_epics
    score = (
        schedule_score * w_schedule
        + completion_score * w_completion
        + risk_score * w_risks
        + release_score * w_releases
        + epic_score * w_epics
    ) / w_sum
    score = _clamp_score(score)

    # Soft floor/ceiling nudges for extreme end-states
    if total > 0 and open_n == 0 and not risky_releases:
        score = max(score, 92.0)
    if time_pct >= 85 and tasks_pct <= 5 and total > 0 and (at_risk_n or risky_releases):
        score = min(score, 18.0)

    drivers.sort(key=lambda d: (-float(d.get("impact") or 0), d.get("id") or ""))
    # keep top contributors
    drivers = drivers[:8]

    # Early sprint: absence of risks ≠ victory — dampen celebratory scores.
    if time_pct < 25:
        evidence = min(1.0, (tasks_pct / 20.0) + done_n * 0.04)
        neutral = 66.0
        score = _clamp_score(neutral + (score - neutral) * (0.30 + 0.70 * evidence))
        if tasks_pct < 12 and done_n < 3:
            score = min(score, 74.0)

    recommendation, tone, emoji = _mood_copy(
        score=score,
        time_pct=time_pct,
        tasks_pct=tasks_pct,
        drivers=drivers,
    )

    components = {
        "schedule": _round(schedule_score, 0) or 0.0,
        "completion": _round(completion_score, 0) or 0.0,
        "task_risks": _round(risk_score, 0) or 0.0,
        "releases": _round(release_score, 0) or 0.0,
        "epics_in_sprint": _round(epic_score, 0) or 0.0,
    }
    weights_pct = {
        "schedule": _round(100.0 * w_schedule / w_sum, 0) or 0.0,
        "completion": _round(100.0 * w_completion / w_sum, 0) or 0.0,
        "task_risks": _round(100.0 * w_risks / w_sum, 0) or 0.0,
        "releases": _round(100.0 * w_releases / w_sum, 0) or 0.0,
        "epics_in_sprint": _round(100.0 * w_epics / w_sum, 0) or 0.0,
    }
    components_meta = {
        "schedule": {
            "label": "Соблюдение плана",
            "hint": (
                "Насколько % закрытых задач не отстаёт от % прошедшего времени спринта. "
                "100 — идём в ногу или опережаем; ниже — отстаём."
            ),
            "weight_pct": weights_pct["schedule"],
        },
        "completion": {
            "label": "Темп закрытия",
            "hint": (
                "Насколько фактическое закрытие задач соответствует ожидаемому к текущему "
                "моменту спринта. Не «сколько всего закрыли», а «успеваем ли по календарю»."
            ),
            "weight_pct": weights_pct["completion"],
        },
        "task_risks": {
            "label": "Здоровье задач",
            "hint": (
                "Штраф за доли открытых задач с рисками: угроза срока, неактивность, "
                "без списаний, без оценки, без релиза. 100 — таких почти нет."
            ),
            "weight_pct": weights_pct["task_risks"],
        },
        "releases": {
            "label": "Готовность релизов",
            "hint": (
                "Доля активных релизов без риска срыва/просрочки минус среднее отставание "
                "прогресса. Это не «% выпущенных релизов»."
            ),
            "weight_pct": weights_pct["releases"],
        },
        "epics_in_sprint": {
            "label": "Эпики в спринте",
            "hint": (
                "Средний прогресс эпиков с задачами текущего спринта с учётом доли "
                "рисковых задач в них."
            ),
            "weight_pct": weights_pct["epics_in_sprint"],
        },
    }

    return {
        "score": _round(score, 0) or 0.0,
        "emoji": emoji,
        "tone": tone,
        "recommendation": recommendation,
        "drivers": drivers,
        "components": components,
        "components_meta": components_meta,
        "weights": weights_pct,
        "formula_hint": (
            "Итог = взвешенное среднее пяти оценок (каждая 0…100). "
            "Вес «Темпа закрытия» растёт к концу спринта. "
            "В начале спринта высокий балл слегка приглушается, "
            "чтобы отсутствие рисков не выглядело как победа."
        ),
        "context": {
            "tasks_progress_pct": _round(tasks_pct, 0) or 0.0,
            "time_progress_pct": _round(time_pct, 0) or 0.0,
            "tasks_done": done_n,
            "tasks_total": total,
            "tasks_open": open_n,
            "days_left": days_left,
            "active_releases": len(active_releases),
            "epics_in_scope": len(epic_progress),
        },
    }


def compute_report(raw: dict, *, direction_map_raw: str | None = None) -> dict:
    jira_raw = raw.get("jira")
    gitlab_raw = raw.get("gitlab")
    sources = []
    if jira_raw:
        sources.append("jira")
    if gitlab_raw:
        sources.append("gitlab")

    if not jira_raw or not jira_raw.get("sprint"):
        return {
            "meta": {
                "sources": sources,
                "fetched_at": raw.get("fetched_at"),
            },
            "sprint_report": None,
            "error": "Спринт не найден. Проверьте team.json → jira.boards / jira.projects.",
        }

    sprint_report = compute_sprint_report(jira_raw, gitlab_raw)
    return {
        "meta": {
            "sources": sources,
            "fetched_at": raw.get("fetched_at") or jira_raw.get("fetched_at"),
            "board_id": jira_raw.get("board_id"),
            "team_size": len(TEAM_ROSTER),
        },
        "sprint_report": sprint_report,
        "error": None,
    }

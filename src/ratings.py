from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import pstdev
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .team import TEAM_ROSTER, canonical_team_name
from .team_config import get_team_config


def _place_points() -> dict[int, int]:
    return dict(get_team_config().ratings.place_points)


def _top_n() -> int:
    return get_team_config().ratings.top_n


def _is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def _working_days(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        if not _is_weekend(cur):
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if len(text) >= 5 and text[-5] in "+-" and ":" not in text[-5:]:
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _rating_timezone() -> ZoneInfo:
    name = get_team_config().ratings.timezone
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Moscow")


def _workday_start(day: date, tz: ZoneInfo, start_hour: float) -> datetime:
    hour = int(start_hour)
    minute = int(round((start_hour - hour) * 60))
    if minute >= 60:
        hour += 1
        minute = 0
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def _expected_hours_for_day(
    day: date, *, now: datetime, expected_hours: float, start_hour: float
) -> float:
    if day < now.date():
        return expected_hours
    if day > now.date():
        return 0.0
    elapsed = (now - _workday_start(day, now.tzinfo, start_hour)).total_seconds() / 3600.0
    return min(expected_hours, max(0.0, elapsed))


def _next_workday(day: date) -> date:
    cur = day + timedelta(days=1)
    while _is_weekend(cur):
        cur += timedelta(days=1)
    return cur


def _allowed_record_day(work_day: date, grace_workdays: int) -> date:
    allowed = work_day
    for _ in range(max(0, grace_workdays)):
        allowed = _next_workday(allowed)
    return allowed


def _worklog_discipline(
    name: str,
    *,
    worklogs: list[dict],
    now: datetime,
    expected_hours: float,
    start_hour: float,
    grace_workdays: int,
    advance_tolerance_hours: float,
) -> dict:
    """Score timely recording and discourage logging hours before they elapsed."""
    tz = now.tzinfo
    rows: list[dict] = []
    for log in worklogs:
        author = log.get("author") or {}
        raw_name = author.get("displayName") or author.get("name") or author.get("emailAddress")
        if canonical_team_name(raw_name) != name:
            continue
        started = _parse_dt(log.get("started"))
        created = _parse_dt(log.get("created"))
        updated = _parse_dt(log.get("updated"))
        recorded = max((x for x in (created, updated) if x), default=None)
        if not started or not recorded:
            continue
        started_local = started.astimezone(tz)
        recorded_local = recorded.astimezone(tz)
        hours = max(0.0, float(log.get("time_spent_seconds") or 0) / 3600.0)
        if hours <= 0:
            continue
        rows.append(
            {
                "work_day": started_local.date(),
                "recorded": recorded_local,
                "hours": hours,
            }
        )

    total_hours = sum(row["hours"] for row in rows)
    if total_hours <= 0:
        return {
            "score": 100.0,
            "timely_pct": None,
            "advance_hours": 0.0,
            "advance_days": 0,
            "cadence_pct": None,
            "known_hours": 0.0,
        }

    timely_hours = 0.0
    advance_hours = 0.0
    by_day: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        allowed_day = _allowed_record_day(row["work_day"], grace_workdays)
        if row["recorded"].date() <= allowed_day:
            timely_hours += row["hours"]
        by_day[row["work_day"]].append(row)

    cadence_values: list[float] = []
    advance_days = 0
    for work_day, day_rows in by_day.items():
        day_rows.sort(key=lambda x: x["recorded"])
        cumulative = 0.0
        day_advance_peak = 0.0
        record_times: list[datetime] = []
        for row in day_rows:
            cumulative += row["hours"]
            if row["recorded"].date() < work_day:
                # The entry was created before the day it claims to cover.
                day_advance_peak = max(day_advance_peak, cumulative)
                continue
            if row["recorded"].date() != work_day:
                continue
            elapsed = _expected_hours_for_day(
                work_day,
                now=row["recorded"],
                expected_hours=expected_hours,
                start_hour=start_hour,
            )
            day_advance_peak = max(
                day_advance_peak,
                max(0.0, cumulative - elapsed - advance_tolerance_hours),
            )
            record_times.append(row["recorded"])
        if day_advance_peak > 0:
            advance_days += 1
            advance_hours += day_advance_peak
        # A single honest daily entry is neutral. Multiple entries get a small
        # bonus for being spread across the workday, without rewarding splitting.
        if len(record_times) >= 2 and expected_hours > 0:
            span = (record_times[-1] - record_times[0]).total_seconds() / 3600.0
            cadence_values.append(min(1.0, max(0.0, span / (expected_hours * 0.6))))

    timely_pct = timely_hours / total_hours
    advance_ratio = min(1.0, advance_hours / total_hours)
    cadence_pct = (
        sum(cadence_values) / len(cadence_values) if cadence_values else None
    )
    cadence_score = cadence_pct if cadence_pct is not None else 1.0
    score = 100.0 * (
        0.70 * timely_pct + 0.20 * (1.0 - advance_ratio) + 0.10 * cadence_score
    )
    return {
        "score": max(0.0, min(100.0, score)),
        "timely_pct": timely_pct * 100.0,
        "advance_hours": advance_hours,
        "advance_days": advance_days,
        "cadence_pct": cadence_pct * 100.0 if cadence_pct is not None else None,
        "known_hours": total_hours,
    }


def _round(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _estimate_hours(fields: dict) -> float | None:
    seconds = (
        fields.get("timeoriginalestimate")
        or fields.get("aggregatetimeoriginalestimate")
    )
    if seconds is None:
        return None
    return float(seconds) / 3600.0


def _with_places(
    rows: list[dict], *, reverse: bool = True, limit: int | None = None
) -> list[dict]:
    if limit is None:
        limit = _top_n()
    # Optional numeric "tiebreak": used only when scores are equal.
    # For reverse=True (higher score wins) higher tiebreak also wins;
    # for reverse=False the tuple is inverted so lower score wins first.
    def sort_key(row: dict) -> tuple:
        score = float(row.get("score") or 0)
        tie = float(row.get("tiebreak") or 0)
        if reverse:
            return (score, tie)
        return (-score, -tie)

    rows = sorted(rows, key=sort_key, reverse=True)
    result = []
    for idx, row in enumerate(rows[:limit]):
        item = dict(row)
        item["place"] = idx + 1
        result.append(item)
    return result


def _rank_all(rows: list[dict], *, reverse: bool = True) -> list[dict]:
    """Full ranked list (no top-N cut) for category modal."""
    if not rows:
        return []
    return _with_places(rows, reverse=reverse, limit=len(rows))


def _rating_window(
    sprint: dict,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> tuple[datetime, datetime] | None:
    """Exact rating interval: sprint start -> now, capped by sprint end."""
    start = date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else None
    end = date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    if not start or not end:
        return None

    start_at = datetime.combine(start, time.min, tzinfo=tz)
    raw_start = _parse_dt(sprint.get("start_at"))
    if raw_start:
        local_start = raw_start.astimezone(tz)
        if local_start.date() == start:
            start_at = local_start

    end_at = datetime.combine(end, time.max, tzinfo=tz)
    raw_end = _parse_dt(sprint.get("end_at"))
    if raw_end:
        local_end = raw_end.astimezone(tz)
        if local_end.date() == end:
            end_at = local_end

    return start_at, min(now, end_at)


def _canonical_assignee(issue: dict) -> str | None:
    current = issue.get("_canonical_assignee")
    if current:
        return current
    assignee = (issue.get("fields") or {}).get("assignee") or {}
    if not isinstance(assignee, dict):
        return canonical_team_name(str(assignee))
    raw = (
        assignee.get("displayName")
        or assignee.get("name")
        or assignee.get("emailAddress")
    )
    return canonical_team_name(raw)


def completed_tasks_in_window(
    *,
    sprint: dict,
    issues: list[dict],
    changelogs: list[dict] | None,
    now: datetime | None = None,
) -> dict[str, list[dict]]:
    """
    Find tasks that entered a direction's done state inside the rating window.

    The owner is reconstructed from assignee history and taken at the moment of
    the transition. A task already done when the sprint starts is not counted.
    """
    tz = _rating_timezone()
    local_now = now.astimezone(tz) if now else datetime.now(tz)
    window = _rating_window(sprint, now=local_now, tz=tz)
    if not window:
        return {}
    window_start, window_end = window
    if window_end < window_start:
        return {}

    histories_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in changelogs or []:
        key = (row.get("issue_key") or "").upper()
        if key:
            histories_by_key[key].append(row)

    team_cfg = get_team_config()
    result: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        fields = issue.get("fields") or {}
        owner_after = _canonical_assignee(issue)
        rows = sorted(
            histories_by_key.get(key) or [],
            key=lambda row: _parse_dt(row.get("at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for row in rows:
            at = _parse_dt(row.get("at"))
            assignee_changed = (
                row.get("assignee_from") is not None
                or row.get("assignee_to") is not None
            )
            if assignee_changed:
                explicit_after = canonical_team_name(row.get("assignee_to"))
                if explicit_after:
                    owner_after = explicit_after
            owner_before = (
                canonical_team_name(row.get("assignee_from"))
                if assignee_changed
                else owner_after
            )
            owner_at_transition = owner_before or owner_after

            status_to = row.get("status_to")
            status_from = row.get("status_from")
            direction = TEAM_ROSTER.get(owner_at_transition or "")
            to_done = bool(status_to and direction) and team_cfg.is_direction_done(
                direction, status_to, jira_done=False
            )
            from_done = bool(status_from and direction) and team_cfg.is_direction_done(
                direction, status_from, jira_done=False
            )
            if (
                at
                and window_start <= at.astimezone(tz) <= window_end
                and owner_at_transition in TEAM_ROSTER
                and to_done
                and not from_done
            ):
                result[owner_at_transition].append(
                    {
                        "key": key,
                        "summary": fields.get("summary") or row.get("issue_summary") or key,
                        "status": status_to,
                        "completed_at": at.astimezone(tz).isoformat(),
                        "estimate_hours": _round(_estimate_hours(fields), 2),
                    }
                )
                # One rating completion per task: keep its latest done transition.
                break

            if assignee_changed:
                owner_after = owner_before

    for rows in result.values():
        rows.sort(key=lambda row: row.get("completed_at") or "", reverse=True)
    return dict(result)


def _count_commits_from_gitlab(
    gitlab_raw: dict | None,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[dict[str, int], dict[str, int]]:
    """
    Count commits by commit author (not MR author).
    helper = commits in projects of another direction.
    """
    commits_by_person: dict[str, int] = defaultdict(int)
    helper_commits: dict[str, int] = defaultdict(int)
    if not gitlab_raw:
        return commits_by_person, helper_commits

    team_cfg = get_team_config()
    project_dir_keys = dict(team_cfg.gitlab_projects_keys)

    for project in gitlab_raw.get("projects") or []:
        project_ref = project.get("ref") or ""
        mr_direction_key = project_dir_keys.get(project_ref)
        if not mr_direction_key:
            # try by display-name map
            name = team_cfg.gitlab_projects.get(project_ref)
            mr_direction_key = team_cfg.resolve_direction_key(name)

        for bucket in (
            project.get("merge_requests_merged") or [],
            project.get("merge_requests_open") or [],
        ):
            for mr in bucket:
                for commit in mr.get("commit_events") or []:
                    committed = _parse_dt(commit.get("committed_at"))
                    if not committed:
                        continue
                    committed = committed.astimezone(window_start.tzinfo)
                    if committed < window_start or committed > window_end:
                        continue
                    author_name = commit.get("author")
                    author = canonical_team_name(author_name)
                    if not author:
                        continue
                    commits_by_person[author] += 1
                    person_key = team_cfg.direction_key_for_person(author)
                    if (
                        mr_direction_key
                        and person_key
                        and mr_direction_key != person_key
                    ):
                        helper_commits[author] += 1

    return commits_by_person, helper_commits


def compute_ratings(
    *,
    sprint: dict,
    team_rows: list[dict],
    issues: list[dict],
    hours_by_person_day: dict[str, dict[str, float]],
    hours_by_issue: dict[str, float],
    hours_by_person_issue: dict[str, dict[str, float]] | None = None,
    worklogs: list[dict] | None = None,
    changelogs: list[dict] | None = None,
    links: dict,
    gitlab_raw: dict | None,
    expected_hours: float,
    now: datetime | None = None,
) -> list[dict]:
    rating_cfg = get_team_config().ratings
    tz = _rating_timezone()
    now = now.astimezone(tz) if now else datetime.now(tz)
    today = now.date()
    start = date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else None
    end = date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    if not start or not end:
        return []

    team_cfg = get_team_config()
    all_workdays = _working_days(start, end)
    elapsed_workdays = 0.0
    for workday in all_workdays:
        if workday < today:
            elapsed_workdays += 1.0
        elif workday == today and expected_hours > 0:
            elapsed_workdays += _expected_hours_for_day(
                workday,
                now=now,
                expected_hours=expected_hours,
                start_hour=rating_cfg.workday_start_hour,
            ) / expected_hours
    half_passed = bool(all_workdays) and elapsed_workdays / len(all_workdays) >= 0.5
    work_until = min(end, today)
    workdays = _working_days(start, work_until)

    window = _rating_window(sprint, now=now, tz=tz)
    if not window:
        return []
    window_start, window_end = window
    commits_by_person, helper_commits = _count_commits_from_gitlab(
        gitlab_raw,
        window_start=window_start,
        window_end=window_end,
    )

    person_issue_hours = hours_by_person_issue or {}
    completed_by_person = completed_tasks_in_window(
        sprint=sprint,
        issues=issues,
        changelogs=changelogs,
        now=now,
    )
    closed_by_person: dict[str, int] = defaultdict(int)
    closed_estimate_by_person: dict[str, float] = defaultdict(float)
    over_estimate: dict[str, float] = defaultdict(float)
    under_estimate_closed: dict[str, int] = defaultdict(int)

    for person, completed in completed_by_person.items():
        closed_by_person[person] = len(completed)
        for task in completed:
            key = task.get("key") or ""
            spent = float(person_issue_hours.get(person, {}).get(key, 0.0))
            estimate = task.get("estimate_hours")
            task["hours"] = _round(spent, 2) or 0.0
            if estimate is not None and estimate > 0:
                closed_estimate_by_person[person] += float(estimate)
            if estimate is not None and spent > 0 and spent < estimate:
                under_estimate_closed[person] += 1
            if estimate is not None and spent > estimate:
                over_estimate[person] += spent - estimate

    categories: list[dict] = []
    prize_points: dict[str, int] = defaultdict(int)
    prize_places: dict[str, int] = defaultdict(int)

    def track_prizes(people: list[dict]) -> None:
        for person in people:
            name = person.get("name")
            place = int(person.get("place") or 0)
            points = _place_points()
            if not name or place not in points:
                continue
            prize_places[name] += 1
            prize_points[name] += points[place]

    # 1) Человек-стабильность: pace vs elapsed work time + worklog discipline.
    stability_rows = []
    for name in TEAM_ROSTER:
        daily = []
        day_targets = []
        for day in workdays:
            daily.append(hours_by_person_day.get(name, {}).get(day.isoformat(), 0.0))
            day_targets.append(
                _expected_hours_for_day(
                    day,
                    now=now,
                    expected_hours=expected_hours,
                    start_hour=rating_cfg.workday_start_hour,
                )
            )
        if not workdays:
            continue
        # Current day target grows with elapsed working time; completed days use full norm.
        day_scores = []
        for hours, target in zip(daily, day_targets):
            if target <= 0:
                # Before the workday starts, no hours is correct; advance logs are not.
                day_scores.append(1.0 if hours <= 0 else 0.0)
                continue
            ratio = hours / target
            day_scores.append(max(0.0, 1.0 - abs(ratio - 1.0)))
        mean_day = sum(day_scores) / len(day_scores)
        normalized = [
            hours / target if target > 0 else (0.0 if hours <= 0 else 2.0)
            for hours, target in zip(daily, day_targets)
        ]
        spread = pstdev(normalized) if len(normalized) > 1 else 0.0
        discipline = _worklog_discipline(
            name,
            worklogs=worklogs or [],
            now=now,
            expected_hours=expected_hours,
            start_hour=rating_cfg.workday_start_hour,
            grace_workdays=rating_cfg.worklog_grace_workdays,
            advance_tolerance_hours=rating_cfg.advance_tolerance_hours,
        )
        # Daily pace dominates; actual worklog timestamps provide anti-gaming checks.
        score = mean_day * 65.0 + discipline["score"] * 0.35 - spread * 8.0
        closed_n = int(closed_by_person.get(name, 0))
        timely = discipline.get("timely_pct")
        timely_text = "—" if timely is None else f"{_round(timely, 0)}%"
        stability_rows.append(
            {
                "name": name,
                "direction": TEAM_ROSTER[name],
                "score": score,
                "tiebreak": closed_n,
                "value": f"{_round(score, 0)} баллов",
                "detail": (
                    f"темп {_round(mean_day * 100.0, 0)}% · "
                    f"вовремя {timely_text} · "
                    f"наперёд {_round(discipline.get('advance_hours') or 0.0, 1)} ч "
                    f"за {int(discipline.get('advance_days') or 0)} дн."
                ),
                "stability": {
                    "pace_pct": _round(mean_day * 100.0, 1),
                    "timely_pct": _round(timely, 1),
                    "advance_hours": _round(discipline.get("advance_hours"), 2),
                    "advance_days": int(discipline.get("advance_days") or 0),
                    "cadence_pct": _round(discipline.get("cadence_pct"), 1),
                    "spread": _round(spread, 2),
                    "closed_tasks": closed_n,
                },
            }
        )
    stability_top = _with_places(stability_rows)
    track_prizes(stability_top)
    categories.append(
        {
            "id": "stability",
            "title": "Человек-стабильность",
            "description": (
                "Темп списаний относительно уже прошедшего рабочего времени; "
                "учитываются задние и преждевременные worklog. "
                "При равном балле выше тот, у кого больше задач завершено "
                "после начала спринта"
            ),
            "enabled": True,
            "people": stability_top,
            "all_people": _rank_all(stability_rows),
        }
    )

    # 2) Коммитёр — по авторам коммитов (все направления)
    committer_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": commits_by_person.get(name, 0),
            "value": f"{commits_by_person.get(name, 0)} комм.",
            "detail": TEAM_ROSTER[name],
        }
        for name in TEAM_ROSTER
        if commits_by_person.get(name, 0) > 0
    ]
    committer_top = _with_places(committer_rows)
    track_prizes(committer_top)
    categories.append(
        {
            "id": "committer",
            "title": "Коммитёр",
            "description": (
                "Больше всех коммитов с committed date внутри текущего спринта "
                "(по автору коммита)"
            ),
            "enabled": len(committer_rows) >= 1,
            "people": committer_top,
            "all_people": _rank_all(committer_rows),
        }
    )

    # 3) Статист — закрытие по status_rules направления, вся команда
    statist_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": closed_by_person.get(name, 0),
            "value": f"{closed_by_person.get(name, 0)} задач",
            "detail": TEAM_ROSTER[name],
            "tasks": list(completed_by_person.get(name) or []),
        }
        for name in TEAM_ROSTER
        if closed_by_person.get(name, 0) > 0
    ]
    statist_top = _with_places(statist_rows) if len(list(TEAM_ROSTER)) >= 3 else []
    track_prizes(statist_top)
    categories.append(
        {
            "id": "closer",
            "title": "Статист",
            "description": (
                "Больше всех завершил задач после начала спринта "
                "(по changelog и правилам статуса направления)"
            ),
            "enabled": len(list(TEAM_ROSTER)) >= 3,
            "people": statist_top,
            "all_people": _rank_all(statist_rows),
        }
    )

    # Эффективность — закрытый Original Estimate / часы worklog
    min_hours = 4.0
    efficiency_rows = []
    for name in TEAM_ROSTER:
        hours = float(sum(hours_by_person_day.get(name, {}).values()))
        est_closed = float(closed_estimate_by_person.get(name, 0.0))
        if hours < min_hours or est_closed <= 0:
            continue
        score = est_closed / hours
        closed_n = int(closed_by_person.get(name, 0))
        efficiency_rows.append(
            {
                "name": name,
                "direction": TEAM_ROSTER[name],
                "score": score,
                "tiebreak": closed_n,
                "value": f"{_round(score, 2)} оценки на час",
                "detail": (
                    f"оценка {_round(est_closed, 1)} ч · "
                    f"списано {_round(hours, 1)} ч · "
                    f"закрыто {closed_n}"
                ),
                "tasks": list(completed_by_person.get(name) or []),
            }
        )
    efficiency_top = _with_places(efficiency_rows)
    track_prizes(efficiency_top)
    categories.append(
        {
            "id": "efficiency",
            "title": "Эффективность",
            "description": (
                "Сколько часов оценки завершённых в этом спринте задач приходится "
                "на 1 час списаний спринта "
                f"(минимум {min_hours:.0f} ч списаний). "
                "При равном score выше тот, у кого больше задач завершено в спринте"
            ),
            "enabled": len(efficiency_rows) >= 1,
            "people": efficiency_top,
            "all_people": _rank_all(efficiency_rows),
        }
    )

    underestimator_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": over_estimate.get(name, 0.0),
            "value": f"+{_round(over_estimate.get(name, 0.0), 1)} ч",
            "detail": "списал больше оценки",
            "tasks": [
                task
                for task in (completed_by_person.get(name) or [])
                if task.get("estimate_hours") is not None
                and float(task.get("hours") or 0.0) > float(task["estimate_hours"])
            ],
        }
        for name in TEAM_ROSTER
        if over_estimate.get(name, 0.0) > 0
    ]
    under_top = _with_places(underestimator_rows) if half_passed else []
    track_prizes(under_top)
    categories.append(
        {
            "id": "underestimator",
            "title": "Недооценщик",
            "description": (
                "Больше всех списал в спринте сверх Original Estimate по задачам, "
                "завершённым в этом спринте"
            ),
            "enabled": half_passed,
            "people": under_top,
            "all_people": _rank_all(underestimator_rows),
        }
    )

    overestimator_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": under_estimate_closed.get(name, 0),
            "value": f"{under_estimate_closed.get(name, 0)} задач",
            "detail": TEAM_ROSTER[name],
            "tasks": [
                task
                for task in (completed_by_person.get(name) or [])
                if task.get("estimate_hours") is not None
                and 0 < float(task.get("hours") or 0.0) < float(task["estimate_hours"])
            ],
        }
        for name in TEAM_ROSTER
        if under_estimate_closed.get(name, 0) > 0
    ]
    over_top = _with_places(overestimator_rows) if half_passed else []
    track_prizes(over_top)
    categories.append(
        {
            "id": "overestimator",
            "title": "Переоценщик",
            "description": (
                "Завершил в этом спринте больше задач со списанием спринта ниже оценки"
            ),
            "enabled": half_passed,
            "people": over_top,
            "all_people": _rank_all(overestimator_rows),
        }
    )

    truant_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": sum(hours_by_person_day.get(name, {}).values()),
            "value": f"{_round(sum(hours_by_person_day.get(name, {}).values()), 1)} ч",
            "detail": TEAM_ROSTER[name],
        }
        for name in TEAM_ROSTER
    ]
    truant_top = _with_places(truant_rows, reverse=False) if half_passed else []
    track_prizes(truant_top)
    categories.append(
        {
            "id": "truant",
            "title": "Прогульщик",
            "description": "Меньше всех списывал время в спринте",
            "enabled": half_passed,
            "people": truant_top,
            "all_people": _rank_all(truant_rows, reverse=False),
        }
    )

    helper_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": helper_commits.get(name, 0),
            "value": f"{helper_commits.get(name, 0)} комм.",
            "detail": TEAM_ROSTER[name],
        }
        for name in TEAM_ROSTER
        if helper_commits.get(name, 0) > 0
    ]
    helper_top = _with_places(helper_rows)
    track_prizes(helper_top)
    categories.append(
        {
            "id": "helper",
            "title": "Помогальщик",
            "description": (
                "Больше всех коммитов с датой внутри спринта не в своём направлении "
                "(по автору коммита)"
            ),
            "enabled": len(helper_rows) >= 1,
            "people": helper_top,
            "all_people": _rank_all(helper_rows),
        }
    )

    # Топы — золото 3 / серебро 2 / бронза 1
    tops_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": prize_points[name],
            "value": f"{prize_points[name]} очк.",
            "detail": f"{prize_places[name]} мест · 3/2/1",
        }
        for name in TEAM_ROSTER
        if prize_points.get(name, 0) > 0
    ]
    tops_top = _with_places(tops_rows)
    categories.append(
        {
            "id": "tops",
            "title": "Топы",
            "description": "Сумма очков за места: золото 3, серебро 2, бронза 1",
            "enabled": len(tops_rows) >= 1,
            "people": tops_top,
            "all_people": _rank_all(tops_rows),
        }
    )

    return categories

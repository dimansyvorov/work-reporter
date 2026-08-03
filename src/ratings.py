from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import pstdev

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
    rows = sorted(rows, key=lambda r: r["score"], reverse=reverse)
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


def _count_commits_from_gitlab(gitlab_raw: dict | None) -> tuple[dict[str, int], dict[str, int]]:
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
                by_author = mr.get("commits_by_author") or {}
                if by_author:
                    author_counts = by_author.items()
                else:
                    # fallback: whole MR count to MR author
                    author_raw = mr.get("author") or {}
                    author_name = (
                        author_raw.get("name")
                        or author_raw.get("username")
                        or (author_raw if isinstance(author_raw, str) else None)
                    )
                    count = int(mr.get("commit_count") or 0)
                    author_counts = [(author_name, count)] if author_name and count else []

                for author_name, count in author_counts:
                    count = int(count or 0)
                    if count <= 0:
                        continue
                    author = canonical_team_name(author_name)
                    if not author:
                        continue
                    commits_by_person[author] += count
                    person_key = team_cfg.direction_key_for_person(author)
                    if (
                        mr_direction_key
                        and person_key
                        and mr_direction_key != person_key
                    ):
                        helper_commits[author] += count

    return commits_by_person, helper_commits


def compute_ratings(
    *,
    sprint: dict,
    team_rows: list[dict],
    issues: list[dict],
    hours_by_person_day: dict[str, dict[str, float]],
    hours_by_issue: dict[str, float],
    hours_by_person_issue: dict[str, dict[str, float]] | None = None,
    links: dict,
    gitlab_raw: dict | None,
    expected_hours: float,
) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    start = date.fromisoformat(sprint["start_date"]) if sprint.get("start_date") else None
    end = date.fromisoformat(sprint["end_date"]) if sprint.get("end_date") else None
    if not start or not end:
        return []

    team_cfg = get_team_config()
    day_total = max((end - start).days + 1, 1)
    day_index = 0 if today < start else day_total if today > end else (today - start).days + 1
    half_passed = day_index / day_total >= 0.5
    work_until = min(end, today)
    workdays = _working_days(start, work_until)

    commits_by_person, helper_commits = _count_commits_from_gitlab(gitlab_raw)

    person_issue_hours = hours_by_person_issue or {}
    closed_by_person: dict[str, int] = defaultdict(int)
    over_estimate: dict[str, float] = defaultdict(float)
    under_estimate_closed: dict[str, int] = defaultdict(int)

    for issue in issues:
        fields = issue.get("fields") or {}
        person = issue.get("_canonical_assignee")
        direction = issue.get("_direction")
        if not person:
            continue
        key = (issue.get("key") or "").upper()
        spent = float(person_issue_hours.get(person, {}).get(key, 0.0))
        estimate = _estimate_hours(fields)
        status_name = ((fields.get("status") or {}).get("name"))
        jira_done = bool(fields.get("resolution")) or (
            ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        )
        direction_done = team_cfg.is_direction_done(
            direction, status_name, jira_done=jira_done
        )
        if direction_done:
            closed_by_person[person] += 1
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

    # 1) Человек-стабильность — ежедневная близость к норме, штраф за «свалку» в конце
    stability_rows = []
    for name in TEAM_ROSTER:
        daily = []
        for day in workdays:
            daily.append(hours_by_person_day.get(name, {}).get(day.isoformat(), 0.0))
        if not workdays:
            continue
        # Per-day score: 1.0 at exactly expected, 0 at 0h or ≥2× expected
        day_scores = []
        for hours in daily:
            if expected_hours <= 0:
                day_scores.append(0.0)
                continue
            ratio = hours / expected_hours
            day_scores.append(max(0.0, 1.0 - abs(ratio - 1.0)))
        mean_day = sum(day_scores) / len(day_scores)
        spread = pstdev(daily) if len(daily) > 1 else 0.0
        total_hours = sum(daily)
        # Binge penalty: large share of hours dumped into last 1–2 workdays
        tail = sum(daily[-2:]) if len(daily) >= 2 else total_hours
        binge = (tail / total_hours) if total_hours > 0 else 0.0
        score = mean_day * 100.0 - spread * 2.0 - binge * 25.0
        stability_rows.append(
            {
                "name": name,
                "direction": TEAM_ROSTER[name],
                "score": score,
                "value": f"{_round(mean_day * 100.0, 0)}% к норме",
                "detail": f"σ={_round(spread, 1)} · хвост {_round(binge * 100.0, 0)}%",
            }
        )
    stability_top = _with_places(stability_rows)
    track_prizes(stability_top)
    categories.append(
        {
            "id": "stability",
            "title": "Человек-стабильность",
            "description": "Близость ежедневных списаний к норме; штраф за свалку в конце спринта",
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
            "description": "Больше всех коммитов за спринт (по автору коммита)",
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
            "description": "Больше всех закрывает задачи (по правилам статуса направления)",
            "enabled": len(list(TEAM_ROSTER)) >= 3,
            "people": statist_top,
            "all_people": _rank_all(statist_rows),
        }
    )

    underestimator_rows = [
        {
            "name": name,
            "direction": TEAM_ROSTER[name],
            "score": over_estimate.get(name, 0.0),
            "value": f"+{_round(over_estimate.get(name, 0.0), 1)} ч",
            "detail": "списал больше оценки",
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
            "description": "Больше всех списал выше оценки задач",
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
            "description": "Закрыл больше задач со списанием ниже оценки",
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
            "description": "Больше всех коммитов не в своём направлении (по автору коммита)",
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

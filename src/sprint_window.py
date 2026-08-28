from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


_NAME_RANGE_RE = re.compile(
    r"(?<!\d)(?P<start>\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4}))"
    r"\s*[-–—]\s*"
    r"(?P<end>\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4}))(?!\d)"
)


@dataclass(frozen=True)
class SprintWindow:
    start: date | None
    end: date | None
    source: str


def _parse_name_day(value: str) -> date | None:
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(part) for part in parts)
        if year < 100:
            year += 2000
        return date(year, month, day)
    except ValueError:
        return None


def sprint_name_window(name: str | None) -> SprintWindow | None:
    """Read an explicit dd.mm.yy–dd.mm.yy interval from a sprint name."""
    match = _NAME_RANGE_RE.search(str(name or ""))
    if not match:
        return None
    start = _parse_name_day(match.group("start"))
    end = _parse_name_day(match.group("end"))
    if not start or not end or end < start or (end - start).days > 45:
        return None
    return SprintWindow(start=start, end=end, source="name")


def _fixed_timezone(utc_offset: float):
    return timezone(timedelta(hours=float(utc_offset)))


def _local_day(value: datetime | None, utc_offset: float) -> date | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(_fixed_timezone(utc_offset)).date()


def _local_datetime(value: datetime | None, utc_offset: float) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(_fixed_timezone(utc_offset))


def normalize_sprint_window(
    *,
    name: str | None,
    start: datetime | None,
    end: datetime | None,
    utc_offset: float = 3.0,
) -> SprintWindow:
    """Return one inclusive sprint interval used by all report calculations.

    An explicit interval in the sprint name is the business source of truth. If
    it is absent, Jira dates are used and a midnight end is treated as the
    exclusive boundary at the start of the following day.
    """
    named = sprint_name_window(name)
    if named:
        return named

    start_day = _local_day(start, utc_offset)
    end_day = _local_day(end, utc_offset)
    local_end = _local_datetime(end, utc_offset)
    raw_midnight = end is not None and not any(
        (end.hour, end.minute, end.second, end.microsecond)
    )
    local_midnight = local_end is not None and not any(
        (local_end.hour, local_end.minute, local_end.second, local_end.microsecond)
    )
    if raw_midnight or local_midnight:
        end_day = (end_day - timedelta(days=1)) if end_day else None
    if start_day and end_day and end_day < start_day:
        end_day = start_day
    return SprintWindow(start=start_day, end=end_day, source="jira")


def sprint_business_state(sprint: dict, *, today: date) -> tuple[str, str]:
    """Derive a display state from the business interval, not stale Jira state."""
    raw_state = str(sprint.get("state") or "unknown").lower()
    try:
        start = date.fromisoformat(str(sprint.get("start_date")))
    except (TypeError, ValueError):
        start = None
    try:
        end = date.fromisoformat(str(sprint.get("end_date")))
    except (TypeError, ValueError):
        end = None

    if end and today > end:
        return "closed", "Завершён"
    if start and today < start:
        return "future", "Будущий"
    if raw_state == "closed":
        return "closed", "Завершён"
    if raw_state == "future":
        return "future", "Будущий"
    if start and end and start <= today <= end:
        return "active", "Активен"
    labels = {"active": "Активен", "closed": "Завершён", "future": "Будущий"}
    return raw_state, labels.get(raw_state, raw_state or "—")

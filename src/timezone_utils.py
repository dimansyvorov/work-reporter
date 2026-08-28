from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo

from .team_config import get_team_config


def fixed_timezone(offset_hours: float) -> tzinfo:
    """Build a fixed UTC-offset timezone from numeric team.json settings."""
    return timezone(timedelta(hours=float(offset_hours)))


def server_timezone() -> tzinfo:
    return fixed_timezone(get_team_config().server_timezone)


def person_timezone(name: str | None) -> tzinfo:
    return fixed_timezone(get_team_config().timezone_offset_for(name))


def localize(value: datetime | None, tz: tzinfo) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    return current.astimezone(tz)


def server_now(value: datetime | None = None) -> datetime:
    return localize(value, server_timezone())


def person_now(name: str | None, value: datetime | None = None) -> datetime:
    return localize(value, person_timezone(name))


def server_today(value: datetime | None = None) -> date:
    return server_now(value).date()

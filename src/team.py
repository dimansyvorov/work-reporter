from __future__ import annotations

from .team_config import get_team_config


class _RosterProxy:
    def __getitem__(self, key):
        return get_team_config().roster[key]

    def __contains__(self, key):
        return key in get_team_config().roster

    def keys(self):
        return get_team_config().roster.keys()

    def values(self):
        return get_team_config().roster.values()

    def items(self):
        return get_team_config().roster.items()

    def get(self, key, default=None):
        return get_team_config().roster.get(key, default)

    def __iter__(self):
        return iter(get_team_config().roster)

    def __len__(self):
        return len(get_team_config().roster)


class _OrderProxy:
    def __iter__(self):
        return iter(get_team_config().direction_order)

    def __len__(self):
        return len(get_team_config().direction_order)

    def __getitem__(self, idx):
        return get_team_config().direction_order[idx]


class _DevProxy:
    def __contains__(self, item):
        return item in get_team_config().dev_directions

    def __iter__(self):
        return iter(get_team_config().dev_directions)

    def __len__(self):
        return len(get_team_config().dev_directions)


TEAM_ROSTER = _RosterProxy()
DIRECTION_ORDER = _OrderProxy()
DEV_DIRECTIONS = _DevProxy()


def canonical_team_name(name: str | None) -> str | None:
    """Resolve a free-form name to a unique roster entry; None if ambiguous/unknown."""
    return get_team_config().canonical_name(name)


def direction_for_person(name: str | None) -> str | None:
    return get_team_config().direction_for_person(name)


def is_team_member(name: str | None) -> bool:
    return get_team_config().is_team_member(name)

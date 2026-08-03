from __future__ import annotations

from .people import names_match
from .team_config import get_team_config

OTHER_DIRECTION = "Прочее"


def _default_map() -> dict[str, str]:
    return dict(get_team_config().gitlab_projects)


# Back-compat for imports that expect a dict-like constant
class _MapProxy(dict):
    def copy(self):
        return dict(_default_map())

    def get(self, key, default=None):
        return _default_map().get(key, default)

    def items(self):
        return _default_map().items()

    def keys(self):
        return _default_map().keys()

    def values(self):
        return _default_map().values()

    def __contains__(self, key):
        return key in _default_map()

    def __iter__(self):
        return iter(_default_map())

    def __len__(self):
        return len(_default_map())

    def __getitem__(self, key):
        return _default_map()[key]


DEFAULT_DIRECTION_MAP = _MapProxy()


def parse_direction_map(raw: str | None) -> dict[str, str]:
    """
    Format: project/path:Название,other/path:Название
    Env override merges over team.json gitlab_projects.
    """
    mapping = _default_map()
    if not raw or not raw.strip():
        return mapping

    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        project, title = part.split(":", 1)
        project = project.strip()
        title = title.strip()
        if project and title:
            mapping[project] = title
    return mapping or _default_map()


def direction_for_projects(
    project_refs: list[str],
    direction_map: dict[str, str],
) -> str:
    counts: dict[str, int] = {}
    for ref in project_refs:
        title = direction_map.get(ref)
        if not title:
            for key, value in direction_map.items():
                if ref.endswith(key) or key.endswith(ref):
                    title = value
                    break
        if title:
            counts[title] = counts.get(title, 0) + 1
    if not counts:
        return OTHER_DIRECTION
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]


def assign_direction(
    person_name: str,
    *,
    gitlab_people: list[dict],
    direction_map: dict[str, str],
) -> str:
    for person in gitlab_people:
        if names_match(person_name, person.get("name") or ""):
            return direction_for_projects(person.get("projects") or [], direction_map)
    return OTHER_DIRECTION

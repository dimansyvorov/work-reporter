from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from .errors import CollectError
from .people import names_match

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "team.json"


def _norm_status(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


@dataclass
class StatusRules:
    active: set[str] = field(default_factory=set)
    done: set[str] = field(default_factory=set)

    @classmethod
    def from_raw(cls, raw: dict | None) -> "StatusRules":
        raw = raw or {}
        return cls(
            active={_norm_status(x) for x in (raw.get("active") or []) if str(x).strip()},
            done={_norm_status(x) for x in (raw.get("done") or []) if str(x).strip()},
        )


@dataclass
class DirectionInfo:
    key: str
    name: str
    is_dev: bool = False
    color: str = "#5d6f63"
    short: str = ""


@dataclass
class MetricsSettings:
    release_window_days: int = 14
    slip_tolerance_pp: float = 12.0
    hours_warn_ratio: float = 0.5
    risk_sprint_time_pct: float = 70.0
    risk_days_left: int = 3
    stale_days: int = 3
    epic_bar_min_pct: float = 28.0
    epic_section_min_pct: float = 14.0
    risks_limit: int = 40
    person_tasks_limit: int = 40
    person_active_tasks_limit: int = 30


@dataclass
class RatingsSettings:
    top_n: int = 3
    place_points: dict[int, int] = field(default_factory=lambda: {1: 3, 2: 2, 3: 1})


@dataclass
class UiSettings:
    task_table_preview: int = 5


@dataclass
class JiraBoardConfig:
    """One Jira Agile board to pull issues from (scrum sprint or kanban)."""

    id: str = ""
    name: str = ""
    primary: bool = False
    has_epics: bool = True
    # False for kanban / boards without Agile sprints API
    has_sprints: bool = True


@dataclass
class TeamConfig:
    # key -> info
    directions: dict[str, DirectionInfo] = field(default_factory=dict)
    # display name -> key
    name_to_key: dict[str, str] = field(default_factory=dict)
    # person display name -> direction key
    roster_keys: dict[str, str] = field(default_factory=dict)
    # person display name -> direction display name (compat)
    roster: dict[str, str] = field(default_factory=dict)
    direction_order: list[str] = field(default_factory=list)  # display names
    direction_keys_order: list[str] = field(default_factory=list)
    dev_directions: set[str] = field(default_factory=set)  # display names
    dev_direction_keys: set[str] = field(default_factory=set)
    # project ref -> direction key (also resolved map to names)
    gitlab_projects: dict[str, str] = field(default_factory=dict)  # ref -> display name
    gitlab_projects_keys: dict[str, str] = field(default_factory=dict)  # ref -> key
    # Fallback lookback for GitLab when Jira sprint start is unknown
    gitlab_days: int = 30
    jira_projects: list[str] = field(default_factory=list)
    jira_boards: list[JiraBoardConfig] = field(default_factory=list)
    jira_jql: str = ""
    display_task_filters: list[str] = field(default_factory=list)
    status_rules: dict[str, StatusRules] = field(default_factory=dict)  # by key + default
    inactive_days: int = 3
    # alias (gitlab/jira nickname) -> canonical roster name
    aliases: dict[str, str] = field(default_factory=dict)
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    ratings: RatingsSettings = field(default_factory=RatingsSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    path: Path | None = None

    @property
    def primary_jira_board(self) -> JiraBoardConfig | None:
        if not self.jira_boards:
            return None
        for board in self.jira_boards:
            if board.primary:
                return board
        return self.jira_boards[0]

    def resolve_direction_key(self, value: str | None) -> str | None:
        if not value:
            return None
        text = value.strip()
        if text in self.directions:
            return text
        if text in self.name_to_key:
            return self.name_to_key[text]
        # case-insensitive name match
        lowered = text.lower()
        for name, key in self.name_to_key.items():
            if name.lower() == lowered:
                return key
        return None

    def direction_name(self, key_or_name: str | None) -> str | None:
        key = self.resolve_direction_key(key_or_name)
        if not key:
            return key_or_name
        return self.directions[key].name

    def direction_color(self, key_or_name: str | None) -> str:
        key = self.resolve_direction_key(key_or_name)
        if key and key in self.directions:
            return self.directions[key].color
        return "#5d6f63"

    def direction_short(self, key_or_name: str | None) -> str:
        key = self.resolve_direction_key(key_or_name)
        if key and key in self.directions:
            info = self.directions[key]
            return info.short or info.name
        return (key_or_name or "").strip() or "—"

    def settings_public(self) -> dict:
        """Subset of config exposed to the UI via sprint report."""
        return {
            "direction_shorts": {
                info.name: info.short or info.name for info in self.directions.values()
            },
            "inactive_days": self.inactive_days,
            "metrics": {
                "release_window_days": self.metrics.release_window_days,
                "slip_tolerance_pp": self.metrics.slip_tolerance_pp,
                "hours_warn_ratio": self.metrics.hours_warn_ratio,
                "risk_sprint_time_pct": self.metrics.risk_sprint_time_pct,
                "risk_days_left": self.metrics.risk_days_left,
                "stale_days": self.metrics.stale_days,
                "epic_bar_min_pct": self.metrics.epic_bar_min_pct,
                "epic_section_min_pct": self.metrics.epic_section_min_pct,
                "risks_limit": self.metrics.risks_limit,
            },
            "ratings": {
                "top_n": self.ratings.top_n,
                "place_points": {
                    str(k): v for k, v in sorted(self.ratings.place_points.items())
                },
            },
            "ui": {
                "task_table_preview": self.ui.task_table_preview,
            },
        }

    def canonical_name(self, name: str | None) -> str | None:
        if not name:
            return None
        # 1) exact / normalized exact
        needle = name.strip()
        if needle in self.roster:
            return needle
        from .people import name_tokens, normalize_name, _transliterate

        norm = normalize_name(needle)
        translit = _transliterate(norm)

        # 2) explicit aliases from team.json (gitlab login / english name)
        if needle in self.aliases:
            return self.aliases[needle]
        for alias, canonical in self.aliases.items():
            an = normalize_name(alias)
            if an == norm or _transliterate(an) == translit:
                return canonical

        exact = []
        fuzzy = []
        for canonical in self.roster:
            cn = normalize_name(canonical)
            if cn == norm or _transliterate(cn) == translit:
                exact.append(canonical)
            elif names_match(needle, canonical):
                fuzzy.append(canonical)
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None
        if len(fuzzy) != 1:
            return None
        # Extra guard: Russian roster is «Фамилия Имя …» — require that surname
        # token actually appears in the free-form name (blocks weak matches).
        hit = fuzzy[0]
        roster_tokens = name_tokens(hit)
        needle_tokens = name_tokens(needle)
        if roster_tokens and needle_tokens:
            from .people import _token_equiv

            roster_surname = roster_tokens[0]
            if len(roster_surname) >= 4 and not any(
                _token_equiv(roster_surname, t) for t in needle_tokens
            ):
                return None
        return hit

    def direction_for_person(self, name: str | None) -> str | None:
        canonical = self.canonical_name(name)
        if not canonical:
            return None
        return self.roster[canonical]

    def direction_key_for_person(self, name: str | None) -> str | None:
        canonical = self.canonical_name(name)
        if not canonical:
            return None
        return self.roster_keys.get(canonical)

    def is_team_member(self, name: str | None) -> bool:
        return self.canonical_name(name) is not None

    def is_hidden_from_display(self, summary: str | None) -> bool:
        text = (summary or "").strip()
        if not text or not self.display_task_filters:
            return False
        for pattern in self.display_task_filters:
            pat = pattern.strip()
            if not pat:
                continue
            if fnmatch(text, pat) or fnmatch(text.lower(), pat.lower()):
                return True
            if "*" not in pat and text.lower().startswith(pat.lower()):
                return True
        return False

    def rules_for(self, direction: str | None) -> StatusRules:
        key = self.resolve_direction_key(direction) or "default"
        if key in self.status_rules:
            return self.status_rules[key]
        return self.status_rules.get("default") or StatusRules()

    def classify_status(
        self,
        direction: str | None,
        status_name: str | None,
        *,
        jira_done: bool = False,
    ) -> str:
        rules = self.rules_for(direction)
        key = _norm_status(status_name)
        if key and key in rules.done:
            return "done"
        if key and key in rules.active:
            return "active"
        if not rules.active and not rules.done:
            return "done" if jira_done else "active"
        if jira_done:
            return "done"
        return "other"

    def is_direction_done(
        self,
        direction: str | None,
        status_name: str | None,
        *,
        jira_done: bool = False,
    ) -> bool:
        return self.classify_status(direction, status_name, jira_done=jira_done) == "done"

    def is_direction_active(
        self,
        direction: str | None,
        status_name: str | None,
        *,
        jira_done: bool = False,
    ) -> bool:
        return self.classify_status(direction, status_name, jira_done=jira_done) == "active"


_CACHE: TeamConfig | None = None


def _parse_directions(raw) -> tuple[dict[str, DirectionInfo], list[str]]:
    directions: dict[str, DirectionInfo] = {}
    order: list[str] = []

    if isinstance(raw, dict):
        items = raw.items()
        for key, value in items:
            dkey = str(key).strip()
            if not dkey:
                continue
            if isinstance(value, str):
                info = DirectionInfo(key=dkey, name=value.strip() or dkey, short=value.strip() or dkey)
            else:
                value = value or {}
                name = (value.get("name") or dkey).strip()
                short = (value.get("short") or name).strip()
                info = DirectionInfo(
                    key=dkey,
                    name=name,
                    is_dev=bool(value.get("is_dev")),
                    color=(value.get("color") or "#5d6f63").strip(),
                    short=short,
                )
            directions[dkey] = info
            order.append(dkey)
        return directions, order

    if isinstance(raw, list):
        for idx, item in enumerate(raw):
            if isinstance(item, str):
                dkey = f"dir{idx+1}"
                directions[dkey] = DirectionInfo(
                    key=dkey, name=item.strip(), short=item.strip()
                )
                order.append(dkey)
            elif isinstance(item, dict):
                name = (item.get("name") or "").strip()
                dkey = (item.get("key") or "").strip() or name
                if not dkey:
                    continue
                short = (item.get("short") or name or dkey).strip()
                directions[dkey] = DirectionInfo(
                    key=dkey,
                    name=name or dkey,
                    is_dev=bool(item.get("is_dev")),
                    color=(item.get("color") or "#5d6f63").strip(),
                    short=short,
                )
                order.append(dkey)
    return directions, order


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_metrics(raw: dict | None, *, inactive_days: int) -> MetricsSettings:
    raw = raw if isinstance(raw, dict) else {}
    stale_default = _as_int(raw.get("stale_days"), inactive_days)
    return MetricsSettings(
        release_window_days=max(1, _as_int(raw.get("release_window_days"), 14)),
        slip_tolerance_pp=max(0.0, _as_float(raw.get("slip_tolerance_pp"), 12.0)),
        hours_warn_ratio=min(1.0, max(0.0, _as_float(raw.get("hours_warn_ratio"), 0.5))),
        risk_sprint_time_pct=min(
            100.0, max(0.0, _as_float(raw.get("risk_sprint_time_pct"), 70.0))
        ),
        risk_days_left=max(0, _as_int(raw.get("risk_days_left"), 3)),
        stale_days=max(1, stale_default),
        epic_bar_min_pct=min(100.0, max(1.0, _as_float(raw.get("epic_bar_min_pct"), 28.0))),
        epic_section_min_pct=min(
            100.0, max(1.0, _as_float(raw.get("epic_section_min_pct"), 14.0))
        ),
        risks_limit=max(1, _as_int(raw.get("risks_limit"), 40)),
        person_tasks_limit=max(1, _as_int(raw.get("person_tasks_limit"), 40)),
        person_active_tasks_limit=max(
            1, _as_int(raw.get("person_active_tasks_limit"), 30)
        ),
    )


def _parse_ratings(raw: dict | None) -> RatingsSettings:
    raw = raw if isinstance(raw, dict) else {}
    top_n = max(1, _as_int(raw.get("top_n"), 3))
    points_raw = raw.get("place_points") or {1: 3, 2: 2, 3: 1}
    place_points: dict[int, int] = {}
    if isinstance(points_raw, dict):
        for key, value in points_raw.items():
            try:
                place_points[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
    if not place_points:
        place_points = {1: 3, 2: 2, 3: 1}
    return RatingsSettings(top_n=top_n, place_points=place_points)


def _parse_ui(raw: dict | None) -> UiSettings:
    raw = raw if isinstance(raw, dict) else {}
    return UiSettings(task_table_preview=max(1, _as_int(raw.get("task_table_preview"), 5)))


def load_team_config(path: Path | None = None, *, reload: bool = False) -> TeamConfig:
    global _CACHE
    if _CACHE is not None and not reload and path is None:
        return _CACHE

    cfg_path = path or DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        example = ROOT / "team.json.example"
        hint = (
            f"Скопируйте {example.name} → team.json и заполните данными команды."
            if example.exists()
            else "Создайте team.json по образцу team.json.example."
        )
        raise CollectError(f"Не найден конфиг команды: {cfg_path}\n{hint}")

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CollectError(f"Некорректный JSON в {cfg_path}: {exc}") from exc

    directions, keys_order = _parse_directions(data.get("directions"))
    if not directions:
        raise CollectError(f"В {cfg_path} нет справочника directions.")

    name_to_key = {info.name: key for key, info in directions.items()}

    roster_keys: dict[str, str] = {}
    roster: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for person in data.get("people") or []:
        name = (person.get("name") or "").strip()
        direction_raw = (person.get("direction") or "").strip()
        if not name or not direction_raw:
            continue
        dkey = direction_raw if direction_raw in directions else name_to_key.get(direction_raw)
        if not dkey:
            raise CollectError(
                f"Неизвестное направление «{direction_raw}» у сотрудника {name}. "
                f"Используйте ключ из directions ({', '.join(keys_order)})."
            )
        roster_keys[name] = dkey
        roster[name] = directions[dkey].name
        for alias in person.get("aliases") or []:
            alias_s = str(alias).strip()
            if alias_s:
                aliases[alias_s] = name

    if not roster:
        raise CollectError(f"В {cfg_path} нет сотрудников (people).")

    gitlab_projects_keys: dict[str, str] = {}
    gitlab_projects: dict[str, str] = {}
    for ref, direction_raw in (data.get("gitlab_projects") or {}).items():
        pref = str(ref).strip()
        draw = str(direction_raw).strip()
        if not pref or not draw:
            continue
        dkey = draw if draw in directions else name_to_key.get(draw)
        if not dkey:
            raise CollectError(
                f"Неизвестное направление «{draw}» для проекта {pref}."
            )
        gitlab_projects_keys[pref] = dkey
        gitlab_projects[pref] = directions[dkey].name

    jira_block = data.get("jira") or {}
    if not isinstance(jira_block, dict):
        jira_block = {}
    jira_projects = [
        str(x).strip()
        for x in (jira_block.get("projects") or data.get("jira_projects") or [])
        if str(x).strip()
    ]
    jira_jql = str(jira_block.get("jql") or data.get("jira_jql") or "").strip()
    jira_boards: list[JiraBoardConfig] = []
    raw_boards = jira_block.get("boards") or data.get("jira_boards") or []
    if isinstance(raw_boards, list):
        for idx, item in enumerate(raw_boards):
            if isinstance(item, (int, str)):
                jira_boards.append(
                    JiraBoardConfig(id=str(item).strip(), primary=idx == 0)
                )
                continue
            if not isinstance(item, dict):
                continue
            bid = str(item.get("id") or "").strip()
            jira_boards.append(
                JiraBoardConfig(
                    id=bid,
                    name=str(item.get("name") or "").strip(),
                    primary=bool(item.get("primary")) if "primary" in item else idx == 0,
                    has_epics=bool(item.get("has_epics", True)),
                    has_sprints=bool(item.get("has_sprints", True)),
                )
            )
    # Ensure exactly one primary when boards exist
    if jira_boards and not any(b.primary for b in jira_boards):
        jira_boards[0].primary = True
    if sum(1 for b in jira_boards if b.primary) > 1:
        seen_primary = False
        for board in jira_boards:
            if board.primary and not seen_primary:
                seen_primary = True
            else:
                board.primary = False

    filters = [
        str(x).strip()
        for x in (data.get("display_task_filters") or [])
        if str(x).strip()
    ]

    tag_cfg = data.get("task_tags") or {}
    try:
        inactive_days = int(tag_cfg.get("inactive_days") or 3)
    except (TypeError, ValueError):
        inactive_days = 3
    inactive_days = max(1, inactive_days)

    status_rules: dict[str, StatusRules] = {}
    raw_rules = data.get("status_rules") or {}
    if isinstance(raw_rules, dict):
        for key, value in raw_rules.items():
            raw_key = str(key).strip()
            if not raw_key or not isinstance(value, dict):
                continue
            if raw_key == "default":
                status_rules["default"] = StatusRules.from_raw(value)
                continue
            dkey = raw_key if raw_key in directions else name_to_key.get(raw_key)
            if not dkey:
                continue
            status_rules[dkey] = StatusRules.from_raw(value)

    direction_order = [directions[k].name for k in keys_order]
    dev_direction_keys = {k for k, info in directions.items() if info.is_dev}
    dev_directions = {directions[k].name for k in dev_direction_keys}

    try:
        gitlab_days = int(data.get("gitlab_days") or data.get("days") or 30)
    except (TypeError, ValueError):
        gitlab_days = 30
    gitlab_days = max(1, gitlab_days)

    metrics = _parse_metrics(data.get("metrics"), inactive_days=inactive_days)
    ratings_settings = _parse_ratings(data.get("ratings"))
    ui = _parse_ui(data.get("ui"))

    cfg = TeamConfig(
        directions=directions,
        name_to_key=name_to_key,
        roster_keys=roster_keys,
        roster=roster,
        aliases=aliases,
        direction_order=direction_order,
        direction_keys_order=keys_order,
        dev_directions=dev_directions,
        dev_direction_keys=dev_direction_keys,
        gitlab_projects=gitlab_projects,
        gitlab_projects_keys=gitlab_projects_keys,
        gitlab_days=gitlab_days,
        jira_projects=jira_projects,
        jira_boards=jira_boards,
        jira_jql=jira_jql,
        display_task_filters=filters,
        status_rules=status_rules,
        inactive_days=inactive_days,
        metrics=metrics,
        ratings=ratings_settings,
        ui=ui,
        path=cfg_path,
    )
    # Always refresh process-wide cache (needed for --mock with team.json.example)
    _CACHE = cfg
    return cfg


def get_team_config() -> TeamConfig:
    return load_team_config()

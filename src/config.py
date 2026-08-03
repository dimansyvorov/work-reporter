from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import CollectError
from .team_config import JiraBoardConfig, load_team_config

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class Config:
    gitlab_url: str
    gitlab_token: str
    projects: list[str]
    days: int
    host: str
    port: int
    mock: bool
    jira_url: str
    jira_token: str
    jira_email: str
    jira_user: str
    jira_projects: list[str]
    jira_jql: str
    jira_boards: tuple[JiraBoardConfig, ...]
    jira_expected_hours_per_day: float
    jira_team: list[str]
    direction_map_raw: str

    @property
    def gitlab_api_base(self) -> str:
        return self.gitlab_url.rstrip("/") + "/api/v4"

    @property
    def jira_board_id(self) -> str:
        """Primary board id (compat for older call sites)."""
        for board in self.jira_boards:
            if board.primary and board.id:
                return board.id
        for board in self.jira_boards:
            if board.id:
                return board.id
        return ""

    @property
    def jira_enabled(self) -> bool:
        return bool(
            self.jira_url
            and self.jira_token
            and (self.jira_boards or self.jira_projects or self.jira_jql)
        )

    @property
    def gitlab_enabled(self) -> bool:
        return bool(self.gitlab_url and self.gitlab_token and self.projects)


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load_config(mock: bool = False) -> Config:
    load_dotenv(ROOT / ".env")

    team = None
    team_error: CollectError | None = None
    try:
        # Demo mode must not leak real roster from local team.json
        if mock:
            example = ROOT / "team.json.example"
            if example.exists():
                team = load_team_config(example, reload=True)
            else:
                team = load_team_config(reload=True)
        else:
            team = load_team_config()
    except CollectError as exc:
        team_error = exc

    projects = list(team.gitlab_projects.keys()) if team else []
    jira_projects = list(team.jira_projects) if team else []
    jira_jql = (team.jira_jql if team else "") or ""
    jira_boards = tuple(team.jira_boards) if team else tuple()
    # team.json is source of truth; DAYS / GITLAB_DAYS remain optional overrides
    default_days = team.gitlab_days if team else 30
    env_days = os.getenv("DAYS") or os.getenv("GITLAB_DAYS")
    days = int(env_days) if env_days else default_days

    cfg = Config(
        gitlab_url=os.getenv("GITLAB_URL", "").rstrip("/"),
        gitlab_token=os.getenv("GITLAB_TOKEN", "").strip(),
        projects=projects,
        days=days,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8765")),
        mock=mock,
        jira_url=os.getenv("JIRA_URL", "").rstrip("/"),
        jira_token=os.getenv("JIRA_TOKEN", "").strip(),
        jira_email=os.getenv("JIRA_EMAIL", "").strip(),
        jira_user=os.getenv("JIRA_USER", "").strip(),
        jira_projects=jira_projects,
        jira_jql=jira_jql,
        jira_boards=jira_boards,
        jira_expected_hours_per_day=float(
            os.getenv("JIRA_EXPECTED_HOURS_PER_DAY", "8")
        ),
        jira_team=_split_csv(os.getenv("JIRA_TEAM", "")),
        direction_map_raw=os.getenv("GITLAB_DIRECTION_MAP", "").strip(),
    )

    if not cfg.mock:
        if team_error is not None:
            raise CollectError(
                f"Не удалось загрузить team.json:\n{team_error}"
            ) from team_error
        if not cfg.gitlab_enabled and not cfg.jira_enabled:
            raise CollectError(
                "Missing required env vars for GitLab and/or Jira.\n"
                "Copy .env.example → .env, fill tokens, and configure team.json "
                "(gitlab_projects / jira.projects|boards)."
            )

    return cfg

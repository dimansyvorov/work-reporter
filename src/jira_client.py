from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

import requests

from .config import DATA_DIR, Config
from .errors import CollectError
from .fetch_cache import JsonFileCache
from .parallel import map_parallel_with_client

ProgressCb = Callable[..., None]

# Agile/search page size. Many Server/DC instances cap around 100;
# asking for more usually just returns the server max.
JIRA_PAGE_SIZE = 100
# Bounded concurrency for per-issue endpoints (worklogs / remotelinks / agile).
JIRA_WORKERS = 2
# Chunk size for `key in (...)` JQL refreshes.
JIRA_KEY_CHUNK = 50

BASE_ISSUE_FIELDS = [
    "summary",
    "status",
    "issuetype",
    "assignee",
    "reporter",
    "priority",
    "created",
    "updated",
    "resolutiondate",
    "resolution",
    "project",
    "parent",
    "description",
    "timeoriginalestimate",
    "aggregatetimeoriginalestimate",
    "timeestimate",
    "aggregatetimeestimate",
    "timespent",
    "aggregatetimespent",
    # Agile Software fields
    "epic",
    "sprint",
    "closedSprints",
    "fixVersions",
]


class JiraClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

        if cfg.jira_email:
            self.session.auth = (cfg.jira_email, cfg.jira_token)
        elif cfg.jira_user:
            self.session.auth = (cfg.jira_user, cfg.jira_token)
        else:
            self.session.headers["Authorization"] = f"Bearer {cfg.jira_token}"

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.cfg.jira_url.rstrip('/')}{path}"
        params = dict(params or {})

        for _ in range(5):
            try:
                resp = self.session.get(url, params=params, timeout=60)
            except requests.exceptions.RequestException as exc:
                raise CollectError(
                    f"Jira request failed: {exc}\n"
                    f"URL: {url}\n"
                    "Проверьте VPN, JIRA_URL и токен."
                ) from exc

            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", "2")))
                continue

            if resp.status_code >= 400:
                detail = resp.text[:400].replace("\n", " ")
                raise CollectError(
                    f"Jira API error {resp.status_code} for {url}\n"
                    f"Response: {detail}\n"
                    "Проверьте токен, team.json → jira.boards/projects и права (Agile API).\n"
                    "Cloud: задайте JIRA_EMAIL. Server/DC PAT: только JIRA_TOKEN."
                )

            if not resp.content:
                return {}
            return resp.json()

        raise CollectError(f"Jira rate limit persists for {url}")

    def discover_sprint_field(self) -> str | None:
        """Find GreenHopper Sprint custom field id (REST search often needs it)."""
        try:
            fields = self._request("/rest/api/2/field")
        except CollectError:
            return None
        if not isinstance(fields, list):
            return None

        scored: list[tuple[int, str]] = []
        for field in fields:
            field_id = field.get("id")
            if not field_id:
                continue
            name = (field.get("name") or "").lower()
            clause = " ".join(field.get("clauseNames") or []).lower()
            schema = field.get("schema") or {}
            custom = (schema.get("custom") or "").lower()
            blob = f"{name} {clause} {custom}"

            score = 0
            if "gh-sprint" in custom or custom.endswith(":sprint"):
                score = 100
            elif name in {"sprint", "спринт"}:
                score = 90
            elif "sprint" in clause or "спринт" in clause:
                score = 80
            elif "sprint" in custom:
                score = 70
            elif ("sprint" in blob or "спринт" in blob) and field_id.startswith(
                "customfield_"
            ):
                score = 50
            if score:
                scored.append((score, field_id))

        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][1]

    def discover_epic_link_field(self) -> str | None:
        try:
            fields = self._request("/rest/api/2/field")
        except CollectError:
            return None
        if not isinstance(fields, list):
            return None

        scored: list[tuple[int, str]] = []
        for field in fields:
            field_id = field.get("id")
            if not field_id:
                continue
            name = (field.get("name") or "").lower()
            clause = " ".join(field.get("clauseNames") or []).lower()
            schema = field.get("schema") or {}
            custom = (schema.get("custom") or "").lower()
            blob = f"{name} {clause} {custom}"

            score = 0
            if "gh-epic-link" in custom or custom.endswith("epic-link"):
                score = 100
            elif name in {"epic link", "epic", "ссылка на эпик", "эпик", "epic-link"}:
                score = 90
            elif "epic link" in clause or "ссылка на эпик" in clause:
                score = 80
            elif "epic" in custom or "эпик" in custom:
                score = 70
            elif ("epic" in blob or "эпик" in blob) and "name" not in name:
                # avoid matching "Epic Name" (text), prefer link-like fields
                if "link" in blob or "ссыл" in blob or field_id.startswith("customfield_"):
                    score = 60
            if score:
                scored.append((score, field_id))

        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][1]

    def discover_epic_field_from_sample(self, issue_key: str) -> str | None:
        """Scan all fields of one issue for a value that points to an Epic."""
        try:
            issue = self.fetch_issue(issue_key)
        except CollectError:
            return None
        fields = issue.get("fields") or {}
        candidates: list[str] = []
        for field_id, value in fields.items():
            if not str(field_id).startswith("customfield_"):
                continue
            key = None
            if isinstance(value, str) and "-" in value and " " not in value.strip():
                key = value.strip().upper()
            elif isinstance(value, dict) and value.get("key"):
                key = str(value["key"]).upper()
            if not key:
                continue
            try:
                linked = self.fetch_issue(key, ["issuetype", "summary"])
            except CollectError:
                continue
            itype = (
                ((linked.get("fields") or {}).get("issuetype") or {}).get("name") or ""
            ).lower()
            if itype == "epic":
                candidates.append(field_id)
        return candidates[0] if candidates else None

    def fetch_board_epics(self, board_id: str | int) -> list[dict]:
        epics: list[dict] = []
        start_at = 0
        while True:
            try:
                data = self._request(
                    f"/rest/agile/1.0/board/{board_id}/epic",
                    {"startAt": start_at, "maxResults": JIRA_PAGE_SIZE},
                )
            except CollectError:
                break
            batch = data.get("values") or []
            epics.extend(batch)
            if data.get("isLast", True) or not batch:
                break
            start_at += len(batch)
        return epics

    def fetch_board_epic_issues(
        self, board_id: str | int, epic_id: int | str, fields: list[str] | None = None
    ) -> list[dict]:
        issues: list[dict] = []
        start_at = 0
        field_list = fields or ["summary", "status", "issuetype", "assignee"]
        while True:
            try:
                data = self._request(
                    f"/rest/agile/1.0/board/{board_id}/epic/{epic_id}/issue",
                    {
                        "startAt": start_at,
                        "maxResults": JIRA_PAGE_SIZE,
                        "fields": ",".join(field_list),
                    },
                )
            except CollectError:
                break
            batch = data.get("issues") or []
            issues.extend(batch)
            total = int(data.get("total") or 0)
            start_at += len(batch)
            if not batch or start_at >= total:
                break
        return issues

    def fetch_issue(self, key: str, fields: list[str] | None = None) -> dict:
        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        return self._request(f"/rest/api/2/issue/{key}", params)

    def fetch_agile_issue(self, key: str) -> dict:
        return self._request(
            f"/rest/agile/1.0/issue/{key}",
            {"fields": "summary,status,issuetype,epic,parent"},
        )

    def search(self, jql: str, fields: list[str] | None = None) -> list[dict]:
        issues: list[dict] = []
        start_at = 0
        max_results = JIRA_PAGE_SIZE
        field_list = fields or BASE_ISSUE_FIELDS

        while True:
            data = self._request(
                "/rest/api/2/search",
                {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "fields": ",".join(field_list),
                },
            )
            batch = data.get("issues") or []
            issues.extend(batch)
            total = int(data.get("total") or 0)
            start_at += len(batch)
            if not batch or start_at >= total:
                break

        return issues

    def fetch_issues_by_keys(
        self, keys: list[str], fields: list[str] | None = None
    ) -> list[dict]:
        """Fetch issues by key in chunks (avoids re-downloading a whole sprint)."""
        clean = [k.strip().upper() for k in keys if (k or "").strip()]
        if not clean:
            return []
        out: list[dict] = []
        for i in range(0, len(clean), JIRA_KEY_CHUNK):
            chunk = clean[i : i + JIRA_KEY_CHUNK]
            jql = "key in (" + ", ".join(chunk) + ")"
            out.extend(self.search(jql, fields=fields))
        return out

    def fetch_worklogs(self, issue_key: str) -> list[dict]:
        logs: list[dict] = []
        start_at = 0
        while True:
            data = self._request(
                f"/rest/api/2/issue/{issue_key}/worklog",
                {"startAt": start_at, "maxResults": JIRA_PAGE_SIZE},
            )
            batch = data.get("worklogs") or []
            logs.extend(batch)
            total = int(data.get("total") or len(logs))
            start_at += len(batch)
            if not batch or start_at >= total:
                break
        return logs

    def fetch_issue_changelog(self, issue_key: str) -> list[dict]:
        """
        Status/assignee history for one issue.

        This Jira Server build has no /changelog sub-resource (404); use
        expand=changelog on the issue instead.
        """
        data = self._request(
            f"/rest/api/2/issue/{issue_key}",
            {
                "fields": "summary,updated",
                "expand": "changelog",
            },
        )
        changelog = data.get("changelog") or {}
        histories = changelog.get("histories") or []
        total = int(changelog.get("total") or len(histories))
        if total > len(histories):
            print(
                f"  ! changelog truncated for {issue_key}: "
                f"got {len(histories)}/{total}"
            )
        return _normalize_changelog_histories(histories)

    def fetch_issue_comments(self, issue_key: str) -> list[dict]:
        comments: list[dict] = []
        start_at = 0
        while True:
            data = self._request(
                f"/rest/api/2/issue/{issue_key}/comment",
                {"startAt": start_at, "maxResults": JIRA_PAGE_SIZE},
            )
            batch = data.get("comments") or []
            comments.extend(batch)
            total = int(data.get("total") or len(comments))
            start_at += len(batch)
            if not batch or start_at >= total:
                break
        return comments

    def fetch_remote_links(self, issue_key: str) -> list[dict]:
        try:
            data = self._request(f"/rest/api/2/issue/{issue_key}/remotelink")
        except CollectError:
            return []
        return data if isinstance(data, list) else []

    def list_boards(self, project_key: str) -> list[dict]:
        data = self._request(
            "/rest/agile/1.0/board",
            {"projectKeyOrId": project_key, "maxResults": JIRA_PAGE_SIZE},
        )
        return data.get("values") or []

    def list_project_versions(self, project_key: str) -> list[dict]:
        try:
            data = self._request(f"/rest/api/2/project/{project_key}/versions")
        except CollectError as exc:
            print(f"  ! versions for {project_key}: {exc}")
            return []
        return data if isinstance(data, list) else []

    def list_sprints(self, board_id: str | int, state: str) -> list[dict]:
        sprints: list[dict] = []
        start_at = 0
        while True:
            data = self._request(
                f"/rest/agile/1.0/board/{board_id}/sprint",
                {"state": state, "startAt": start_at, "maxResults": JIRA_PAGE_SIZE},
            )
            batch = data.get("values") or []
            sprints.extend(batch)
            if data.get("isLast", True) or not batch:
                break
            start_at += len(batch)
        return sprints

    def fetch_sprint_issues(
        self, sprint_id: int | str, fields: list[str] | None = None
    ) -> list[dict]:
        issues: list[dict] = []
        start_at = 0
        field_list = fields or BASE_ISSUE_FIELDS
        while True:
            data = self._request(
                f"/rest/agile/1.0/sprint/{sprint_id}/issue",
                {
                    "startAt": start_at,
                    "maxResults": JIRA_PAGE_SIZE,
                    "fields": ",".join(field_list),
                },
            )
            batch = data.get("issues") or []
            issues.extend(batch)
            total = int(data.get("total") or 0)
            start_at += len(batch)
            if not batch or start_at >= total:
                break
        return issues

    def fetch_board_issues(
        self,
        board_id: str | int,
        fields: list[str] | None = None,
        *,
        jql: str | None = None,
    ) -> list[dict]:
        """Issues visible on a board (scrum or kanban)."""
        issues: list[dict] = []
        start_at = 0
        field_list = fields or BASE_ISSUE_FIELDS
        while True:
            params: dict[str, Any] = {
                "startAt": start_at,
                "maxResults": JIRA_PAGE_SIZE,
                "fields": ",".join(field_list),
            }
            if jql:
                params["jql"] = jql
            data = self._request(
                f"/rest/agile/1.0/board/{board_id}/issue",
                params,
            )
            batch = data.get("issues") or []
            issues.extend(batch)
            total = int(data.get("total") or 0)
            start_at += len(batch)
            if not batch or start_at >= total:
                break
        return issues


def _base_jql(cfg: Config) -> str:
    if cfg.jira_jql:
        return f"({cfg.jira_jql})"
    if cfg.jira_projects:
        projects = ", ".join(cfg.jira_projects)
        return f"project in ({projects})"
    return ""


def _parse_jira_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    if len(text) >= 5 and text[-5] in "+-" and ":" not in text[-5:]:
        text = text[:-2] + ":" + text[-2:]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _is_numeric_board_id(value: str | None) -> bool:
    text = (value or "").strip()
    return bool(text) and text.isdigit()


def _list_project_boards(client: JiraClient, cfg: Config) -> list[dict]:
    """All Agile boards visible for configured jira.projects."""
    found: list[dict] = []
    seen: set[str] = set()
    for project in cfg.jira_projects:
        try:
            boards = client.list_boards(project)
        except CollectError as exc:
            print(f"  ! boards for project {project}: {exc}")
            continue
        for board in boards:
            bid = str(board.get("id") or "").strip()
            if not bid or bid in seen:
                continue
            seen.add(bid)
            found.append(board)
    return found


def _format_board_catalog(boards: list[dict]) -> str:
    if not boards:
        return "(доски не найдены — проверьте jira.projects и права Agile)"
    lines = []
    for board in boards:
        lines.append(
            f"  · id={board.get('id')}  name=«{board.get('name')}»  "
            f"type={board.get('type') or '?'}"
        )
    return "\n".join(lines)


def _auto_board_id(client: JiraClient, cfg: Config) -> str | None:
    boards = _list_project_boards(client, cfg)
    scrum = [b for b in boards if (b.get("type") or "").lower() == "scrum"]
    chosen = scrum[0] if scrum else (boards[0] if boards else None)
    if chosen:
        print(f"  · board auto: {chosen.get('name')} (id={chosen.get('id')})")
        return str(chosen["id"])
    return None


def _resolve_board_id(
    client: JiraClient,
    cfg: Config,
    *,
    board_id: str,
    board_name: str,
    catalog: list[dict] | None = None,
    allow_auto: bool = False,
) -> str:
    """
    Resolve team.json board id/name to a numeric Agile board id.

    Accepts:
      - numeric id ("123")
      - empty id → match by name, or auto if allow_auto
      - name-like id ("DevOps") → match against board name
    """
    catalog = catalog if catalog is not None else _list_project_boards(client, cfg)
    raw_id = (board_id or "").strip()
    name = (board_name or "").strip()

    if _is_numeric_board_id(raw_id):
        return raw_id

    # Treat non-numeric "id" as a name hint (e.g. "devops", "base")
    needles: list[str] = []
    for value in (name, raw_id):
        text = (value or "").strip().lower()
        if text and text not in needles:
            needles.append(text)

    for needle in needles:
        exact = [
            b
            for b in catalog
            if (b.get("name") or "").strip().lower() == needle
        ]
        if len(exact) == 1:
            bid = str(exact[0]["id"])
            print(f"  · board by name «{exact[0].get('name')}» → id={bid}")
            return bid
        partial = [
            b
            for b in catalog
            if needle in (b.get("name") or "").strip().lower()
        ]
        if len(partial) == 1:
            bid = str(partial[0]["id"])
            print(f"  · board by name «{partial[0].get('name')}» → id={bid}")
            return bid

    if allow_auto and not _is_numeric_board_id(raw_id):
        # Prefer first scrum board without re-printing via helper internals
        scrum = [b for b in catalog if (b.get("type") or "").lower() == "scrum"]
        chosen = scrum[0] if scrum else (catalog[0] if catalog else None)
        if chosen:
            bid = str(chosen["id"])
            print(f"  · board auto: {chosen.get('name')} (id={bid})")
            return bid

    raise CollectError(
        "Не удалось определить numeric id доски Jira.\n"
        f"В team.json указано: id=«{board_id or '—'}», name=«{board_name or '—'}».\n"
        "Нужен числовой id из URL доски (…/rapidBoard.jspa?rapidView=123 → 123)\n"
        "или точное name доски из списка ниже.\n"
        f"Доступные доски для jira.projects={cfg.jira_projects}:\n"
        f"{_format_board_catalog(catalog)}"
    )


def _resolve_boards(client: JiraClient, cfg: Config) -> list:
    """Return configured boards with numeric ids filled."""
    from .team_config import JiraBoardConfig

    boards = list(cfg.jira_boards)
    catalog = _list_project_boards(client, cfg)

    if not boards:
        auto_id = _auto_board_id(client, cfg)
        if auto_id:
            return [
                JiraBoardConfig(
                    id=auto_id,
                    name="auto",
                    primary=True,
                    has_epics=True,
                    has_sprints=True,
                )
            ]
        return []

    resolved: list[JiraBoardConfig] = []
    for board in boards:
        board_id = _resolve_board_id(
            client,
            cfg,
            board_id=board.id,
            board_name=board.name,
            catalog=catalog,
            allow_auto=board.primary,
        )
        resolved.append(
            JiraBoardConfig(
                id=board_id,
                name=board.name,
                primary=board.primary,
                has_epics=board.has_epics,
                has_sprints=board.has_sprints,
            )
        )
    if resolved and not any(b.primary for b in resolved):
        resolved[0].primary = True
    return resolved


def _pick_sprint(client: JiraClient, board_id: str) -> dict | None:
    active = client.list_sprints(board_id, "active")
    if active:
        return active[0]

    closed = client.list_sprints(board_id, "closed")
    if not closed:
        return None

    def sort_key(sprint: dict) -> str:
        return sprint.get("endDate") or sprint.get("completeDate") or sprint.get("startDate") or ""

    return sorted(closed, key=sort_key, reverse=True)[0]


def _sprint_from_open_sprints_jql(
    client: JiraClient,
    cfg: Config,
    fields: list[str] | None = None,
) -> tuple[dict, list[dict]]:
    base = _base_jql(cfg)
    jql = f"{base} AND sprint in openSprints()" if base else "sprint in openSprints()"
    issues = client.search(jql + " ORDER BY updated DESC", fields=fields)
    if not issues:
        raise CollectError(
            "Не найден активный спринт.\n"
            "Укажите team.json → jira.boards[].id или jira.projects / openSprints()."
        )

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=7)
    end = today + timedelta(days=7)
    sprint = {
        "id": None,
        "name": "Active sprint",
        "state": "active",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "source": "jql_openSprints",
    }
    return sprint, issues


def _inclusive_sprint_end_date(end: datetime | None, start: datetime | None) -> date | None:
    """
    Jira Agile often stores endDate as midnight at the *start* of the day after
    the last sprint day (exclusive boundary). Example: endDate 2026-08-15T00:00:00
    → inclusive last day is 14.08. End-of-day timestamps (23:59) stay as that date.
    """
    if end is None:
        return None
    end_day = end.date()
    if (
        end.hour == 0
        and end.minute == 0
        and end.second == 0
        and end.microsecond == 0
    ):
        end_day = end_day - timedelta(days=1)
    if start is not None and end_day < start.date():
        return start.date()
    return end_day


def _normalize_sprint(sprint: dict) -> dict:
    start = _parse_jira_dt(sprint.get("startDate"))
    end = _parse_jira_dt(sprint.get("endDate") or sprint.get("completeDate"))
    end_day = _inclusive_sprint_end_date(end, start)
    return {
        "id": sprint.get("id"),
        "name": sprint.get("name") or "Sprint",
        "state": (sprint.get("state") or "unknown").lower(),
        "start_date": (start.date().isoformat() if start else None),
        "end_date": (end_day.isoformat() if end_day else None),
        "start_at": start.isoformat() if start else sprint.get("startDate"),
        "end_at": end.isoformat() if end else sprint.get("endDate"),
        "goal": sprint.get("goal"),
        "source": sprint.get("source") or "agile",
    }


def _issue_reported_timespent(issue: dict) -> int:
    fields = issue.get("fields") or {}
    return int(fields.get("timespent") or 0)


def _normalize_changelog_histories(histories: list[dict]) -> list[dict]:
    """Keep only status/assignee transitions, one row per changelog group."""
    out: list[dict] = []
    for history in histories or []:
        status_from = status_to = None
        assignee_from = assignee_to = None
        for item in history.get("items") or []:
            field = (item.get("field") or "").strip().lower()
            if field == "status":
                status_from = item.get("fromString")
                status_to = item.get("toString")
            elif field == "assignee":
                assignee_from = item.get("fromString")
                assignee_to = item.get("toString")
        if (
            status_from is None
            and status_to is None
            and assignee_from is None
            and assignee_to is None
        ):
            continue
        author = history.get("author") or {}
        out.append(
            {
                "at": history.get("created"),
                "author": (author.get("displayName") or author.get("name") or "").strip()
                or None,
                "status_from": status_from,
                "status_to": status_to,
                "assignee_from": assignee_from,
                "assignee_to": assignee_to,
            }
        )
    return out


def _collect_changelogs_for_issues(
    client: JiraClient,
    issues: list[dict],
    *,
    on_progress: ProgressCb | None = None,
) -> list[dict]:
    """
    Load status/assignee changelog with disk cache keyed by issue key + updated.
    Returns flat list of {issue_key, issue_summary, ...history fields}.
    """
    cache = JsonFileCache(DATA_DIR / "cache" / "changelogs.json")
    cfg = client.cfg

    to_fetch: list[dict] = []
    cached_rows: list[dict] = []
    cache_hits = 0

    for issue in issues:
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        fields = issue.get("fields") or {}
        summary = fields.get("summary")
        updated = str(fields.get("updated") or "")
        cache_key = f"{key}|{updated}"
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            for row in cached:
                if isinstance(row, dict):
                    cached_rows.append(row)
            cache_hits += 1
            continue
        to_fetch.append(
            {
                "key": key,
                "summary": summary,
                "updated": updated,
                "cache_key": cache_key,
            }
        )

    total = len(to_fetch)
    print(f"  · changelogs: fetch={total}, cache={cache_hits}")

    def progress(done: int, all_n: int) -> None:
        if on_progress:
            on_progress(
                "jira_changelog",
                f"Jira: changelog {done}/{all_n}",
                current=done,
                total=all_n,
            )

    def worker(c: JiraClient, item: dict) -> tuple[str, list[dict]]:
        try:
            histories = c.fetch_issue_changelog(item["key"])
        except CollectError as exc:
            print(f"  ! changelog {item['key']}: {exc}")
            histories = []
        rows = [
            {
                "issue_key": item["key"],
                "issue_summary": item["summary"],
                **hist,
            }
            for hist in histories
        ]
        return item["cache_key"], rows

    fetched_rows: list[dict] = []
    if to_fetch:
        pairs = map_parallel_with_client(
            to_fetch,
            lambda: JiraClient(cfg),
            worker,
            max_workers=JIRA_WORKERS,
            on_progress=progress if total else None,
            progress_every=10,
        )
        updates = {cache_key: rows for cache_key, rows in pairs}
        cache.set_many(updates)
        for _, rows in pairs:
            fetched_rows.extend(rows)

    return cached_rows + fetched_rows


def _comment_body_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body.strip()
    if isinstance(body, dict):
        parts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and node.get("text"):
                    parts.append(str(node.get("text")))
                for child in node.get("content") or []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(body)
        return " ".join(parts).strip()
    return str(body).strip()


def _normalize_comments(raw_comments: list[dict]) -> list[dict]:
    out: list[dict] = []
    for comment in raw_comments or []:
        author = comment.get("author") or {}
        body = _comment_body_text(comment.get("body"))
        if not body:
            continue
        out.append(
            {
                "id": comment.get("id"),
                "at": comment.get("created") or comment.get("updated"),
                "author": (author.get("displayName") or author.get("name") or "").strip()
                or None,
                "body": body[:2000],
            }
        )
    # newest last chronologically for display oldest→newest; keep last 30
    out.sort(key=lambda c: c.get("at") or "")
    if len(out) > 30:
        out = out[-30:]
    return out


def _collect_comments_for_issues(
    client: JiraClient,
    issues: list[dict],
    *,
    on_progress: ProgressCb | None = None,
) -> list[dict]:
    """Load issue comments with disk cache keyed by issue key + updated."""
    cache = JsonFileCache(DATA_DIR / "cache" / "comments.json")
    cfg = client.cfg

    to_fetch: list[dict] = []
    cached_rows: list[dict] = []
    cache_hits = 0

    for issue in issues:
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        fields = issue.get("fields") or {}
        updated = str(fields.get("updated") or "")
        cache_key = f"{key}|{updated}"
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            for row in cached:
                if isinstance(row, dict):
                    cached_rows.append(row)
            cache_hits += 1
            continue
        to_fetch.append(
            {
                "key": key,
                "updated": updated,
                "cache_key": cache_key,
            }
        )

    total = len(to_fetch)
    print(f"  · comments: fetch={total}, cache={cache_hits}")

    def progress(done: int, all_n: int) -> None:
        if on_progress:
            on_progress(
                "jira_comments",
                f"Jira: комментарии {done}/{all_n}",
                current=done,
                total=all_n,
            )

    def worker(c: JiraClient, item: dict) -> tuple[str, list[dict]]:
        try:
            raw = c.fetch_issue_comments(item["key"])
        except CollectError as exc:
            print(f"  ! comments {item['key']}: {exc}")
            raw = []
        rows = [
            {
                "issue_key": item["key"],
                **comment,
            }
            for comment in _normalize_comments(raw)
        ]
        return item["cache_key"], rows

    fetched_rows: list[dict] = []
    if to_fetch:
        pairs = map_parallel_with_client(
            to_fetch,
            lambda: JiraClient(cfg),
            worker,
            max_workers=JIRA_WORKERS,
            on_progress=progress if total else None,
            progress_every=10,
        )
        updates = {cache_key: rows for cache_key, rows in pairs}
        cache.set_many(updates)
        for _, rows in pairs:
            fetched_rows.extend(rows)

    return cached_rows + fetched_rows


def _filter_worklogs_for_window(
    logs: list[dict],
    *,
    key: str,
    summary: Any,
    since_date: date,
    until_date: date,
) -> list[dict]:
    out: list[dict] = []
    for log in logs:
        started = _parse_jira_dt(log.get("started"))
        if not started:
            continue
        day = started.date()
        if day < since_date or day > until_date:
            continue
        out.append(
            {
                "issue_key": key,
                "issue_summary": summary,
                "id": log.get("id"),
                "started": log.get("started"),
                "time_spent_seconds": log.get("timeSpentSeconds") or 0,
                "author": log.get("author") or {},
                "comment": log.get("comment"),
            }
        )
    return out


def _collect_worklogs_for_issues(
    client: JiraClient,
    issues: list[dict],
    *,
    since_date: date,
    until_date: date,
    on_progress: ProgressCb | None = None,
) -> list[dict]:
    """
    Load worklogs with:
    - skip when Jira reports timespent=0 (no worklogs to fetch)
    - disk cache keyed by issue key + updated + window
    - bounded parallel HTTP (thread-local clients)
    """
    cache = JsonFileCache(DATA_DIR / "cache" / "worklogs.json")
    since_s = since_date.isoformat()
    until_s = until_date.isoformat()
    cfg = client.cfg

    to_fetch: list[dict] = []
    worklogs: list[dict] = []
    skipped_zero = 0
    cache_hits = 0

    for issue in issues:
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        fields = issue.get("fields") or {}
        summary = fields.get("summary")
        if _issue_reported_timespent(issue) <= 0:
            skipped_zero += 1
            continue
        updated = str(fields.get("updated") or "")
        cache_key = f"{key}|{updated}|{since_s}|{until_s}"
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            worklogs.extend(cached)
            cache_hits += 1
            continue
        to_fetch.append(
            {
                "key": key,
                "summary": summary,
                "updated": updated,
                "cache_key": cache_key,
            }
        )

    total = len(to_fetch)
    print(
        f"  · worklogs: fetch={total}, cache={cache_hits}, "
        f"skip_zero_time={skipped_zero}"
    )

    def progress(done: int, all_n: int) -> None:
        if on_progress:
            on_progress(
                "jira_worklogs",
                f"Jira: worklogs {done}/{all_n}",
                current=done,
                total=all_n,
            )

    def worker(c: JiraClient, item: dict) -> tuple[str, list[dict]]:
        raw_logs = c.fetch_worklogs(item["key"])
        filtered = _filter_worklogs_for_window(
            raw_logs,
            key=item["key"],
            summary=item["summary"],
            since_date=since_date,
            until_date=until_date,
        )
        return item["cache_key"], filtered

    if to_fetch:
        pairs = map_parallel_with_client(
            to_fetch,
            lambda: JiraClient(cfg),
            worker,
            max_workers=JIRA_WORKERS,
            on_progress=progress if total else None,
            progress_every=5,
        )
        updates = {cache_key: logs for cache_key, logs in pairs}
        cache.set_many(updates)
        for _, logs in pairs:
            worklogs.extend(logs)

    return worklogs


def _extract_epic_info(fields: dict, epic_field_id: str | None) -> dict | None:
    """
    Supports:
    - Agile field `epic` ({key, name/summary, ...})
    - Classic customfield Epic Link (string key or object)
    - parent / parentEpic when parent issuetype is Epic
    """
    epic = fields.get("epic")
    if isinstance(epic, dict) and epic.get("key"):
        return {
            "key": str(epic["key"]).upper(),
            "summary": epic.get("summary") or epic.get("name") or epic.get("key"),
            "status": None,
            "done": bool(epic.get("done")),
        }

    parent_epic = fields.get("parentEpic")
    if isinstance(parent_epic, str) and parent_epic.strip():
        return {"key": parent_epic.strip().upper(), "summary": parent_epic.strip(), "status": None}
    if isinstance(parent_epic, dict) and parent_epic.get("key"):
        return {
            "key": str(parent_epic["key"]).upper(),
            "summary": parent_epic.get("summary") or parent_epic.get("name") or parent_epic["key"],
            "status": None,
        }

    if epic_field_id:
        value = fields.get(epic_field_id)
        if isinstance(value, str) and value.strip():
            # Classic Epic Link is often just the epic key — no title in this field.
            key = value.strip().upper()
            return {"key": key, "summary": None, "status": None}
        if isinstance(value, dict) and value.get("key"):
            key = str(value["key"]).upper()
            summary = value.get("summary") or value.get("name")
            if summary and str(summary).strip().upper() == key:
                summary = None
            return {
                "key": key,
                "summary": summary,
                "status": None,
            }

    parent = fields.get("parent") or {}
    if parent.get("key"):
        parent_fields = parent.get("fields") or {}
        parent_type = ((parent_fields.get("issuetype") or {}).get("name") or "").lower()
        if parent_type in {"epic", "эпик"}:
            return {
                "key": str(parent["key"]).upper(),
                "summary": parent_fields.get("summary") or parent.get("key"),
                "status": ((parent_fields.get("status") or {}).get("name")),
            }
    return None


def _apply_epic(issue: dict, info: dict) -> None:
    issue["_epic_key"] = info["key"]
    summary = info.get("summary")
    # Ignore placeholder summaries that are just the epic key
    if summary and str(summary).strip().upper() != str(info["key"]).upper():
        issue["_epic_summary"] = summary
    fields = issue.setdefault("fields", {})
    fields["epic"] = {
        "key": info["key"],
        "summary": issue.get("_epic_summary") or info.get("summary"),
        "name": issue.get("_epic_summary") or info.get("summary"),
    }


def _enrich_missing_epics_via_board(
    client: JiraClient, board_id: str | int, issues: list[dict]
) -> int:
    """Map sprint issues to epics via Agile board epic endpoints."""
    by_key = {(i.get("key") or "").upper(): i for i in issues if i.get("key")}
    attached = 0
    board_epics = client.fetch_board_epics(board_id)
    if not board_epics:
        return 0
    print(f"  · board epics: {len(board_epics)}")
    for epic in board_epics:
        epic_id = epic.get("id")
        epic_key = (epic.get("key") or "").upper()
        if not epic_id or not epic_key:
            continue
        summary = epic.get("summary") or epic.get("name") or epic_key
        epic_issues = client.fetch_board_epic_issues(board_id, epic_id, fields=["summary"])
        for child in epic_issues:
            child_key = (child.get("key") or "").upper()
            issue = by_key.get(child_key)
            if not issue or issue.get("_epic_key"):
                continue
            _apply_epic(
                issue,
                {"key": epic_key, "summary": summary, "status": None},
            )
            attached += 1
    return attached


def _enrich_missing_epics_via_agile(client: JiraClient, issues: list[dict]) -> int:
    missing = [i for i in issues if not i.get("_epic_key") and i.get("key")]
    if not missing:
        return 0
    missing = missing[:80]
    print(f"  · epic enrich via agile issue ({len(missing)} without epic)…")
    cfg = client.cfg

    def worker(c: JiraClient, issue: dict) -> tuple[dict, dict | None]:
        try:
            agile_issue = c.fetch_agile_issue(issue["key"])
        except CollectError:
            return issue, None
        return issue, _extract_epic_info(agile_issue.get("fields") or {}, None)

    pairs = map_parallel_with_client(
        missing,
        lambda: JiraClient(cfg),
        worker,
        max_workers=JIRA_WORKERS,
        progress_every=10,
    )
    attached = 0
    for issue, info in pairs:
        if not info:
            continue
        _apply_epic(issue, info)
        attached += 1
    return attached


def _enrich_gitlab_links_from_jira(
    client: JiraClient,
    issues: list[dict],
    *,
    on_progress: ProgressCb | None = None,
) -> int:
    """
    Attach GitLab MR refs from description (local) + remote links (HTTP).

    Description is parsed locally for all issues; remotelink HTTP runs in
    parallel with disk cache keyed by issue key + updated (same as changelog).
    """
    from .linking import extract_mr_refs_from_text

    cfg = client.cfg
    cache = JsonFileCache(DATA_DIR / "cache" / "remotelinks.json")
    targets: list[dict] = []
    desc_hits = 0
    cache_hits = 0

    def merge_refs(
        refs: list[dict], seen: set[str], items: list[dict]
    ) -> None:
        for ref in items:
            token = (ref.get("web_url") or "").lower()
            if not token or token in seen:
                continue
            seen.add(token)
            refs.append(ref)

    for issue in issues:
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        refs: list[dict] = []
        seen: set[str] = set()

        fields = issue.get("fields") or {}
        desc = fields.get("description")
        if isinstance(desc, str):
            merge_refs(refs, seen, extract_mr_refs_from_text(desc))
        elif desc is not None:
            merge_refs(refs, seen, extract_mr_refs_from_text(str(desc)))

        if refs:
            desc_hits += 1

        updated = str(fields.get("updated") or "")
        cache_key = f"{key}|{updated}"
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            merge_refs(refs, seen, [r for r in cached if isinstance(r, dict)])
            issue["_gitlab_mrs"] = refs
            cache_hits += 1
            continue

        issue["_gitlab_mrs"] = refs
        issue["_gitlab_mr_seen"] = seen
        issue["_remotelink_cache_key"] = cache_key
        targets.append(issue)

    def progress(done: int, all_n: int) -> None:
        if on_progress:
            on_progress(
                "jira_issues",
                f"Jira: GitLab-ссылки {done}/{all_n}",
                current=done,
                total=max(all_n, 1),
            )

    def worker(c: JiraClient, issue: dict) -> tuple[str, dict, list[dict]]:
        key = issue.get("key")
        extra: list[dict] = []
        try:
            for link in c.fetch_remote_links(key):
                obj = link.get("object") or {}
                url = obj.get("url") or link.get("url") or ""
                title = obj.get("title") or ""
                extra.extend(extract_mr_refs_from_text(url, title))
        except CollectError:
            pass
        # Deduplicate payload stored in cache
        seen_urls: set[str] = set()
        unique: list[dict] = []
        for ref in extra:
            token = (ref.get("web_url") or "").lower()
            if not token or token in seen_urls:
                continue
            seen_urls.add(token)
            unique.append(ref)
        return str(issue.get("_remotelink_cache_key") or ""), issue, unique

    print(
        f"  · gitlab links: remotelink_fetch={len(targets)}, "
        f"cache={cache_hits}, from_description={desc_hits}"
    )

    if targets:
        pairs = map_parallel_with_client(
            targets,
            lambda: JiraClient(cfg),
            worker,
            max_workers=JIRA_WORKERS,
            on_progress=progress,
            progress_every=10,
        )
        updates = {
            cache_key: unique
            for cache_key, _, unique in pairs
            if cache_key
        }
        cache.set_many(updates)
        for _, issue, extra in pairs:
            seen: set[str] = issue.get("_gitlab_mr_seen") or set()
            refs = list(issue.get("_gitlab_mrs") or [])
            merge_refs(refs, seen, extra)
            issue["_gitlab_mrs"] = refs

    for issue in issues:
        issue.pop("_gitlab_mr_seen", None)
        issue.pop("_remotelink_cache_key", None)

    return sum(1 for i in issues if i.get("_gitlab_mrs"))


def _parse_version_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Jira often returns YYYY-MM-DD; sometimes datetime
    try:
        if "T" in text:
            dt = _parse_jira_dt(text)
            return dt.date() if dt else None
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _collect_releases(
    client: JiraClient,
    cfg: Config,
    *,
    sprint: dict,
    issue_fields: list[str],
    on_progress: ProgressCb | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Versions with releaseDate in [sprint_start, sprint_end + release_window_days].
    Returns (releases_meta, release_issues).
    """
    from .team_config import get_team_config

    start = (
        date.fromisoformat(sprint["start_date"])
        if sprint.get("start_date")
        else datetime.now(timezone.utc).date()
    )
    end = (
        date.fromisoformat(sprint["end_date"])
        if sprint.get("end_date")
        else start
    )
    window_days = get_team_config().metrics.release_window_days
    window_end = end + timedelta(days=window_days)

    releases: list[dict] = []
    seen_ids: set[str] = set()
    for project in cfg.jira_projects:
        for raw in client.list_project_versions(project):
            if raw.get("archived"):
                continue
            vid = str(raw.get("id") or "").strip()
            if not vid or vid in seen_ids:
                continue
            release_day = _parse_version_date(
                raw.get("releaseDate") or raw.get("userReleaseDate")
            )
            if not release_day or release_day < start or release_day > window_end:
                continue
            seen_ids.add(vid)
            start_day = _parse_version_date(
                raw.get("startDate") or raw.get("userStartDate")
            )
            releases.append(
                {
                    "id": vid,
                    "name": raw.get("name") or vid,
                    "project": project,
                    "description": raw.get("description") or "",
                    "released": bool(raw.get("released")),
                    "overdue": bool(raw.get("overdue")),
                    "release_date": release_day.isoformat(),
                    "start_date": start_day.isoformat() if start_day else None,
                    "web_url": (
                        f"{cfg.jira_url.rstrip('/')}/projects/{project}"
                        f"/versions/{vid}"
                    ),
                }
            )

    releases.sort(key=lambda r: (r.get("release_date") or "", r.get("name") or ""))
    if not releases:
        return [], []

    if on_progress:
        on_progress(
            "jira_epics",
            f"Jira: релизы в окне спринта — {len(releases)}",
        )

    # One search for all version ids (numeric ids are stable in JQL)
    ids = ", ".join(r["id"] for r in releases)
    projects = ", ".join(cfg.jira_projects) if cfg.jira_projects else ""
    jql_parts = [f"fixVersion in ({ids})"]
    if projects:
        jql_parts.insert(0, f"project in ({projects})")
    jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"
    try:
        release_issues = client.search(jql, fields=issue_fields)
    except CollectError as exc:
        print(f"  ! release issues: {exc}")
        release_issues = []
    print(f"  · releases: {len(releases)}, issues: {len(release_issues)}")
    return releases, release_issues


def _epic_link_jql_clauses(epic_field_id: str | None) -> list[str]:
    """
    JQL left-hand sides that can match classic Epic Link values.

    On many Server/DC instances the human name «Epic Link» is unavailable in JQL,
    and `customfield_NNNN` is also rejected — only `cf[NNNN]` works.
    """
    clauses: list[str] = []
    if epic_field_id and epic_field_id.startswith("customfield_"):
        num = epic_field_id.removeprefix("customfield_")
        if num.isdigit():
            clauses.append(f"cf[{num}]")
        # Some Cloud/newer servers accept the REST id in JQL; keep as secondary.
        clauses.append(epic_field_id)
    clauses.extend(['"Epic Link"', '"Ссылка на эпик"'])
    # de-dupe, keep order
    seen: set[str] = set()
    out: list[str] = []
    for c in clauses:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _fetch_issues_for_epics(
    client: JiraClient,
    epic_keys: list[str],
    *,
    fields: list[str],
    epic_field_id: str | None,
    projects: list[str] | None = None,
) -> list[dict]:
    """All issues belonging to the given epics (full epic scope, not sprint-only)."""
    keys = [k.strip().upper() for k in epic_keys if (k or "").strip()]
    if not keys:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    project_clause = (
        f"project in ({', '.join(projects)}) AND " if projects else ""
    )
    link_clauses = _epic_link_jql_clauses(epic_field_id)
    for i in range(0, len(keys), 40):
        chunk = keys[i : i + 40]
        joined = ", ".join(f'"{k}"' for k in chunk)
        queries: list[str] = []
        for link in link_clauses:
            queries.append(
                f"{project_clause}({link} in ({joined}) OR parent in ({joined}))"
            )
        # Broader fallbacks without project filter
        for link in link_clauses:
            queries.append(f"({link} in ({joined}) OR parent in ({joined}))")
        # parent-only (next-gen / hierarchy) — often empty on classic boards
        queries.append(f"{project_clause}parent in ({joined})")
        queries.append(f"parent in ({joined})")

        batch: list[dict] = []
        for jql in queries:
            try:
                batch = client.search(jql + " ORDER BY updated DESC", fields=fields)
                if batch:
                    break
            except CollectError as exc:
                # Expected on instances where «Epic Link» / customfield_* JQL is invalid
                msg = str(exc)
                if "400" in msg or "не существует" in msg or "does not exist" in msg.lower():
                    print(f"  · epic scope jql skip: {jql[:90]}…")
                else:
                    print(f"  ! epic scope jql: {exc}")
                continue
        for issue in batch:
            key = (issue.get("key") or "").upper()
            if not key or key in seen:
                continue
            seen.add(key)
            info = _extract_epic_info(issue.get("fields") or {}, epic_field_id)
            if info:
                _apply_epic(issue, info)
            # Ensure epic key is set even when extract fails for child rows
            if not issue.get("_epic_key"):
                for epic_key in chunk:
                    # parent key match is enough for parent-in query hits
                    parent = (issue.get("fields") or {}).get("parent") or {}
                    if (parent.get("key") or "").upper() == epic_key:
                        issue["_epic_key"] = epic_key
                        break
                # classic Epic Link stored as string / object on the custom field
                if not issue.get("_epic_key") and epic_field_id:
                    raw_val = (issue.get("fields") or {}).get(epic_field_id)
                    if isinstance(raw_val, str) and raw_val.strip().upper() in {
                        k.upper() for k in chunk
                    }:
                        issue["_epic_key"] = raw_val.strip().upper()
                    elif isinstance(raw_val, dict) and (raw_val.get("key") or "").upper() in {
                        k.upper() for k in chunk
                    }:
                        issue["_epic_key"] = str(raw_val["key"]).upper()
            out.append(issue)
    print(f"  · epic scope issues: {len(out)} for {len(keys)} epics")
    return out


def _collect_epics(client: JiraClient, issues: list[dict], epic_field_id: str | None) -> dict:
    epics: dict[str, dict] = {}
    for issue in issues:
        info = _extract_epic_info(issue.get("fields") or {}, epic_field_id)
        if not info:
            continue
        key = info["key"]
        issue["_epic_key"] = key
        if info.get("summary"):
            issue["_epic_summary"] = info["summary"]
        epics.setdefault(
            key,
            {
                "key": key,
                "summary": info.get("summary") or key,
                "status": info.get("status"),
            },
        )

    # Fill summaries for keys that are only links (parallel)
    need_meta = [
        key
        for key, meta in epics.items()
        if not meta.get("summary") or meta["summary"] == key
    ]
    if need_meta:
        cfg = client.cfg

        def worker(c: JiraClient, key: str) -> tuple[str, dict | None]:
            try:
                issue = c.fetch_issue(key, ["summary", "status", "issuetype"])
            except CollectError:
                return key, None
            fields = issue.get("fields") or {}
            return key, {
                "summary": fields.get("summary") or key,
                "status": ((fields.get("status") or {}).get("name")),
            }

        for key, meta_update in map_parallel_with_client(
            need_meta,
            lambda: JiraClient(cfg),
            worker,
            max_workers=JIRA_WORKERS,
            progress_every=10,
        ):
            if not meta_update:
                continue
            epics[key]["summary"] = meta_update["summary"]
            epics[key]["status"] = meta_update.get("status")
    return epics


def _jira_avatar_urls(person: dict | None) -> str | None:
    if not person:
        return None
    urls = person.get("avatarUrls") or {}
    return urls.get("48x48") or urls.get("32x32") or urls.get("24x24")


def _fetch_roster_jira_profiles(client: JiraClient) -> dict[str, dict]:
    """
    Resolve Jira username + avatar for every roster member via user API.

    Fills gaps for people who never appear as assignee/reporter/worklog author
    in the current sprint payload (assignee without worklogs yet).
    """
    from .team import TEAM_ROSTER
    from .team_config import get_team_config

    team_cfg = get_team_config()
    out: dict[str, dict] = {}
    hits = 0
    for name in sorted(TEAM_ROSTER.keys(), key=lambda x: x.lower()):
        profile: dict[str, Any] = {}
        for username in team_cfg.jira_username_candidates(name):
            try:
                data = client._request(
                    "/rest/api/2/user",
                    {"username": username},
                )
            except CollectError:
                continue
            if not isinstance(data, dict):
                continue
            avatar = _jira_avatar_urls(data)
            profile = {
                "username": data.get("name") or username,
                "display_name": data.get("displayName") or name,
                "avatar_url": avatar,
            }
            hits += 1
            break
        if profile:
            out[name] = profile
    print(f"  · roster jira profiles: {hits}/{len(TEAM_ROSTER)}")
    return out


def _kanban_board_jql(
    board: Any,
    sprint_date_candidates: list[tuple[date | None, date | None]],
) -> str:
    """JQL for kanban boards: explicit board.jql or open + updated since sprint."""
    custom = str(getattr(board, "jql", "") or "").strip()
    if custom:
        return custom
    start: date | None = None
    for s, _e in sprint_date_candidates:
        if s and (start is None or s < start):
            start = s
    if start:
        return f'resolution is EMPTY AND updated >= "{start.isoformat()}"'
    return "resolution is EMPTY AND updated >= -21d"


def _filter_kanban_only_to_roster(issues: list[dict]) -> list[dict]:
    """
    Drop kanban-only issues whose assignee is not on the team roster.

    Scrum / primary-board issues are kept as-is (first board wins). This avoids
    enriching hundreds of DEVOPS tickets that never appear in the report.
    """
    from .team import TEAM_ROSTER, canonical_team_name

    kept: list[dict] = []
    dropped = 0
    for issue in issues:
        if issue.get("_board_has_sprints", True):
            kept.append(issue)
            continue
        fields = issue.get("fields") or {}
        assignee = fields.get("assignee") or {}
        name = ""
        if isinstance(assignee, dict):
            for key in ("displayName", "name", "emailAddress"):
                name = (assignee.get(key) or "").strip()
                if name:
                    break
        canonical = canonical_team_name(name) if name else None
        if canonical and canonical in TEAM_ROSTER:
            kept.append(issue)
        else:
            dropped += 1
    if dropped:
        print(
            f"  · kanban: отброшено {dropped} задач вне roster "
            f"(до enrichment осталось {len(kept)})"
        )
    return kept


def collect_jira_raw(cfg: Config, on_progress: ProgressCb | None = None) -> dict:
    def progress(step: str, message: str, **kwargs) -> None:
        print(f"  · {message}")
        if on_progress:
            on_progress(step, message, **kwargs)

    client = JiraClient(cfg)
    base = _base_jql(cfg)

    progress("jira_sprint", "Jira: определяю доски…")
    boards = _resolve_boards(client, cfg)

    progress("jira_sprint", "Jira: определяю поле Epic Link…")
    epic_field_id = client.discover_epic_link_field()
    sprint_field_id = client.discover_sprint_field()
    issue_fields = list(BASE_ISSUE_FIELDS)
    if "parentEpic" not in issue_fields:
        issue_fields.append("parentEpic")
    if epic_field_id and epic_field_id not in issue_fields:
        issue_fields.append(epic_field_id)
        print(f"  · epic custom field: {epic_field_id}")
    else:
        print("  · epic custom field: not found yet (will probe sample / board epics)")
    if sprint_field_id and sprint_field_id not in issue_fields:
        issue_fields.append(sprint_field_id)
        print(f"  · sprint custom field: {sprint_field_id}")
    else:
        print("  · sprint custom field: not found (will rely on fields.sprint / strings)")

    issues_by_key: dict[str, dict] = {}
    board_summaries: list[dict] = []
    primary_sprint_raw: dict | None = None
    primary_board_id: str | None = None
    sprint_date_candidates: list[tuple[date | None, date | None]] = []

    if boards:
        # Primary first so its issues win on key collisions
        boards_ordered = sorted(boards, key=lambda b: (not b.primary, b.name or b.id))
        for board in boards_ordered:
            label = board.name or board.id
            progress("jira_sprint", f"Jira: доска «{label}» (id={board.id})…")

            sprint_raw: dict | None = None
            if board.has_sprints:
                sprint_raw = _pick_sprint(client, board.id)
                if not sprint_raw:
                    print(f"  ! на доске {board.id} нет active/closed спринта — пропуск")
                    continue
                norm = _normalize_sprint(sprint_raw)
                sprint_date_candidates.append(
                    (
                        date.fromisoformat(norm["start_date"])
                        if norm.get("start_date")
                        else None,
                        date.fromisoformat(norm["end_date"])
                        if norm.get("end_date")
                        else None,
                    )
                )
                progress(
                    "jira_issues",
                    f"Jira: спринт «{sprint_raw.get('name')}» · доска {label}",
                )
                board_issues = client.fetch_sprint_issues(
                    sprint_raw["id"], fields=issue_fields
                )
            else:
                # Kanban / non-sprint board: open issues, narrowed by board.jql
                # or by primary sprint start (fallback: last 21 days).
                kanban_jql = _kanban_board_jql(board, sprint_date_candidates)
                progress(
                    "jira_issues",
                    f"Jira: канбан «{label}» (без спринтов)",
                )
                board_issues = client.fetch_board_issues(
                    board.id,
                    fields=issue_fields,
                    jql=kanban_jql,
                )
                print(
                    f"  · board {board.id} (has_sprints=false): "
                    f"{len(board_issues)} issues · jql={kanban_jql}"
                )

            for issue in board_issues:
                key = (issue.get("key") or "").upper()
                if not key:
                    continue
                issue["_board_id"] = board.id
                issue["_board_name"] = board.name
                issue["_board_has_epics"] = board.has_epics
                issue["_board_has_sprints"] = board.has_sprints
                # First occurrence wins (primary board is first after sort)
                if key not in issues_by_key:
                    issues_by_key[key] = issue
            board_summaries.append(
                {
                    "id": board.id,
                    "name": board.name,
                    "primary": board.primary,
                    "has_epics": board.has_epics,
                    "has_sprints": board.has_sprints,
                    "sprint_id": sprint_raw.get("id") if sprint_raw else None,
                    "sprint_name": sprint_raw.get("name") if sprint_raw else None,
                    "issues": len(board_issues),
                }
            )
            if sprint_raw and (board.primary or primary_sprint_raw is None):
                primary_sprint_raw = sprint_raw
                primary_board_id = board.id
            elif board.primary and primary_board_id is None:
                primary_board_id = board.id

        if not primary_sprint_raw:
            # All boards are kanban / without sprints — fall back to openSprints JQL
            # only for sprint window metadata; keep already collected board issues.
            if issues_by_key:
                print("  · нет scrum-досок со спринтом — даты из openSprints()")
                try:
                    primary_sprint_raw, _ = _sprint_from_open_sprints_jql(
                        client, cfg, fields=["summary"]
                    )
                except CollectError:
                    today = datetime.now(timezone.utc).date()
                    primary_sprint_raw = {
                        "id": None,
                        "name": "Board issues",
                        "state": "active",
                        "startDate": (today - timedelta(days=14)).isoformat(),
                        "endDate": (today + timedelta(days=14)).isoformat(),
                        "source": "kanban_window",
                    }
            else:
                raise CollectError(
                    "Ни на одной доске из team.json → jira.boards не найдены задачи.\n"
                    "Для kanban укажите has_sprints: false; для scrum — numeric board id."
                )
        issues = list(issues_by_key.values())
        issues = _filter_kanban_only_to_roster(issues)
    else:
        progress("jira_sprint", "Jira: boards не заданы — openSprints()")
        primary_sprint_raw, issues = _sprint_from_open_sprints_jql(
            client, cfg, fields=issue_fields
        )
        for issue in issues:
            issue["_board_has_epics"] = True

    progress("jira_issues", f"Jira: получено задач — {len(issues)}")

    # Probe sample issue for Epic Link if catalog discovery failed
    if not epic_field_id and issues:
        sample_key = next((i.get("key") for i in issues if i.get("key")), None)
        if sample_key:
            progress("jira_epics", "Jira: ищу Epic Link по примеру задачи…")
            probed = client.discover_epic_field_from_sample(sample_key)
            if probed:
                epic_field_id = probed
                print(f"  · epic custom field (from sample): {epic_field_id}")
                if epic_field_id not in issue_fields:
                    issue_fields.append(epic_field_id)
                # Refresh only issues still missing epic — no full sprint re-download
                missing_keys = [
                    (i.get("key") or "").upper()
                    for i in (issues_by_key.values() if issues_by_key else issues)
                    if i.get("_board_has_epics") is not False
                    and not i.get("_epic_key")
                    and not _extract_epic_info(i.get("fields") or {}, None)
                    and i.get("key")
                ]
                if missing_keys:
                    progress(
                        "jira_epics",
                        f"Jira: дозагрузка Epic Link для {len(missing_keys)} задач…",
                    )
                    refreshed = client.fetch_issues_by_keys(
                        missing_keys, fields=issue_fields
                    )
                    target = (
                        issues_by_key
                        if issues_by_key
                        else {
                            (i.get("key") or "").upper(): i
                            for i in issues
                            if i.get("key")
                        }
                    )
                    for issue in refreshed:
                        key = (issue.get("key") or "").upper()
                        if not key:
                            continue
                        prev = target.get(key)
                        if prev:
                            issue["_board_id"] = prev.get("_board_id")
                            issue["_board_name"] = prev.get("_board_name")
                            issue["_board_has_epics"] = prev.get(
                                "_board_has_epics", True
                            )
                        target[key] = issue
                    issues = list(target.values())
                    if issues_by_key is not None:
                        issues_by_key.update(target)

    for issue in issues:
        if issue.get("_board_has_epics") is False:
            continue
        info = _extract_epic_info(issue.get("fields") or {}, epic_field_id)
        if info:
            _apply_epic(issue, info)

    linked = sum(1 for i in issues if i.get("_epic_key"))
    progress("jira_epics", f"Jira: эпики на задачах {linked}/{len(issues)}")

    # Board epic mapping only where coverage for that board is still poor
    epic_boards = [b for b in boards if b.has_epics] if boards else []
    if epic_boards:
        progress("jira_epics", "Jira: сопоставляю эпики через board API…")
        attached_total = 0
        for board in epic_boards:
            board_issue_subset = [
                i
                for i in issues
                if i.get("_board_id") == board.id or not i.get("_board_id")
            ]
            if not board_issue_subset:
                continue
            linked_sub = sum(1 for i in board_issue_subset if i.get("_epic_key"))
            if linked_sub >= max(3, len(board_issue_subset) // 10):
                print(
                    f"  · board {board.id}: epic coverage ok "
                    f"({linked_sub}/{len(board_issue_subset)}) — skip board epics"
                )
                continue
            attached = _enrich_missing_epics_via_board(
                client, board.id, board_issue_subset
            )
            attached_total += attached
            print(f"  · board {board.id}: +{attached} epic links")
        linked = sum(1 for i in issues if i.get("_epic_key"))
        if attached_total:
            print(f"  · attached via board epics: {attached_total}")

    # Last resort: per-issue agile endpoint only if still almost no epics
    epic_candidates = [i for i in issues if i.get("_board_has_epics") is not False]
    linked_candidates = sum(1 for i in epic_candidates if i.get("_epic_key"))
    if epic_candidates and linked_candidates < max(1, len(epic_candidates) // 20):
        progress("jira_epics", "Jira: дозагрузка эпиков по задачам…")
        attached = _enrich_missing_epics_via_agile(client, epic_candidates)
        print(f"  · attached via agile issue: {attached}")

    sprint = _normalize_sprint(primary_sprint_raw)
    start_date = (
        date.fromisoformat(sprint["start_date"])
        if sprint["start_date"]
        else datetime.now(timezone.utc).date()
    )
    end_date = (
        date.fromisoformat(sprint["end_date"])
        if sprint["end_date"]
        else datetime.now(timezone.utc).date()
    )
    # Expand worklog window across all boards' sprints
    for s, e in sprint_date_candidates:
        if s and s < start_date:
            start_date = s
        if e and e > end_date:
            end_date = e
    today = datetime.now(timezone.utc).date()
    worklog_until = min(end_date, today)

    progress("jira_issues", f"Jira: ищу ссылки на GitLab MR ({len(issues)} задач)…")
    linked_mrs = _enrich_gitlab_links_from_jira(
        client, issues, on_progress=on_progress
    )
    print(f"  · gitlab MR links from Jira: {linked_mrs}")

    progress("jira_worklogs", f"Jira: загружаю worklogs по {len(issues)} задачам…")
    try:
        worklogs = _collect_worklogs_for_issues(
            client,
            issues,
            since_date=start_date,
            until_date=worklog_until,
            on_progress=on_progress,
        )
    except CollectError as exc:
        print(f"  ! worklogs skipped: {exc}")
        worklogs = []

    progress(
        "jira_changelog",
        f"Jira: загружаю историю статусов по {len(issues)} задачам…",
    )
    try:
        changelogs = _collect_changelogs_for_issues(
            client,
            issues,
            on_progress=on_progress,
        )
    except CollectError as exc:
        print(f"  ! changelogs skipped: {exc}")
        changelogs = []

    progress(
        "jira_comments",
        f"Jira: загружаю комментарии по {len(issues)} задачам…",
    )
    try:
        comments = _collect_comments_for_issues(
            client,
            issues,
            on_progress=on_progress,
        )
    except CollectError as exc:
        print(f"  ! comments skipped: {exc}")
        comments = []

    progress("jira_epics", "Jira: собираю метаданные эпиков…")
    epics = _collect_epics(
        client,
        [i for i in issues if i.get("_board_has_epics") is not False],
        epic_field_id,
    )

    progress("jira_epics", "Jira: загружаю релизы…")
    releases, release_issues = _collect_releases(
        client,
        cfg,
        sprint=sprint,
        issue_fields=issue_fields,
        on_progress=on_progress,
    )

    # Enrich release issues with epic links, then pull full epic scope for timeline
    release_epic_keys: list[str] = []
    seen_epic: set[str] = set()
    for issue in release_issues:
        info = _extract_epic_info(issue.get("fields") or {}, epic_field_id)
        if not info:
            continue
        _apply_epic(issue, info)
        ek = info["key"]
        if ek not in seen_epic:
            seen_epic.add(ek)
            release_epic_keys.append(ek)

    epic_scope_issues: list[dict] = []
    if release_epic_keys:
        progress(
            "jira_epics",
            f"Jira: полный объём эпиков релизов ({len(release_epic_keys)})…",
        )
        epic_scope_issues = _fetch_issues_for_epics(
            client,
            release_epic_keys,
            fields=issue_fields,
            epic_field_id=epic_field_id,
            projects=list(cfg.jira_projects),
        )
        # cf[NNNN] returns only the epic key — restore titles from epics meta
        for issue in epic_scope_issues:
            ek = (issue.get("_epic_key") or "").upper()
            meta = epics.get(ek) or {}
            title = meta.get("summary")
            if title and str(title).strip().upper() != ek:
                issue["_epic_summary"] = title
                fields = issue.setdefault("fields", {})
                epic_obj = fields.get("epic") if isinstance(fields.get("epic"), dict) else {}
                fields["epic"] = {
                    "key": ek,
                    "summary": title,
                    "name": title,
                    **{k: v for k, v in epic_obj.items() if k not in {"key", "summary", "name"}},
                }

    progress("jira_epics", "Jira: профили roster (аватары / username)…")
    roster_profiles = _fetch_roster_jira_profiles(client)

    progress(
        "jira_epics",
        f"Jira: готово — {len(issues)} задач, {len(worklogs)} списаний, "
        f"{len(changelogs)} changelog, {len(comments)} комментариев, "
        f"{len(epics)} эпиков, {len(releases)} релизов",
    )

    return {
        "source": "jira",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "browse_base": cfg.jira_url.rstrip("/"),
        "base_jql": base,
        "board_id": primary_board_id,
        "boards": board_summaries,
        "expected_hours_per_day": cfg.jira_expected_hours_per_day,
        "team": cfg.jira_team,
        "sprint": sprint,
        "issues": issues,
        "worklogs": worklogs,
        "changelogs": changelogs,
        "comments": comments,
        "epics": epics,
        "epic_field_id": epic_field_id,
        "releases": releases,
        "release_issues": release_issues,
        "release_epic_keys": release_epic_keys,
        "epic_scope_issues": epic_scope_issues,
        "roster_profiles": roster_profiles,
        "since": sprint.get("start_at") or start_date.isoformat(),
        "days": max((end_date - start_date).days, 1),
    }

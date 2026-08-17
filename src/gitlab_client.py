from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from .config import DATA_DIR, Config
from .errors import CollectError
from .fetch_cache import JsonFileCache
from .parallel import map_parallel_with_client

GITLAB_WORKERS = 2


class GitLabClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "PRIVATE-TOKEN": cfg.gitlab_token,
                "Accept": "application/json",
            }
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.cfg.gitlab_api_base}{path}"
        params = dict(params or {})
        params.setdefault("per_page", 100)

        for attempt in range(5):
            try:
                resp = self.session.get(url, params=params, timeout=60)
            except requests.exceptions.RequestException as exc:
                raise CollectError(
                    f"GitLab request failed: {exc}\n"
                    f"URL: {url}\n"
                    "Check VPN, GITLAB_URL, and token access."
                ) from exc

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "2"))
                time.sleep(retry_after)
                continue
            if resp.status_code >= 400:
                detail = resp.text[:300].replace("\n", " ")
                raise CollectError(
                    f"GitLab API error {resp.status_code} for {url}\n"
                    f"Response: {detail}\n"
                    "Check token scopes (read_api), project path, and access rights."
                )
            return resp

        raise CollectError(f"GitLab rate limit persists for {url}")

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        params = dict(params or {})
        params["page"] = 1
        items: list[dict] = []

        while True:
            resp = self._get(path, params)
            batch = resp.json()
            if not batch:
                break
            items.extend(batch)
            next_page = resp.headers.get("X-Next-Page")
            if not next_page:
                break
            params["page"] = int(next_page)

        return items

    @staticmethod
    def project_id(project: str) -> str:
        if project.isdigit():
            return project
        return quote(project, safe="")

    def fetch_project(self, project: str) -> dict:
        return self._get(f"/projects/{self.project_id(project)}").json()

    def fetch_merge_requests(
        self,
        project: str,
        *,
        state: str,
        updated_after: datetime | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "state": state,
            "order_by": "updated_at",
            "sort": "desc",
            "with_labels_details": "false",
        }
        if updated_after is not None:
            params["updated_after"] = updated_after.isoformat()
        return self._paginate(
            f"/projects/{self.project_id(project)}/merge_requests",
            params,
        )

    def fetch_mr_commits(self, project: str, iid: int) -> list[dict]:
        return self._paginate(
            f"/projects/{self.project_id(project)}/merge_requests/{iid}/commits"
        )


def _collect_one_project(
    client: GitLabClient, project_ref: str, since: datetime
) -> dict:
    project = client.fetch_project(project_ref)
    merged = client.fetch_merge_requests(
        project_ref, state="merged", updated_after=since
    )
    # Open MRs: keep full list for linking, but commits are enriched selectively later.
    open_mrs = client.fetch_merge_requests(project_ref, state="opened")
    return {
        "ref": project_ref,
        "id": project["id"],
        "name": project.get("name_with_namespace") or project.get("name"),
        "web_url": project.get("web_url"),
        "merge_requests_merged": merged,
        "merge_requests_open": open_mrs,
    }


def collect_raw(
    cfg: Config,
    *,
    since: datetime | None = None,
    on_progress=None,
) -> dict:
    since = since or (datetime.now(timezone.utc) - timedelta(days=cfg.days))
    refs = list(cfg.projects)
    total = max(len(refs), 1)

    def progress(done: int, all_n: int) -> None:
        if on_progress:
            label = refs[done - 1] if 0 < done <= len(refs) else ""
            on_progress(
                "gitlab",
                f"GitLab: проект {done}/{all_n}" + (f" — {label}" if label else ""),
                current=done,
                total=all_n,
            )

    def worker(client: GitLabClient, project_ref: str) -> dict:
        return _collect_one_project(client, project_ref, since)

    projects_out = map_parallel_with_client(
        refs,
        lambda: GitLabClient(cfg),
        worker,
        max_workers=min(GITLAB_WORKERS, max(len(refs), 1)),
        on_progress=progress if refs else None,
        progress_every=1,
    )

    roster_profiles = _fetch_roster_gitlab_profiles(cfg)

    return {
        "source": "gitlab",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "days": cfg.days,
        "since": since.isoformat(),
        "projects": projects_out,
        "roster_profiles": roster_profiles,
    }


def _fetch_roster_gitlab_profiles(cfg: Config) -> dict[str, dict]:
    """
    Resolve GitLab username/avatar for roster members with avatar_source=gitlab
    (and any with gitlab_username), even if they authored no MRs in-window.
    """
    import re

    from .team import TEAM_ROSTER
    from .team_config import get_team_config

    team_cfg = get_team_config()
    client = GitLabClient(cfg)
    out: dict[str, dict] = {}
    hits = 0

    for name in sorted(TEAM_ROSTER.keys(), key=lambda x: x.lower()):
        source = team_cfg.avatar_source_for(name)
        explicit = team_cfg.gitlab_username_for(name)
        if source != "gitlab" and not explicit:
            continue
        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        for alias, target in team_cfg.aliases.items():
            if target != name:
                continue
            token = str(alias).strip()
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,64}", token) and token not in candidates:
                candidates.append(token)
        # Last resort: surname search (helps when alias ≠ GitLab username)
        surname = name.split()[0] if name.split() else ""
        if surname and surname not in candidates:
            candidates.append(surname)

        profile: dict[str, Any] = {}
        for q in candidates:
            try:
                resp = client._get("/users", {"search": q, "per_page": 10})
                users = resp.json() if resp is not None else []
            except CollectError:
                continue
            if not isinstance(users, list):
                continue
            for user in users:
                uname = (user.get("username") or "").strip()
                display = (user.get("name") or "").strip()
                # Prefer exact username / alias hit; else display-name match
                q_l = q.lower()
                if uname.lower() == q_l or q_l in uname.lower() or (
                    display and name.split()[0].lower() in display.lower()
                ):
                    profile = {
                        "username": uname,
                        "name": display or name,
                        "avatar_url": user.get("avatar_url"),
                        "web_url": user.get("web_url"),
                    }
                    hits += 1
                    break
            if profile:
                break
        if profile:
            out[name] = profile

    print(f"  · roster gitlab profiles: {hits}/{sum(1 for n in TEAM_ROSTER if team_cfg.avatar_source_for(n)=='gitlab' or team_cfg.gitlab_username_for(n))}")
    return out


def _mr_text_keys(mr: dict) -> set[str]:
    from .linking import extract_issue_keys

    return set(
        extract_issue_keys(
            mr.get("title"),
            mr.get("description"),
            mr.get("source_branch"),
            mr.get("target_branch"),
        )
    )


def _should_enrich_mr_commits(
    mr: dict,
    *,
    bucket: str,
    issue_keys: set[str],
    linked_mr_urls: set[str],
) -> bool:
    """
    Merged MRs in the sprint window always get commits (ratings / helpers).
    Open MRs: only when tied to sprint issues (key in title/branch or Jira link).
    """
    if bucket == "merge_requests_merged":
        return True
    web = (mr.get("web_url") or "").rstrip("/").lower()
    if web and web in linked_mr_urls:
        return True
    # normalize .../merge_requests/1 vs .../-/merge_requests/1
    if web:
        alt = web.replace("/-/merge_requests/", "/merge_requests/")
        alt2 = web.replace("/merge_requests/", "/-/merge_requests/")
        if alt in linked_mr_urls or alt2 in linked_mr_urls:
            return True
    if not issue_keys:
        return True
    return bool(_mr_text_keys(mr) & issue_keys)


def _apply_commit_payload(mr: dict, payload: dict) -> None:
    mr["commit_count"] = int(payload.get("commit_count") or 0)
    mr["commits_by_author"] = dict(payload.get("commits_by_author") or {})
    mr["commit_events"] = list(payload.get("commit_events") or [])
    mr["commit_messages"] = list(payload.get("commit_messages") or [])
    mr["issue_keys_from_commits"] = list(payload.get("issue_keys_from_commits") or [])


def _empty_commit_payload() -> dict:
    return {
        "commit_count": 0,
        "commits_by_author": {},
        "commit_events": [],
        "commit_messages": [],
        "issue_keys_from_commits": [],
    }


def _build_commit_payload(commits: list[dict]) -> dict:
    from .linking import extract_issue_keys

    by_author: dict[str, int] = {}
    commit_events: list[dict] = []
    commit_messages: list[str] = []
    keys_from_commits: list[str] = []
    seen_keys: set[str] = set()
    for commit in commits:
        author = (
            (commit.get("author_name") or "").strip()
            or (commit.get("committer_name") or "").strip()
            or (commit.get("author_email") or "").strip()
        )
        if author:
            by_author[author] = by_author.get(author, 0) + 1
        committed_at = (
            commit.get("committed_date")
            or commit.get("authored_date")
            or commit.get("created_at")
        )
        if author and committed_at:
            commit_events.append(
                {
                    "author": author,
                    "committed_at": committed_at,
                }
            )
        message = (commit.get("title") or commit.get("message") or "").strip()
        if message:
            commit_messages.append(message.split("\n", 1)[0][:300])
            for key in extract_issue_keys(message):
                if key not in seen_keys:
                    seen_keys.add(key)
                    keys_from_commits.append(key)
    return {
        "commit_count": len(commits),
        "commits_by_author": by_author,
        "commit_events": commit_events,
        "commit_messages": commit_messages[:40],
        "issue_keys_from_commits": keys_from_commits,
    }


def enrich_mr_commit_counts(
    cfg: Config,
    gitlab_raw: dict,
    *,
    issue_keys: set[str] | None = None,
    jira_raw: dict | None = None,
    on_progress=None,
) -> None:
    """
    Attach commit_count and commits_by_author for selected MRs.

    Optimizations (safe):
    - skip open MRs not linked to sprint issues / Jira MR links
    - reuse disk cache when MR updated_at unchanged
    - bounded parallel fetch with thread-local clients
    """
    if not gitlab_raw:
        return

    keys = {(k or "").upper() for k in (issue_keys or set()) if k}
    linked_mr_urls: set[str] = set()
    if jira_raw:
        for issue in jira_raw.get("issues") or []:
            for ref in issue.get("_gitlab_mrs") or []:
                url = (ref.get("web_url") or "").rstrip("/").lower()
                if url:
                    linked_mr_urls.add(url)

    cache = JsonFileCache(DATA_DIR / "cache" / "mr_commits.json")
    to_fetch: list[tuple[str, dict]] = []
    skipped_open = 0
    cache_hits = 0

    for project in gitlab_raw.get("projects") or []:
        project_ref = project.get("ref")
        if not project_ref:
            continue
        for bucket_name in ("merge_requests_merged", "merge_requests_open"):
            for mr in project.get(bucket_name) or []:
                if not _should_enrich_mr_commits(
                    mr,
                    bucket=bucket_name,
                    issue_keys=keys,
                    linked_mr_urls=linked_mr_urls,
                ):
                    _apply_commit_payload(mr, _empty_commit_payload())
                    skipped_open += 1
                    continue
                iid = mr.get("iid")
                if iid is None:
                    _apply_commit_payload(mr, _empty_commit_payload())
                    continue
                updated = str(mr.get("updated_at") or "")
                # v2 stores author + commit timestamp for exact sprint filtering.
                cache_key = f"v2|{project_ref}|{iid}|{updated}"
                cached = cache.get(cache_key)
                if (
                    isinstance(cached, dict)
                    and "commit_count" in cached
                    and "commit_events" in cached
                ):
                    _apply_commit_payload(mr, cached)
                    cache_hits += 1
                    continue
                to_fetch.append((project_ref, mr))

    total = len(to_fetch)
    print(
        f"  · commits: fetch={total}, cache={cache_hits}, "
        f"skip_open_unlinked={skipped_open}"
    )

    def progress(done: int, all_n: int) -> None:
        if on_progress:
            on_progress(
                "commits",
                f"GitLab: коммиты MR {done}/{all_n}",
                current=done,
                total=max(all_n, 1),
            )

    def worker(client: GitLabClient, item: tuple[str, dict]) -> tuple[str, dict, dict]:
        project_ref, mr = item
        iid = int(mr["iid"])
        updated = str(mr.get("updated_at") or "")
        cache_key = f"v2|{project_ref}|{iid}|{updated}"
        try:
            commits = client.fetch_mr_commits(project_ref, iid)
            payload = _build_commit_payload(commits)
        except Exception:
            payload = {
                "commit_count": int(mr.get("commit_count") or 0),
                "commits_by_author": dict(mr.get("commits_by_author") or {}),
                "commit_events": list(mr.get("commit_events") or []),
                "commit_messages": list(mr.get("commit_messages") or []),
                "issue_keys_from_commits": list(mr.get("issue_keys_from_commits") or []),
            }
        return cache_key, mr, payload

    if to_fetch:
        results = map_parallel_with_client(
            to_fetch,
            lambda: GitLabClient(cfg),
            worker,
            max_workers=GITLAB_WORKERS,
            on_progress=progress,
            progress_every=3,
        )
        updates: dict[str, Any] = {}
        for cache_key, mr, payload in results:
            _apply_commit_payload(mr, payload)
            updates[cache_key] = payload
        cache.set_many(updates)

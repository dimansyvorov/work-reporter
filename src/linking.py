from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

ISSUE_KEY_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]+-\d+)\b")
# GitLab MR URLs: .../group/project/-/merge_requests/123  or  .../merge_requests/123
MR_URL_RE = re.compile(
    r"(https?://[^\s\"'<>]+?)/(?:-/)?merge_requests/(\d+)",
    re.IGNORECASE,
)


def extract_issue_keys(*texts: str | None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in ISSUE_KEY_RE.findall(str(text)):
            key = match.upper()
            if key not in seen:
                seen.add(key)
                found.append(key)
    return found


def extract_mr_refs_from_text(*texts: str | None) -> list[dict]:
    """Parse GitLab merge-request URLs → {web_url, project_path, iid}."""
    found: list[dict] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in MR_URL_RE.finditer(str(text)):
            base = match.group(1).rstrip("/")
            iid = int(match.group(2))
            web_url = f"{base}/-/merge_requests/{iid}"
            key = web_url.lower()
            if key in seen:
                continue
            seen.add(key)
            path = urlparse(base).path.strip("/")
            path = re.sub(r"/-$", "", path)
            found.append(
                {
                    "web_url": web_url,
                    "project_path": path,
                    "iid": iid,
                }
            )
    return found


def _mr_issue_keys(mr: dict) -> list[str]:
    texts = [
        mr.get("title"),
        mr.get("description"),
        mr.get("source_branch"),
        mr.get("target_branch"),
    ]
    # Commit messages collected during enrich
    for msg in mr.get("commit_messages") or []:
        texts.append(msg)
    for key in mr.get("issue_keys_from_commits") or []:
        texts.append(str(key))
    return extract_issue_keys(*texts)


def _person_name(author: dict | None) -> str:
    if not author:
        return "Неизвестный автор"
    return (author.get("name") or author.get("username") or "Неизвестный автор").strip()


def _person_avatar(author: dict | None) -> str | None:
    if not author:
        return None
    return author.get("avatar_url") or None


def _normalize_mr_url(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip().rstrip("/").lower()
    if "/-/merge_requests/" not in text:
        text = text.replace("/merge_requests/", "/-/merge_requests/")
    text = text.replace("/-/-/merge_requests/", "/-/merge_requests/")
    return text


def _mr_identity(mr: dict) -> tuple[str | None, int | None, str | None]:
    """Return (project_ref_or_path, iid, normalized_web_url)."""
    iid = mr.get("iid")
    try:
        iid_i = int(iid) if iid is not None else None
    except (TypeError, ValueError):
        iid_i = None
    project = (
        mr.get("project_ref")
        or mr.get("project_path")
        or mr.get("project")
        or ""
    )
    project = str(project).strip() or None
    return project, iid_i, _normalize_mr_url(mr.get("web_url"))


def iter_mrs(gitlab_raw: dict | None) -> list[dict]:
    if not gitlab_raw:
        return []
    mrs: list[dict] = []
    for project in gitlab_raw.get("projects") or []:
        project_name = project.get("name")
        project_ref = project.get("ref") or ""
        for state, bucket in (
            ("merged", project.get("merge_requests_merged") or []),
            ("opened", project.get("merge_requests_open") or []),
        ):
            for mr in bucket:
                keys = _mr_issue_keys(mr)
                author = mr.get("author") or {}
                mrs.append(
                    {
                        "iid": mr.get("iid"),
                        "title": mr.get("title"),
                        "state": state,
                        "author": _person_name(author),
                        "avatar_url": _person_avatar(author),
                        "web_url": mr.get("web_url"),
                        "project": project_name,
                        "project_ref": project_ref,
                        "source_branch": mr.get("source_branch"),
                        "issue_keys": keys,
                        "commit_count": mr.get("commit_count"),
                        "commits_by_author": mr.get("commits_by_author") or {},
                    }
                )
    return mrs


def _issue_remote_mr_refs(issue: dict) -> list[dict]:
    """MR refs attached on the Jira issue (remote links / parsed URLs)."""
    refs: list[dict] = []
    seen: set[str] = set()

    def add(ref: dict) -> None:
        url = _normalize_mr_url(ref.get("web_url"))
        token = url or f"{ref.get('project_path')}|{ref.get('iid')}"
        if not token or token in seen:
            return
        seen.add(token)
        refs.append(ref)

    for ref in issue.get("_gitlab_mrs") or []:
        add(ref)

    fields = issue.get("fields") or {}
    # Description / environment sometimes contain MR links (esp. mobile)
    for text in (
        fields.get("description"),
        fields.get("environment"),
    ):
        if isinstance(text, str):
            for ref in extract_mr_refs_from_text(text):
                add(ref)
        elif isinstance(text, dict):
            # ADF document — flatten strings
            blob = str(text)
            for ref in extract_mr_refs_from_text(blob):
                add(ref)

    return refs


def _mrs_match_ref(mr: dict, ref: dict) -> bool:
    project, iid, url = _mr_identity(mr)
    ref_iid = ref.get("iid")
    try:
        ref_iid_i = int(ref_iid) if ref_iid is not None else None
    except (TypeError, ValueError):
        ref_iid_i = None
    ref_url = _normalize_mr_url(ref.get("web_url"))
    if url and ref_url and url == ref_url:
        return True
    if iid is None or ref_iid_i is None or iid != ref_iid_i:
        return False
    ref_path = (ref.get("project_path") or "").strip().lower()
    if not ref_path or not project:
        return False
    proj = project.lower()
    return (
        proj == ref_path
        or proj.endswith("/" + ref_path)
        or ref_path.endswith("/" + proj)
        or proj.endswith(ref_path)
        or ref_path.endswith(proj)
    )


def link_sprint_issues(
    sprint_issues: list[dict],
    gitlab_raw: dict | None,
    *,
    browse_base: str = "",
) -> dict:
    browse_base = browse_base.rstrip("/")
    mrs = iter_mrs(gitlab_raw)
    mrs_by_issue: dict[str, list[dict]] = defaultdict(list)
    mrs_without_key = []
    linked_mr_ids: set[tuple] = set()

    def mr_dedupe_key(mr: dict) -> tuple:
        project, iid, url = _mr_identity(mr)
        return (project or "", iid or 0, url or "")

    def attach(key: str, mr: dict) -> None:
        bucket = mrs_by_issue[key]
        token = mr_dedupe_key(mr)
        for existing in bucket:
            if mr_dedupe_key(existing) == token:
                return
        bucket.append(mr)
        linked_mr_ids.add(token)

    # 1) MR → issue via keys in title/description/branch/commits
    for mr in mrs:
        if mr["issue_keys"]:
            for key in mr["issue_keys"]:
                attach(key, mr)
        else:
            mrs_without_key.append(mr)

    # 2) Issue → MR via Jira remote links / URLs in description
    for issue in sprint_issues:
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        for ref in _issue_remote_mr_refs(issue):
            matched = False
            for mr in mrs:
                if _mrs_match_ref(mr, ref):
                    attach(key, mr)
                    matched = True
            if not matched and ref.get("web_url"):
                # Keep a stub so commit_count can still show the link in UI later
                attach(
                    key,
                    {
                        "iid": ref.get("iid"),
                        "title": f"MR !{ref.get('iid')}",
                        "state": "linked",
                        "author": "—",
                        "avatar_url": None,
                        "web_url": ref.get("web_url"),
                        "project": ref.get("project_path"),
                        "project_ref": ref.get("project_path"),
                        "source_branch": None,
                        "issue_keys": [key],
                        "commit_count": None,
                        "commits_by_author": {},
                        "from_jira_link": True,
                    },
                )

    issues_by_key: dict[str, dict] = {}
    for issue in sprint_issues:
        key = (issue.get("key") or "").upper()
        if not key:
            continue
        fields = issue.get("fields") or {}
        assignee = fields.get("assignee") or {}
        issues_by_key[key] = {
            "key": key,
            "summary": fields.get("summary"),
            "status": ((fields.get("status") or {}).get("name")),
            "done": bool(fields.get("resolution"))
            or ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
            == "done",
            "assignee": assignee.get("displayName")
            or assignee.get("name")
            or "Без исполнителя",
            "avatar_url": _jira_avatar(assignee),
            "web_url": f"{browse_base}/browse/{key}" if browse_base else None,
            "mrs": mrs_by_issue.get(key) or [],
        }

    issues_without_mr = [
        {
            "key": issue["key"],
            "summary": issue["summary"],
            "status": issue["status"],
            "assignee": issue["assignee"],
            "avatar_url": issue["avatar_url"],
            "web_url": issue["web_url"],
        }
        for issue in issues_by_key.values()
        if not issue["mrs"] and not issue["done"]
    ]

    return {
        "issues_by_key": issues_by_key,
        "mrs_by_issue": dict(mrs_by_issue),
        "issues_without_mr": issues_without_mr[:30],
        "mrs_without_key": [
            {
                "iid": mr["iid"],
                "title": mr["title"],
                "state": mr["state"],
                "author": mr["author"],
                "avatar_url": mr["avatar_url"],
                "web_url": mr["web_url"],
                "project": mr["project"],
            }
            for mr in mrs_without_key
            if mr_dedupe_key(mr) not in linked_mr_ids
        ][:20],
    }


def _jira_avatar(person: dict | None) -> str | None:
    if not person:
        return None
    urls = person.get("avatarUrls") or {}
    return urls.get("48x48") or urls.get("32x32") or urls.get("24x24") or urls.get("16x16")

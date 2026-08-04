from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .team import TEAM_ROSTER
from .team_config import get_team_config


def build_mock_raw(days: int = 30) -> dict:
    """Synthetic demo payload for `python run.py --mock` (no API access)."""
    now = datetime.now(timezone.utc)
    # Mid/late sprint so half-gated ratings unlock in demo
    start = (now - timedelta(days=9)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (now + timedelta(days=5)).replace(hour=23, minute=59, second=59, microsecond=0)
    team_cfg = get_team_config()

    def iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    roster = list(TEAM_ROSTER.keys())
    people = [
        {
            "displayName": name,
            "avatarUrls": {"48x48": f"https://www.gravatar.com/avatar/{i}?d=identicon"},
        }
        for i, name in enumerate(roster, start=1)
    ]

    statuses = [
        {"name": "К выполнению", "statusCategory": {"key": "new"}},
        {"name": "В работе", "statusCategory": {"key": "indeterminate"}},
        {"name": "Ревью", "statusCategory": {"key": "indeterminate"}},
        {"name": "Готово", "statusCategory": {"key": "done"}},
    ]

    epics = {
        "DEMO-1": {"key": "DEMO-1", "summary": "Эпик: Направление A", "status": "In Progress"},
        "DEMO-2": {"key": "DEMO-2", "summary": "Эпик: Направление B", "status": "In Progress"},
        "DEMO-3": {"key": "DEMO-3", "summary": "Эпик: Направление C", "status": "In Progress"},
        "DEMO-4": {"key": "DEMO-4", "summary": "Эпик: Направление D", "status": "In Progress"},
    }
    epic_by_dir = {}
    for idx, dir_name in enumerate(team_cfg.direction_order):
        epic_by_dir[dir_name] = f"DEMO-{(idx % 4) + 1}"

    project_refs = list(team_cfg.gitlab_projects_keys.keys()) or [
        "demo-group/mobile",
        "demo-group/web",
        "demo-group/backend",
    ]

    def project_for_direction(direction_name: str) -> str:
        for ref, dname in team_cfg.gitlab_projects.items():
            if dname == direction_name:
                return ref
        return project_refs[0]

    issues = []
    for i, person in enumerate(people, start=1):
        status = statuses[i % len(statuses)]
        done = status["statusCategory"]["key"] == "done"
        direction = TEAM_ROSTER[person["displayName"]]
        epic_key = epic_by_dir.get(direction, "DEMO-1")
        key = f"DEMO-{100 + i}"
        estimate_h = 6 + (i % 5)
        spent_h = estimate_h + (2 if i % 3 == 0 else -1 if i % 3 == 1 else 0)
        if i % 7 == 0:
            summary = f"Поддержка: {direction} #{i}"
        elif i % 11 == 0:
            summary = f"Прилеты: срочный фикс #{i}"
        else:
            summary = f"{direction}: задача #{i}"
        remaining_h = 0 if done else max(estimate_h - max(spent_h, 0), 0)
        sprint_fields = [{"id": 42, "state": "active", "name": "Sprint Demo"}]
        if i % 5 == 0 and not done:
            sprint_fields = [
                {"id": 41, "state": "closed", "name": "Sprint Prev"},
                {"id": 42, "state": "active", "name": "Sprint Demo"},
            ]
            remaining_h = max(remaining_h, 48)
        issues.append(
            {
                "key": key,
                "_epic_key": epic_key,
                "_epic_summary": epics[epic_key]["summary"],
                "fields": {
                    "summary": summary,
                    "status": status,
                    "issuetype": {"name": "Story"},
                    "assignee": person,
                    "resolution": {"name": "Done"} if done else None,
                    "created": iso(start + timedelta(days=1)),
                    "updated": iso(now - timedelta(days=(i % 5), hours=i)),
                    "resolutiondate": iso(now - timedelta(days=1)) if done else None,
                    "timeoriginalestimate": estimate_h * 3600,
                    "aggregatetimeoriginalestimate": estimate_h * 3600,
                    "timeestimate": remaining_h * 3600,
                    "aggregatetimeestimate": remaining_h * 3600,
                    "timespent": max(spent_h, 1) * 3600,
                    "sprint": sprint_fields,
                    "closedSprints": [s for s in sprint_fields if s.get("state") == "closed"],
                    "epic": {
                        "key": epic_key,
                        "summary": epics[epic_key]["summary"],
                        "name": epics[epic_key]["summary"],
                    },
                    "fixVersions": [
                        {
                            "id": "1001",
                            "name": "Demo 1.0",
                            "released": False,
                            "releaseDate": (now + timedelta(days=4)).date().isoformat(),
                        }
                    ],
                },
            }
        )

    # Latin-ish author labels derived from roster order (no hardcoded real names)
    gl_authors = {
        name: {
            "name": f"demo.user{i}",
            "avatar_url": f"https://www.gravatar.com/avatar/{100 + i}?d=identicon",
        }
        for i, name in enumerate(roster, start=1)
    }

    helper_name = next(
        (n for n, d in TEAM_ROSTER.items() if d == "Бэкенд"),
        roster[min(1, len(roster) - 1)] if roster else "Demo Helper",
    )

    merged = []
    for i, issue in enumerate(issues[:8], start=1):
        assignee = issue["fields"]["assignee"]["displayName"]
        author = gl_authors.get(
            assignee,
            {
                "name": assignee,
                "avatar_url": f"https://www.gravatar.com/avatar/{200 + i}?d=identicon",
            },
        )
        key = issue["key"]
        created = start + timedelta(days=1, hours=i)
        commit_count = 2 + (i % 5)
        commits_by_author = {author["name"]: commit_count}
        if i <= 2:
            helper_author = gl_authors.get(helper_name, {"name": helper_name})
            commits_by_author[helper_author["name"]] = 3
            commit_count += 3
        direction = TEAM_ROSTER.get(assignee) or ""
        backendish = direction == "Бэкенд"
        title = (
            f"Implement service layer #{i}"
            if backendish
            else f"{key} | Feature #{i}"
        )
        description = "" if backendish else key
        branch = f"feature/task-{i}" if backendish else f"feature/{key.lower()}"
        project_path = project_for_direction(direction)
        mr_url = f"https://gitlab.example.com/{project_path}/-/merge_requests/{100 + i}"
        merged.append(
            {
                "iid": 100 + i,
                "title": title,
                "description": description,
                "source_branch": branch,
                "state": "merged",
                "author": author,
                "created_at": iso(created),
                "merged_at": iso(created + timedelta(hours=8)),
                "updated_at": iso(created + timedelta(hours=8)),
                "web_url": mr_url,
                "commit_count": commit_count,
                "commits_by_author": commits_by_author,
                "commit_messages": [f"{key}: implement feature {i}", "cleanup"],
                "issue_keys_from_commits": [key],
            }
        )
        if backendish:
            issue["_gitlab_mrs"] = [
                {
                    "web_url": mr_url,
                    "project_path": project_path,
                    "iid": 100 + i,
                }
            ]
            issue["fields"]["description"] = f"See MR {mr_url}"

    helper_author = gl_authors.get(
        helper_name,
        {"name": "demo.helper", "avatar_url": "https://www.gravatar.com/avatar/199?d=identicon"},
    )
    helper_project = project_for_direction("Бэкенд")
    merged.append(
        {
            "iid": 199,
            "title": "help another direction",
            "description": "",
            "source_branch": "fix/help-other",
            "state": "merged",
            "author": helper_author,
            "created_at": iso(start + timedelta(days=2)),
            "merged_at": iso(start + timedelta(days=2, hours=4)),
            "updated_at": iso(start + timedelta(days=2, hours=4)),
            "web_url": f"https://gitlab.example.com/{helper_project}/-/merge_requests/199",
            "commit_count": 4,
            "commits_by_author": {helper_author["name"]: 4},
            "commit_messages": ["DEMO-103: assist"],
            "issue_keys_from_commits": ["DEMO-103"],
        }
    )

    worklogs = []
    for i in range(0, 6):
        day = (now - timedelta(days=i)).replace(hour=12, minute=0, second=0, microsecond=0)
        for j, person in enumerate(people[:10]):
            hours = 8 if (i + j) % 4 else 3
            issue = issues[j % len(issues)]
            worklogs.append(
                {
                    "issue_key": issue["key"],
                    "issue_summary": issue["fields"]["summary"],
                    "id": str(1000 + i * 20 + j),
                    "started": iso(day),
                    "time_spent_seconds": hours * 3600,
                    "author": person,
                }
            )

    changelogs: list[dict] = []
    if len(people) >= 2 and len(issues) >= 3:
        p0, p1 = people[0]["displayName"], people[1]["displayName"]
        yday = (now - timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
        # skip weekend for "yesterday" demo
        while yday.weekday() >= 5:
            yday -= timedelta(days=1)
        today_am = now.replace(hour=10, minute=30, second=0, microsecond=0)
        changelogs.extend(
            [
                {
                    "issue_key": issues[0]["key"],
                    "issue_summary": issues[0]["fields"]["summary"],
                    "at": iso(yday),
                    "author": p0,
                    "status_from": "В работе",
                    "status_to": "In Review",
                    "assignee_from": p0,
                    "assignee_to": p0,
                },
                {
                    "issue_key": issues[1]["key"],
                    "issue_summary": issues[1]["fields"]["summary"],
                    "at": iso(yday.replace(hour=17)),
                    "author": p0,
                    "status_from": "In Review",
                    "status_to": "Ready for testing",
                    "assignee_from": p0,
                    "assignee_to": p1,
                },
                {
                    "issue_key": issues[2]["key"],
                    "issue_summary": issues[2]["fields"]["summary"],
                    "at": iso(today_am),
                    "author": p1,
                    "status_from": "Сделать",
                    "status_to": "В работе",
                    "assignee_from": p1,
                    "assignee_to": p1,
                },
            ]
        )

    projects_out = []
    for idx, ref in enumerate(project_refs[:3]):
        chunk = merged[idx * 2 : (idx + 1) * 2] if idx < 2 else merged[4:]
        projects_out.append(
            {
                "ref": ref,
                "id": idx + 1,
                "name": ref.split("/")[-1],
                "web_url": f"https://gitlab.example.com/{ref}",
                "merge_requests_merged": chunk,
                "merge_requests_open": [],
            }
        )

    return {
        "source": "mock",
        "fetched_at": iso(now),
        "gitlab": {
            "source": "mock",
            "fetched_at": iso(now),
            "days": (end.date() - start.date()).days,
            "since": iso(start),
            "projects": projects_out,
        },
        "jira": {
            "source": "mock",
            "fetched_at": iso(now),
            "browse_base": "https://jira.example.com",
            "base_jql": "project in (DEMO)",
            "board_id": "1",
            "expected_hours_per_day": 8,
            "team": [],
            "sprint": {
                "id": 42,
                "name": "Sprint Demo",
                "state": "active",
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
                "start_at": iso(start),
                "end_at": iso(end),
                "goal": "Demo sprint goal",
            },
            "issues": issues,
            "worklogs": worklogs,
            "changelogs": changelogs,
            "comments": [
                {
                    "issue_key": issues[0]["key"],
                    "at": iso(now - timedelta(hours=5)),
                    "author": people[0]["displayName"],
                    "body": "Демо-комментарий: проверил на стенде, ок.",
                }
            ]
            if issues
            else [],
            "epics": epics,
            "releases": [
                {
                    "id": "1001",
                    "name": "Demo 1.0",
                    "project": "DEMO",
                    "released": False,
                    "release_date": (now + timedelta(days=4)).date().isoformat(),
                    "description": "Demo release",
                }
            ],
            "release_issues": issues[:12],
            "release_epic_keys": list(epics.keys()),
            "epic_scope_issues": issues,
        },
    }

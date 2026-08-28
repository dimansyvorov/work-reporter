from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.ratings import compute_ratings
from src.team import TEAM_ROSTER


TZ = ZoneInfo("Europe/Moscow")


def _issue(key: str, assignee: str, *, status: str = "Done", estimate: float = 8.0) -> dict:
    return {
        "key": key,
        "_canonical_assignee": assignee,
        "_direction": TEAM_ROSTER[assignee],
        "fields": {
            "summary": key,
            "assignee": {"displayName": assignee},
            "status": {"name": status, "statusCategory": {"key": "done"}},
            "resolution": {"name": "Done"},
            "timeoriginalestimate": int(estimate * 3600),
        },
    }


def _sprint() -> dict:
    return {
        "id": 351,
        "name": "Sprint 15 | 17.08.26-28.08.26",
        "start_date": "2026-08-17",
        "end_date": "2026-08-28",
        "start_at": "2026-08-17T00:00:00+03:00",
        "end_at": "2026-08-28T23:59:59+03:00",
    }


def _ratings(
    *,
    issues=None,
    changelogs=None,
    gitlab=None,
    now=None,
    hours_by_person_issue=None,
    hours_by_person_day=None,
    worklogs=None,
) -> list[dict]:
    return compute_ratings(
        sprint=_sprint(),
        team_rows=[],
        issues=issues or [],
        hours_by_person_day=hours_by_person_day or {},
        hours_by_issue={},
        hours_by_person_issue=hours_by_person_issue or {},
        worklogs=worklogs or [],
        changelogs=changelogs or [],
        links={},
        gitlab_raw=gitlab,
        expected_hours=8.0,
        now=now or datetime(2026, 8, 17, 12, 0, tzinfo=TZ),
    )


def _category(rows: list[dict], category_id: str) -> dict:
    return next(row for row in rows if row["id"] == category_id)


class RatingsWindowTest(unittest.TestCase):
    def test_closer_counts_only_done_transition_inside_sprint(self):
        person = next(iter(TEAM_ROSTER))
        rows = _ratings(
            issues=[_issue("SPRINT-OLD", person), _issue("SPRINT-NEW", person)],
            changelogs=[
                {
                    "issue_key": "SPRINT-OLD",
                    "at": "2026-08-16T16:00:00+03:00",
                    "status_from": "In Progress",
                    "status_to": "Done",
                },
                {
                    "issue_key": "SPRINT-NEW",
                    "at": "2026-08-17T10:00:00+03:00",
                    "status_from": "In Progress",
                    "status_to": "Done",
                },
            ],
        )

        closer = _category(rows, "closer")
        person_row = next(row for row in closer["all_people"] if row["name"] == person)
        self.assertEqual(person_row["score"], 1)
        self.assertEqual([task["key"] for task in person_row["tasks"]], ["SPRINT-NEW"])

    def test_closer_credits_owner_at_transition_not_current_assignee(self):
        first, second = list(TEAM_ROSTER)[:2]
        rows = _ratings(
            issues=[_issue("SPRINT-1", second)],
            changelogs=[
                {
                    "issue_key": "SPRINT-1",
                    "at": "2026-08-17T10:00:00+03:00",
                    "status_from": "In Progress",
                    "status_to": "Done",
                },
                {
                    "issue_key": "SPRINT-1",
                    "at": "2026-08-17T11:00:00+03:00",
                    "assignee_from": first,
                    "assignee_to": second,
                },
            ],
        )

        closer = _category(rows, "closer")
        scores = {row["name"]: row["score"] for row in closer["all_people"]}
        self.assertEqual(scores.get(first), 1)
        self.assertNotIn(second, scores)

    def test_committer_counts_unique_repository_commits_inside_window(self):
        person = next(iter(TEAM_ROSTER))
        gitlab = {
            "projects": [
                {
                    "ref": "demo/project",
                    "repository_commit_events": [
                        {
                            "id": "before-window",
                            "author": person,
                            "committed_at": "2026-08-16T12:00:00+03:00",
                        },
                        {
                            "id": "inside-window",
                            "author": person,
                            "committed_at": "2026-08-17T11:00:00+03:00",
                        },
                        {
                            "id": "inside-window",
                            "author": person,
                            "committed_at": "2026-08-17T11:00:00+03:00",
                        },
                    ],
                }
            ]
        }

        committer = _category(_ratings(gitlab=gitlab), "committer")
        self.assertEqual(committer["all_people"][0]["score"], 1)

    def test_committer_does_not_use_mr_commit_events(self):
        person = next(iter(TEAM_ROSTER))
        gitlab = {
            "projects": [
                {
                    "ref": "demo/project",
                    "merge_requests_open": [
                        {
                            "commit_events": [
                                {
                                    "author": person,
                                    "committed_at": "2026-08-17T11:00:00+03:00",
                                }
                            ]
                        }
                    ],
                }
            ]
        }

        committer = _category(_ratings(gitlab=gitlab), "committer")
        self.assertFalse(committer["enabled"])
        self.assertEqual(committer["all_people"], [])

    def test_half_sprint_gate_uses_elapsed_work_time(self):
        before_half = _ratings(now=datetime(2026, 8, 20, 18, 0, tzinfo=TZ))
        at_half = _ratings(now=datetime(2026, 8, 21, 18, 0, tzinfo=TZ))

        self.assertFalse(_category(before_half, "truant")["enabled"])
        self.assertTrue(_category(at_half, "truant")["enabled"])

    def test_stability_uses_employee_local_current_date(self):
        person = next(iter(TEAM_ROSTER))
        hours = {
            day: 8.0
            for day in (
                "2026-08-17",
                "2026-08-18",
                "2026-08-19",
                "2026-08-20",
                "2026-08-21",
                "2026-08-24",
            )
        }
        hours["2026-08-25"] = 1.0
        instant = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)

        def local_now(name, now):
            offset = 10 if name == person else 3
            return now.astimezone(timezone(timedelta(hours=offset)))

        with patch("src.ratings.person_now", side_effect=local_now):
            rows = _ratings(
                hours_by_person_day={person: hours},
                now=instant,
            )

        category = _category(rows, "stability")
        person_row = next(
            row for row in category["all_people"] if row["name"] == person
        )
        self.assertLess(person_row["stability"]["pace_pct"], 100)

    def test_underestimator_includes_active_tasks_and_shows_task_count(self):
        person = next(iter(TEAM_ROSTER))
        issue = _issue("SPRINT-ACTIVE", person, status="In Progress", estimate=8.0)
        rows = _ratings(
            issues=[issue],
            hours_by_person_issue={person: {"SPRINT-ACTIVE": 11.0}},
            now=datetime(2026, 8, 24, 18, 0, tzinfo=TZ),
        )

        category = _category(rows, "underestimator")
        person_row = next(row for row in category["all_people"] if row["name"] == person)
        self.assertEqual(person_row["score"], 3.0)
        self.assertEqual(person_row["value"], "1 задача · +3 ч")
        self.assertEqual(person_row["tasks"][0]["status"], "In Progress")

    def test_overestimator_keeps_closed_task_count_and_adds_hours(self):
        person = next(iter(TEAM_ROSTER))
        rows = _ratings(
            issues=[_issue("SPRINT-DONE", person, estimate=8.0)],
            changelogs=[
                {
                    "issue_key": "SPRINT-DONE",
                    "at": "2026-08-20T17:00:00+03:00",
                    "status_from": "In Progress",
                    "status_to": "Done",
                }
            ],
            hours_by_person_issue={person: {"SPRINT-DONE": 3.0}},
            now=datetime(2026, 8, 24, 18, 0, tzinfo=TZ),
        )

        category = _category(rows, "overestimator")
        person_row = next(row for row in category["all_people"] if row["name"] == person)
        self.assertEqual(person_row["score"], 5.0)
        self.assertEqual(person_row["tiebreak"], 1)
        self.assertEqual(person_row["value"], "1 задача · -5 ч")

    def test_overestimator_ranks_by_hours_then_task_count(self):
        first, second = list(TEAM_ROSTER)[:2]
        issues = [
            _issue("SPRINT-BIG-GAP", first, estimate=20.0),
            _issue("SPRINT-SMALL-GAP-1", second, estimate=8.0),
            _issue("SPRINT-SMALL-GAP-2", second, estimate=8.0),
        ]
        changelogs = [
            {
                "issue_key": issue["key"],
                "at": f"2026-08-20T{hour:02d}:00:00+03:00",
                "status_from": "In Progress",
                "status_to": "Done",
            }
            for hour, issue in enumerate(issues, start=10)
        ]
        rows = _ratings(
            issues=issues,
            changelogs=changelogs,
            hours_by_person_issue={
                first: {"SPRINT-BIG-GAP": 5.0},
                second: {
                    "SPRINT-SMALL-GAP-1": 5.0,
                    "SPRINT-SMALL-GAP-2": 5.0,
                },
            },
            now=datetime(2026, 8, 24, 18, 0, tzinfo=TZ),
        )

        category = _category(rows, "overestimator")
        self.assertEqual(
            [row["name"] for row in category["all_people"][:2]],
            [first, second],
        )
        self.assertEqual(category["all_people"][0]["value"], "1 задача · -15 ч")
        self.assertEqual(category["all_people"][1]["value"], "2 задачи · -6 ч")


if __name__ == "__main__":
    unittest.main()

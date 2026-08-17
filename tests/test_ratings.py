from __future__ import annotations

import unittest
from datetime import datetime
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


def _ratings(*, issues=None, changelogs=None, gitlab=None, now=None) -> list[dict]:
    return compute_ratings(
        sprint=_sprint(),
        team_rows=[],
        issues=issues or [],
        hours_by_person_day={},
        hours_by_issue={},
        hours_by_person_issue={},
        worklogs=[],
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

    def test_committer_filters_individual_commit_timestamps(self):
        person = next(iter(TEAM_ROSTER))
        gitlab = {
            "projects": [
                {
                    "ref": "demo/project",
                    "merge_requests_open": [
                        {
                            "commit_count": 2,
                            "commits_by_author": {person: 2},
                            "commit_events": [
                                {
                                    "author": person,
                                    "committed_at": "2026-08-16T12:00:00+03:00",
                                },
                                {
                                    "author": person,
                                    "committed_at": "2026-08-17T11:00:00+03:00",
                                },
                            ],
                        }
                    ],
                    "merge_requests_merged": [],
                }
            ]
        }

        committer = _category(_ratings(gitlab=gitlab), "committer")
        self.assertEqual(committer["all_people"][0]["score"], 1)

    def test_half_sprint_gate_uses_elapsed_work_time(self):
        before_half = _ratings(now=datetime(2026, 8, 20, 18, 0, tzinfo=TZ))
        at_half = _ratings(now=datetime(2026, 8, 21, 18, 0, tzinfo=TZ))

        self.assertFalse(_category(before_half, "truant")["enabled"])
        self.assertTrue(_category(at_half, "truant")["enabled"])


if __name__ == "__main__":
    unittest.main()

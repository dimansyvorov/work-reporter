from __future__ import annotations

import unittest
from unittest.mock import patch

from src.ai_brief import build_ai_snapshot
from src.ai_release_briefs import _apply_briefs_to_releases, generate_ai_release_briefs
from src.metrics import _compute_team_mood


class AiSprintScopeTest(unittest.TestCase):
    def test_snapshot_keeps_only_releases_inside_sprint_and_matches_goal_date(self):
        report = {
            "sprint": {
                "name": "Sprint 14",
                "goal": "Релиз 12.08",
                "start_date": "2026-08-03",
                "end_date": "2026-08-14",
            },
            "team_mood": {"drivers": []},
            "risks": {
                "at_risk": [
                    {"key": "IN-1"},
                    {"key": "OUT-1", "release_outside_sprint": True},
                ]
            },
            "releases": [
                {
                    "name": "front 1.19.0",
                    "release_date": "2026-08-12",
                    "in_sprint": True,
                    "risk": "on_track",
                    "tasks": [],
                },
                {
                    "name": "front 24.08",
                    "release_date": "2026-08-24",
                    "in_sprint": False,
                    "risk": "at_risk",
                    "tasks": [],
                },
            ],
            "directions": [],
            "team": [],
        }

        snapshot = build_ai_snapshot(report)

        self.assertEqual([r["name"] for r in snapshot["sprint_releases"]], ["front 1.19.0"])
        self.assertEqual([r["name"] for r in snapshot["goal"]["releases"]], ["front 1.19.0"])
        self.assertEqual(snapshot["risky_releases"], [])
        self.assertEqual(snapshot["risk_counts"]["at_risk"], 1)
        self.assertEqual(snapshot["risk_examples"]["at_risk"], ["IN-1"])

    def test_future_release_does_not_change_team_mood(self):
        sprint = {
            "tasks_progress_pct": 60,
            "time_progress_pct": 70,
            "total": 10,
            "open": 4,
            "done": 6,
            "days_left": 3,
        }
        risks = {
            "at_risk": [],
            "stale": [],
            "no_worklogs": [],
            "no_estimate": [],
            "no_release": [],
        }
        baseline = _compute_team_mood(
            sprint=sprint,
            risks=risks,
            releases=[],
            epic_timeline={"epics": []},
        )
        with_future = _compute_team_mood(
            sprint=sprint,
            risks=risks,
            releases=[
                {
                    "name": "future",
                    "released": False,
                    "in_sprint": False,
                    "risk": "overdue",
                    "slip_gap_pp": 80,
                }
            ],
            epic_timeline={"epics": []},
        )

        self.assertEqual(with_future["score"], baseline["score"])
        self.assertFalse(any(row.get("id") == "releases_risk" for row in with_future["drivers"]))

    def test_release_brief_is_never_attached_to_released_version(self):
        sprint_report = {
            "releases": [
                {"name": "done", "release_date": "2026-08-12", "released": True},
                {"name": "open", "release_date": "2026-08-12", "released": False},
            ]
        }
        briefs = {
            "2026-08-12": {
                "status": "ok",
                "markdown": "В графике.",
                "verdict": "В графике.",
            }
        }

        _apply_briefs_to_releases(sprint_report, briefs)

        self.assertNotIn("ai_brief", sprint_report["releases"][0])
        self.assertEqual(sprint_report["releases"][1]["ai_brief"]["text"], "В графике.")

    @patch("src.ai_release_briefs._generate_group")
    @patch("src.ai_release_briefs._llm_settings")
    def test_release_bundle_is_partial_when_only_some_groups_succeed(
        self, mocked_settings, mocked_group
    ):
        mocked_settings.return_value = {
            "enabled": True,
            "url": "https://example.test",
            "token": "secret",
            "model": "/models/Qwen3.6",
            "temperature": 0.2,
            "timeout": 45,
            "max_tokens": 4096,
            "cache_sec": 3000,
        }
        mocked_group.side_effect = [
            {"status": "ok", "reason": None},
            {"status": "error", "error": "timeout"},
        ]
        bundle = generate_ai_release_briefs(
            {
                "releases": [
                    {"id": "1", "name": "one", "release_date": "2026-08-20"},
                    {"id": "2", "name": "two", "release_date": "2026-08-21"},
                ]
            }
        )

        self.assertEqual(bundle["status"], "partial")
        self.assertEqual(bundle["ok_count"], 1)
        self.assertEqual(bundle["total"], 2)


if __name__ == "__main__":
    unittest.main()

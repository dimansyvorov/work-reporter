from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from src.ai_sprint_review import (
    build_sprint_review_snapshot,
    sprint_review_available,
)


MOSCOW = ZoneInfo("Europe/Moscow")


class SprintReviewTest(unittest.TestCase):
    def setUp(self):
        self.sprint = {
            "id": 14,
            "name": "Sprint 14 | 03.08.26-14.08.26",
            "start_date": "2026-08-03",
            "end_date": "2026-08-14",
            "goal": "Подготовить августовские релизы",
            "done": 1,
            "total": 2,
            "tasks_progress_pct": 50,
        }

    def test_review_opens_friday_at_workday_start(self):
        self.assertFalse(
            sprint_review_available(
                self.sprint,
                now=datetime(2026, 8, 14, 8, 59, tzinfo=MOSCOW),
            )
        )
        self.assertTrue(
            sprint_review_available(
                self.sprint,
                now=datetime(2026, 8, 14, 9, 0, tzinfo=MOSCOW),
            )
        )
        self.assertTrue(
            sprint_review_available(
                self.sprint,
                now=datetime(2026, 8, 16, 12, 0, tzinfo=MOSCOW),
            )
        )

    def test_new_sprint_hides_previous_review_card(self):
        next_sprint = {
            "id": 15,
            "start_date": "2026-08-17",
            "end_date": "2026-08-28",
        }
        self.assertFalse(
            sprint_review_available(
                next_sprint,
                now=datetime(2026, 8, 17, 10, 0, tzinfo=MOSCOW),
            )
        )

    def test_snapshot_prepares_history_anomalies_and_release_topic_matches(self):
        report = {
            "sprint": self.sprint,
            "issues": {
                "SPRINT-1": {
                    "key": "SPRINT-1",
                    "summary": "Сложная задача",
                    "status": "В работе",
                    "direction_state": "active",
                    "assignee": "Иван И.",
                    "in_current_sprint": True,
                    "history": [
                        {
                            "at": "2026-08-11T10:00:00+03:00",
                            "status_from": "К выполнению",
                            "status_to": "В работе",
                        },
                        {
                            "at": "2026-08-13T10:00:00+03:00",
                            "status_from": "Ревью",
                            "status_to": "В работе",
                        },
                    ],
                    "worklogs": [
                        {"at": "2026-08-13T12:00:00+03:00", "hours": 6},
                    ],
                }
            },
            "releases": [
                {
                    "id": "200",
                    "name": "Release 20.08",
                    "release_date": "2026-08-20",
                    "description": "БУ расходка + Аварийны 3.0",
                    "released": False,
                    "in_sprint": False,
                    "tasks": [
                        {
                            "key": "SPRINT-20",
                            "summary": "БУ расходные материалы",
                            "sprints": [
                                {"id": 15, "name": "Sprint 15", "state": "future"}
                            ],
                        }
                    ],
                }
            ],
        }

        snapshot = build_sprint_review_snapshot(
            report,
            now=datetime(2026, 8, 16, 12, 0, tzinfo=MOSCOW),
        )

        self.assertTrue(snapshot["next_planning"]["forecast"])
        self.assertEqual(snapshot["next_planning"]["start_date"], "2026-08-17")
        self.assertEqual(snapshot["next_planning"]["end_date"], "2026-08-28")
        self.assertIn("возвратов по статусам: 1", snapshot["history_analysis"]["anomalies"][0]["signals"])
        topics = snapshot["next_planning"]["releases"][0]["key_topics"]
        matched = next(row for row in topics if row["topic"] == "БУ расходка")
        self.assertEqual(matched["match_state"], "matched")
        uncertain = next(row for row in topics if row["topic"] == "Аварийны 3.0")
        self.assertEqual(uncertain["match_state"], "uncertain")


if __name__ == "__main__":
    unittest.main()

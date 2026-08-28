from __future__ import annotations

import unittest
from datetime import date

from src.metrics import _build_task_tags, _has_risk_tags


class ClosedTaskRiskTest(unittest.TestCase):
    def _tags(self, direction_state: str) -> list[dict]:
        return _build_task_tags(
            fields={
                "summary": "Demo task",
                "sprint": [
                    {"id": 14, "state": "closed"},
                    {"id": 15, "state": "active"},
                ],
                "fixVersions": [
                    {
                        "id": "200",
                        "name": "Demo 1.0",
                        "released": False,
                        "releaseDate": "2026-08-21",
                    }
                ],
            },
            direction="Demo",
            direction_state=direction_state,
            sprint_id=15,
            today=date(2026, 8, 19),
            end_d=date(2026, 8, 28),
            expected_hours=8.0,
            inactive_days=5,
        )

    def test_active_carryover_task_has_delay_risk(self):
        tags = self._tags("active")

        self.assertIn("delay", {tag["id"] for tag in tags})
        self.assertTrue(_has_risk_tags(tags))

    def test_done_carryover_task_keeps_only_release_context(self):
        tags = self._tags("done")

        self.assertEqual([tag["id"] for tag in tags], ["release"])
        self.assertFalse(_has_risk_tags(tags))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from src.sprint_window import (
    normalize_sprint_window,
    sprint_business_state,
    sprint_name_window,
)


class SprintWindowTest(unittest.TestCase):
    def test_name_interval_is_business_source_of_truth(self):
        window = normalize_sprint_window(
            name="Sprint 14 | 03.08.26-14.08.26",
            start=datetime(2026, 8, 3, tzinfo=timezone.utc),
            end=datetime(2026, 8, 15, 23, 59, tzinfo=timezone.utc),
        )

        self.assertEqual(window.start, date(2026, 8, 3))
        self.assertEqual(window.end, date(2026, 8, 14))
        self.assertEqual(window.source, "name")

    def test_midnight_jira_end_is_exclusive_without_name_dates(self):
        window = normalize_sprint_window(
            name="Sprint 14",
            start=datetime(2026, 8, 3, tzinfo=timezone.utc),
            end=datetime(2026, 8, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(window.end, date(2026, 8, 14))
        self.assertEqual(window.source, "jira")

    def test_utc_timestamp_is_checked_at_local_midnight(self):
        window = normalize_sprint_window(
            name="Sprint 14",
            start=datetime(2026, 8, 3, tzinfo=timezone.utc),
            end=datetime(2026, 8, 14, 21, tzinfo=timezone.utc),
            timezone_name="Europe/Moscow",
        )

        self.assertEqual(window.end, date(2026, 8, 14))

    def test_finished_interval_overrides_stale_active_jira_state(self):
        state, label = sprint_business_state(
            {
                "state": "active",
                "start_date": "2026-08-03",
                "end_date": "2026-08-14",
            },
            today=date(2026, 8, 16),
        )

        self.assertEqual(state, "closed")
        self.assertEqual(label, "Завершён")

    def test_invalid_name_range_is_ignored(self):
        self.assertIsNone(sprint_name_window("Sprint | 14.08.26-03.08.26"))


if __name__ == "__main__":
    unittest.main()

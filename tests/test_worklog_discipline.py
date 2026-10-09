from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.ratings import _worklog_discipline


TZ = timezone(timedelta(hours=3))


class WorklogDisciplineTest(unittest.TestCase):
    person = "Тестовый Сотрудник"

    def setUp(self):
        canonical = patch(
            "src.ratings.canonical_team_name",
            side_effect=lambda name: name if name == self.person else None,
        )
        canonical.start()
        self.addCleanup(canonical.stop)

    def log(self, day, recorded, hours=8.0, *, updated=None):
        return {
            "author": {"displayName": self.person},
            "started": f"{day}T09:00:00+03:00",
            "created": recorded,
            "updated": updated or recorded,
            "time_spent_seconds": hours * 3600,
        }

    def discipline(self, logs, *, grace=1, tz=TZ):
        return _worklog_discipline(
            self.person,
            worklogs=logs,
            now=datetime(2026, 8, 31, 18, tzinfo=tz),
            expected_hours=8.0,
            start_hour=9.0,
            grace_workdays=grace,
            advance_tolerance_hours=0.5,
        )

    def test_same_day_gets_full_credit(self):
        result = self.discipline([self.log("2026-08-17", "2026-08-17T17:00:00+03:00")])

        self.assertAlmostEqual(result["score"], 100)
        self.assertEqual(result["timely_pct"], 100)
        self.assertEqual(result["recording_timing"]["same_day"], {"hours": 8, "pct": 100})

    def test_next_workday_gets_ninety_percent_credit(self):
        result = self.discipline([self.log("2026-08-17", "2026-08-18T17:00:00+03:00")])

        self.assertAlmostEqual(result["score"], 93)
        self.assertEqual(result["timely_pct"], 90)
        self.assertEqual(result["recording_timing"]["next_workday"], {"hours": 8, "pct": 100})
        self.assertIsNone(result["cadence_pct"])

    def test_after_grace_gets_no_timeliness_credit(self):
        result = self.discipline([self.log("2026-08-17", "2026-08-19T17:00:00+03:00")])

        self.assertAlmostEqual(result["score"], 30)
        self.assertEqual(result["timely_pct"], 0)
        self.assertEqual(result["recording_timing"]["later"], {"hours": 8, "pct": 100})

    def test_credit_and_breakdown_are_weighted_by_hours(self):
        result = self.discipline([
            self.log("2026-08-17", "2026-08-17T17:00:00+03:00", 8),
            self.log("2026-08-17", "2026-08-18T17:00:00+03:00", 2),
            self.log("2026-08-17", "2026-08-19T17:00:00+03:00", 6),
        ])

        self.assertAlmostEqual(result["timely_pct"], (8 + 2 * 0.9) / 16 * 100)
        self.assertEqual(result["known_hours"], 16)
        self.assertEqual(result["recording_timing"], {
            "same_day": {"hours": 8, "pct": 50},
            "next_workday": {"hours": 2, "pct": 12.5},
            "later": {"hours": 6, "pct": 37.5},
            "before_workday": {"hours": 0, "pct": 0},
        })

    def test_splitting_next_day_entry_does_not_change_credit(self):
        single = self.discipline([self.log("2026-08-17", "2026-08-18T17:00:00+03:00")])
        split = self.discipline([
            self.log("2026-08-17", "2026-08-18T10:00:00+03:00", 1),
            self.log("2026-08-17", "2026-08-18T12:00:00+03:00", 3),
            self.log("2026-08-17", "2026-08-18T17:00:00+03:00", 4),
        ])

        self.assertEqual(single, split)

    def test_backfilling_entire_sprint_is_not_treated_as_daily_next_day_logging(self):
        logs = [
            self.log(f"2026-08-{day}", "2026-08-31T10:00:00+03:00")
            for day in (17, 18, 19, 20, 21, 24, 25, 26, 27, 28)
        ]
        result = self.discipline(logs)

        self.assertAlmostEqual(result["timely_pct"], 9)
        self.assertEqual(result["recording_timing"]["next_workday"], {"hours": 8, "pct": 10})
        self.assertEqual(result["recording_timing"]["later"], {"hours": 72, "pct": 90})

    def test_friday_to_weekend_or_monday_is_within_next_workday(self):
        for day in (22, 23, 24):
            with self.subTest(day=day):
                result = self.discipline([
                    self.log("2026-08-21", f"2026-08-{day}T17:00:00+03:00")
                ])
                self.assertEqual(result["timely_pct"], 90)
                self.assertEqual(result["recording_timing"]["next_workday"]["pct"], 100)

        late = self.discipline([self.log("2026-08-21", "2026-08-25T17:00:00+03:00")])
        self.assertEqual(late["timely_pct"], 0)
        self.assertEqual(late["recording_timing"]["later"]["pct"], 100)

    def test_employee_local_date_determines_recording_bucket(self):
        logs = [self.log("2026-08-17", "2026-08-17T22:00:00+03:00")]
        local = self.discipline(logs, tz=timezone(timedelta(hours=7)))
        server = self.discipline(logs)

        self.assertEqual(local["timely_pct"], 90)
        self.assertEqual(local["recording_timing"]["next_workday"]["pct"], 100)
        self.assertEqual(server["timely_pct"], 100)
        self.assertEqual(server["recording_timing"]["same_day"]["pct"], 100)

    def test_existing_last_update_timestamp_rule_is_preserved(self):
        result = self.discipline([
            self.log("2026-08-17", "2026-08-17T17:00:00+03:00",
                     updated="2026-08-19T17:00:00+03:00")
        ])

        self.assertEqual(result["timely_pct"], 0)
        self.assertEqual(result["recording_timing"]["later"]["pct"], 100)

    def test_existing_advance_penalty_is_preserved(self):
        result = self.discipline([self.log("2026-08-17", "2026-08-16T17:00:00+03:00")])

        self.assertEqual(result["timely_pct"], 100)
        self.assertEqual(result["advance_hours"], 8)
        self.assertEqual(result["advance_days"], 1)
        self.assertAlmostEqual(result["score"], 80)
        self.assertEqual(result["recording_timing"]["same_day"]["pct"], 0)
        self.assertEqual(result["recording_timing"]["before_workday"], {"hours": 8, "pct": 100})

    def test_existing_overtime_penalty_and_half_hour_tolerance_are_preserved(self):
        result = self.discipline([self.log("2026-08-17", "2026-08-17T18:00:00+03:00", 9)])

        self.assertEqual(result["timely_pct"], 100)
        self.assertEqual(result["advance_hours"], 0.5)
        self.assertEqual(result["advance_days"], 1)
        self.assertAlmostEqual(result["score"], 100 - 20 * 0.5 / 9)

    def test_existing_cadence_score_is_preserved_including_zero(self):
        result = self.discipline([
            self.log("2026-08-17", "2026-08-17T17:00:00+03:00", 4),
            self.log("2026-08-17", "2026-08-17T17:00:00+03:00", 4),
        ])

        self.assertEqual(result["cadence_pct"], 0)
        self.assertAlmostEqual(result["score"], 90)

    def test_grace_configuration_still_controls_soft_penalty_window(self):
        next_day = [self.log("2026-08-17", "2026-08-18T17:00:00+03:00")]
        self.assertEqual(self.discipline(next_day, grace=0)["timely_pct"], 0)

        second_day = [self.log("2026-08-17", "2026-08-19T17:00:00+03:00")]
        result = self.discipline(second_day, grace=2)
        self.assertEqual(result["timely_pct"], 90)
        self.assertEqual(result["recording_timing"]["later"]["pct"], 100)

    def test_missing_timestamps_are_not_misrepresented_as_zero_percent(self):
        result = self.discipline([self.log("2026-08-17", None)])

        self.assertEqual(result["known_hours"], 0)
        self.assertIsNone(result["timely_pct"])
        self.assertIsNone(result["recording_timing"])

    def test_unknown_authors_and_nonpositive_hours_do_not_affect_breakdown(self):
        unknown = self.log("2026-08-17", "2026-08-19T17:00:00+03:00")
        unknown["author"]["displayName"] = "Другой Сотрудник"
        result = self.discipline([
            unknown,
            self.log("2026-08-17", "2026-08-19T17:00:00+03:00", 0),
            self.log("2026-08-17", "2026-08-19T17:00:00+03:00", -1),
            self.log("2026-08-17", "2026-08-18T17:00:00+03:00", 8),
        ])

        self.assertEqual(result["known_hours"], 8)
        self.assertEqual(result["timely_pct"], 90)
        self.assertEqual(result["recording_timing"]["next_workday"]["pct"], 100)


if __name__ == "__main__":
    unittest.main()

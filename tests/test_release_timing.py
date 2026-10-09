from __future__ import annotations

import unittest

from src.ai_brief import PROMPT_VERSION as SPRINT_PROMPT_VERSION
from src.ai_brief import SYSTEM_PROMPT as SPRINT_SYSTEM_PROMPT
from src.ai_brief import build_ai_snapshot
from src.ai_release_briefs import PROMPT_VERSION as RELEASE_PROMPT_VERSION
from src.ai_release_briefs import SYSTEM_PROMPT as RELEASE_SYSTEM_PROMPT
from src.ai_release_briefs import build_release_group_snapshot
from src.ai_sprint_review import PROMPT_VERSION as REVIEW_PROMPT_VERSION
from src.ai_sprint_review import SYSTEM_PROMPT as REVIEW_SYSTEM_PROMPT
from src.ai_sprint_review import build_sprint_review_snapshot
from src.release_timing import release_publication_facts


class ReleasePublicationTimingTest(unittest.TestCase):
    def test_publication_states_are_deterministic(self):
        cases = (
            ({"released": True, "days_left": -2}, "released", False, False),
            ({"released": False, "days_left": 2}, "before_release_day", False, False),
            ({"released": False, "days_left": 0}, "release_day", True, False),
            ({"released": False, "days_left": -1}, "past_release_day", False, True),
            ({"released": False, "days_left": None}, "date_unknown", False, None),
        )
        for release, phase, expected_evening, overdue in cases:
            with self.subTest(phase=phase):
                facts = release_publication_facts(release)
                self.assertEqual(facts["phase"], phase)
                self.assertEqual(
                    facts["publication_expected_this_evening"], expected_evening
                )
                self.assertEqual(facts["publication_overdue"], overdue)

    def test_sprint_snapshot_does_not_mark_release_day_publication_overdue(self):
        release = {
            "id": "1",
            "name": "demo 1.0",
            "release_date": "2026-09-10",
            "released": False,
            "in_sprint": True,
            "days_left": 0,
            "risk": "at_risk",
            "risk_label": "Риск срыва",
            "tasks": [],
        }
        snapshot = build_ai_snapshot(
            {
                "sprint": {"goal": "Релиз demo 1.0"},
                "team_mood": {"drivers": []},
                "risks": {},
                "releases": [release],
                "directions": [],
                "team": [],
            }
        )

        row = snapshot["sprint_releases"][0]
        self.assertEqual(row["risk"], "at_risk")
        self.assertTrue(row["publication"]["publication_expected_this_evening"])
        self.assertFalse(row["publication"]["publication_overdue"])
        self.assertIn("release_day_rule", snapshot["release_publication_policy"])

    def test_release_group_snapshot_carries_policy_and_prepared_facts(self):
        release = {
            "id": "1",
            "name": "demo 1.0",
            "release_date": "2026-09-10",
            "released": False,
            "days_left": 0,
            "risk": "at_risk",
            "tasks": [],
        }
        snapshot = build_release_group_snapshot(
            {"sprint": {}, "releases": [release]}, [release]
        )

        publication = snapshot["releases"][0]["publication"]
        self.assertTrue(publication["publication_expected_this_evening"])
        self.assertFalse(publication["publication_overdue"])
        self.assertIn("overdue_rule", snapshot["release_publication_policy"])

    def test_sprint_review_snapshot_uses_same_release_day_rule(self):
        release = {
            "id": "1",
            "name": "demo 1.0",
            "release_date": "2026-09-10",
            "released": False,
            "days_left": 0,
            "in_sprint": True,
            "tasks": [],
        }
        snapshot = build_sprint_review_snapshot(
            {
                "sprint": {
                    "id": 1,
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-10",
                },
                "issues": {},
                "releases": [release],
            }
        )

        publication = snapshot["release_outcomes"][0]["publication"]
        self.assertTrue(publication["publication_expected_this_evening"])
        self.assertFalse(publication["publication_overdue"])
        self.assertIn("readiness_rule", snapshot["release_publication_policy"])

    def test_prompts_and_versions_protect_release_day_semantics(self):
        for prompt in (
            SPRINT_SYSTEM_PROMPT,
            RELEASE_SYSTEM_PROMPT,
            REVIEW_SYSTEM_PROMPT,
        ):
            self.assertIn("вечером после", prompt)
            self.assertIn("следующего календарного дня", prompt)
        self.assertEqual(SPRINT_PROMPT_VERSION, "sprint-brief-v9")
        self.assertEqual(RELEASE_PROMPT_VERSION, "release-group-brief-v5")
        self.assertEqual(REVIEW_PROMPT_VERSION, "sprint-review-v3")


if __name__ == "__main__":
    unittest.main()

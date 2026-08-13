from __future__ import annotations

import unittest

from src.ai_reuse import reuse_previous_ai


def _current(sprint_id: int = 42) -> dict:
    return {
        "sprint_report": {
            "sprint": {"id": sprint_id},
            "people": {
                "Current Person": {"ai_note": {"text": "stale"}},
                "New Person": {},
            },
            "releases": [
                {
                    "name": "Current release",
                    "release_date": "2026-08-15",
                    "ai_brief": {"text": "stale"},
                },
                {"name": "No date"},
            ],
        }
    }


def _previous(sprint_id: int = 42) -> dict:
    return {
        "sprint_report": {
            "sprint": {"id": sprint_id},
            "ai_brief": {
                "status": "ok",
                "reason": "generated",
                "generated_at": "2026-08-12T10:00:00+00:00",
                "markdown": "Sprint brief",
            },
            "ai_person_notes": {
                "status": "ok",
                "reason": "generated",
                "generated_at": "2026-08-12T10:01:00+00:00",
                "notes": {
                    "Current Person": {"text": "Current note", "tone": "info"},
                    "Former Person": {"text": "Old note", "tone": "attention"},
                },
            },
            "ai_release_briefs": {
                "status": "ok",
                "reason": "generated",
                "generated_at": "2026-08-12T10:02:00+00:00",
                "briefs": {
                    "2026-08-15": {
                        "status": "ok",
                        "reason": "generated",
                        "generated_at": "2026-08-12T10:02:00+00:00",
                        "markdown": "Current release brief",
                    },
                    "2026-08-20": {
                        "status": "ok",
                        "reason": "generated",
                        "generated_at": "2026-08-12T10:03:00+00:00",
                        "markdown": "Old release brief",
                    },
                },
            },
        }
    }


class ReusePreviousAiTest(unittest.TestCase):
    def test_reuses_only_matching_current_entities_and_preserves_generation_time(self):
        report = reuse_previous_ai(_current(), _previous())
        sprint = report["sprint_report"]

        self.assertEqual(sprint["ai_brief"]["reason"], "reused")
        self.assertEqual(
            sprint["ai_brief"]["generated_at"], "2026-08-12T10:00:00+00:00"
        )
        self.assertEqual(
            set(sprint["ai_person_notes"]["notes"]), {"Current Person"}
        )
        self.assertEqual(
            sprint["people"]["Current Person"]["ai_note"]["text"], "Current note"
        )
        self.assertNotIn("ai_note", sprint["people"]["New Person"])
        self.assertEqual(
            set(sprint["ai_release_briefs"]["briefs"]), {"2026-08-15"}
        )
        self.assertEqual(
            sprint["releases"][0]["ai_brief"]["reason"], "reused"
        )
        self.assertNotIn("ai_brief", sprint["releases"][1])

    def test_different_sprint_skips_and_clears_embedded_ai(self):
        report = reuse_previous_ai(_current(sprint_id=43), _previous(sprint_id=42))
        sprint = report["sprint_report"]

        self.assertEqual(sprint["ai_brief"]["reason"], "different_sprint")
        self.assertEqual(sprint["ai_person_notes"]["notes"], {})
        self.assertEqual(sprint["ai_release_briefs"]["briefs"], {})
        self.assertNotIn("ai_note", sprint["people"]["Current Person"])
        self.assertNotIn("ai_brief", sprint["releases"][0])

    def test_missing_previous_report_skips_all_ai(self):
        sprint = reuse_previous_ai(_current(), None)["sprint_report"]

        self.assertEqual(sprint["ai_brief"]["reason"], "no_previous")
        self.assertEqual(sprint["ai_person_notes"]["status"], "skipped")
        self.assertEqual(sprint["ai_release_briefs"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()

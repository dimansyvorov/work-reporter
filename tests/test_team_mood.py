from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.ai_brief import _call_litellm_chat, humanize_ai_text
from src.metrics import _compute_team_mood, _sprint_day_progress


TZ = ZoneInfo("Europe/Moscow")


def _empty_risks() -> dict:
    return {
        "at_risk": [],
        "stale": [],
        "no_worklogs": [],
        "no_estimate": [],
        "no_release": [],
    }


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {"model": "/models/Qwen3.6", "choices": [{"message": {"content": "OK"}}]}
        ).encode()


class TeamMoodTest(unittest.TestCase):
    def test_sprint_progress_uses_work_hours_and_exact_start(self):
        index, total, progress = _sprint_day_progress(
            {
                "start_date": "2026-08-17",
                "end_date": "2026-08-28",
                "start_at": "2026-08-17T12:30:00+03:00",
            },
            now=datetime(2026, 8, 17, 13, 0, tzinfo=TZ),
            expected_hours=8,
            start_hour=9,
        )

        self.assertEqual((index, total), (1, 10))
        self.assertEqual(progress, 0.7)

    def test_weights_sum_to_100_and_release_impact_is_actual(self):
        mood = _compute_team_mood(
            sprint={
                "tasks_progress_pct": 60,
                "time_progress_pct": 70,
                "total": 10,
                "open": 4,
                "done": 6,
            },
            risks=_empty_risks(),
            releases=[
                *[
                    {
                        "name": f"ok-{idx}",
                        "released": False,
                        "in_sprint": True,
                        "risk": "on_track",
                        "slip_gap_pp": 0,
                    }
                    for idx in range(9)
                ],
                {
                    "name": "risk",
                    "released": False,
                    "in_sprint": True,
                    "risk": "at_risk",
                    "slip_gap_pp": 0,
                },
            ],
            epic_timeline={"epics": []},
        )

        self.assertEqual(sum(mood["weights"].values()), 100)
        release_driver = next(row for row in mood["drivers"] if row["id"] == "releases_risk")
        self.assertLess(release_driver["impact"], 3)

    def test_epic_carryover_is_separate_from_current_risk(self):
        mood = _compute_team_mood(
            sprint={
                "tasks_progress_pct": 40,
                "time_progress_pct": 40,
                "total": 10,
                "open": 6,
                "done": 4,
            },
            risks=_empty_risks(),
            releases=[],
            epic_timeline={
                "epics": [
                    {
                        "sprint_tasks": 10,
                        "sprint_done_tasks": 4,
                        "sprint_open_tasks": 6,
                        "sprint_risk_tasks": 0,
                        "sprint_carryover_tasks": 5,
                        "sprint_progress_pct": 40,
                    }
                ]
            },
        )

        ids = {row["id"] for row in mood["drivers"]}
        self.assertIn("epics_carryover", ids)
        self.assertNotIn("epics_in_sprint_risk", ids)
        self.assertEqual(mood["context"]["epic_risk_tasks"], 0)
        self.assertEqual(mood["context"]["epic_carryover_tasks"], 5)

    @patch("src.ai_brief.urllib.request.urlopen", return_value=_FakeResponse())
    def test_llm_request_disables_reasoning(self, mocked_open):
        content, _model = _call_litellm_chat(
            url="https://example.test/chat/completions",
            token="secret",
            model="/models/Qwen3.6",
            temperature=0.2,
            timeout=45,
            max_tokens=1200,
            system="system",
            user="user",
        )

        self.assertEqual(content, "OK")
        request = mocked_open.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(
            payload["chat_template_kwargs"],
            {"enable_thinking": False, "preserve_thinking": False},
        )
        self.assertEqual(payload["max_tokens"], 1200)

    @patch("src.ai_brief.urllib.request.urlopen", return_value=_FakeResponse())
    def test_llm_request_can_enable_reasoning(self, mocked_open):
        _call_litellm_chat(
            url="https://example.test/chat/completions",
            token="secret",
            model="/models/Qwen3.6",
            temperature=0.2,
            timeout=45,
            reasoning=True,
            system="system",
            user="user",
        )

        request = mocked_open.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(
            payload["chat_template_kwargs"],
            {"enable_thinking": True, "preserve_thinking": True},
        )

    def test_reasoning_preamble_is_removed_from_model_text(self):
        self.assertEqual(
            humanize_ai_text("internal reasoning\n</think>\n\n## Вердикт\nВ графике."),
            "## Вердикт\nВ графике.",
        )


if __name__ == "__main__":
    unittest.main()

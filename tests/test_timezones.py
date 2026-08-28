from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.errors import CollectError
from src.jira_client import _filter_worklogs_for_window
from src.ratings import _worklog_discipline
from src.team_config import DEFAULT_CONFIG_PATH, load_team_config
from src.timezone_utils import person_now, server_now


class TimezoneConfigTest(unittest.TestCase):
    east_name = "Тестов Восток"
    server_name = "Тестов Сервер"

    def tearDown(self):
        load_team_config(DEFAULT_CONFIG_PATH, reload=True)

    def _load_test_config(self):
        payload = {
            "server_timezone": 3,
            "directions": {"qa": {"name": "Тестирование"}},
            "people": [
                {
                    "name": self.east_name,
                    "direction": "qa",
                    "timezone": 10,
                },
                {"name": self.server_name, "direction": "qa"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "team.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return load_team_config(path, reload=True)

    def test_server_and_person_offsets_are_absolute_utc_offsets(self):
        cfg = self._load_test_config()
        self.assertEqual(cfg.server_timezone, 3)
        self.assertEqual(cfg.timezone_offset_for(self.east_name), 10)
        self.assertEqual(cfg.timezone_offset_for(self.server_name), 3)

        instant = datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(server_now(instant).hour, 10)
        self.assertEqual(person_now(self.east_name, instant).hour, 17)

    def test_server_timezone_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "team.json"
            path.write_text(json.dumps({"directions": {}, "people": []}))
            with self.assertRaisesRegex(CollectError, "server_timezone"):
                load_team_config(path, reload=True)

    def test_invalid_person_timezone_is_rejected(self):
        payload = {
            "server_timezone": 3,
            "directions": {"qa": {"name": "Тестирование"}},
            "people": [
                {"name": "Тестовый Сотрудник", "direction": "qa", "timezone": 15}
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "team.json"
            path.write_text(json.dumps(payload, ensure_ascii=False))
            with self.assertRaisesRegex(CollectError, "people.*timezone"):
                load_team_config(path, reload=True)

    def test_advance_hours_use_employee_local_time(self):
        self._load_test_config()
        name = self.east_name
        instant = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        worklogs = [
            {
                "author": {"displayName": name},
                "started": "2026-08-28T00:00:00+03:00",
                "created": "2026-08-28T10:00:00+03:00",
                "updated": "2026-08-28T10:00:00+03:00",
                "time_spent_seconds": 8 * 3600,
            }
        ]

        local = _worklog_discipline(
            name,
            worklogs=worklogs,
            now=person_now(name, instant),
            expected_hours=8,
            start_hour=9,
            grace_workdays=1,
            advance_tolerance_hours=0.5,
        )
        server = _worklog_discipline(
            name,
            worklogs=worklogs,
            now=server_now(instant),
            expected_hours=8,
            start_hour=9,
            grace_workdays=1,
            advance_tolerance_hours=0.5,
        )

        self.assertEqual(local["advance_hours"], 0)
        self.assertGreater(server["advance_hours"], 0)

    def test_worklog_window_uses_author_local_date(self):
        self._load_test_config()
        name = self.east_name
        local_now = datetime(
            2026, 8, 29, 0, 30, tzinfo=timezone(timedelta(hours=10))
        )
        logs = [
            {
                "id": "1",
                "author": {"displayName": name},
                "started": "2026-08-29T00:15:00+10:00",
                "timeSpentSeconds": 3600,
            }
        ]

        with patch("src.jira_client.person_now", return_value=local_now):
            result = _filter_worklogs_for_window(
                logs,
                key="SPRINT-1",
                summary="Проверка часового пояса",
                since_date=date(2026, 8, 17),
                until_date=date(2026, 8, 29),
            )

        self.assertEqual([row["id"] for row in result], ["1"])


if __name__ == "__main__":
    unittest.main()

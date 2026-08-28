from __future__ import annotations

import unittest

from src.jira_client import _fill_missing_epic_metadata


class _FakeJiraClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []

    def search(self, jql: str, fields: list[str] | None = None) -> list[dict]:
        self.calls.append((jql, fields))
        return [
            {
                "key": "DEMO-101",
                "fields": {
                    "summary": "Новый флоу релизов",
                    "status": {"name": "Новая"},
                    "issuetype": {"name": "Epic"},
                },
            },
            {
                "key": "DEMO-202",
                "fields": {
                    "summary": "Хранение адресов размещения",
                    "status": {"name": "В работе"},
                    "issuetype": {"name": "Epic"},
                },
            },
        ]


class EpicMetadataTest(unittest.TestCase):
    def test_placeholder_titles_are_loaded_in_one_batch(self):
        client = _FakeJiraClient()
        epics = {
            "DEMO-101": {
                "key": "DEMO-101",
                "summary": "DEMO-101",
                "status": None,
            },
            "DEMO-202": {
                "key": "DEMO-202",
                "summary": None,
                "status": None,
            },
            "DEMO-303": {
                "key": "DEMO-303",
                "summary": "Уже известный эпик",
                "status": "В работе",
            },
        }

        updated = _fill_missing_epic_metadata(client, epics)

        self.assertEqual(updated, 2)
        self.assertEqual(len(client.calls), 1)
        self.assertIn('"DEMO-101"', client.calls[0][0])
        self.assertIn('"DEMO-202"', client.calls[0][0])
        self.assertNotIn('"DEMO-303"', client.calls[0][0])
        self.assertEqual(epics["DEMO-101"]["summary"], "Новый флоу релизов")
        self.assertEqual(epics["DEMO-202"]["summary"], "Хранение адресов размещения")
        self.assertEqual(epics["DEMO-303"]["summary"], "Уже известный эпик")


if __name__ == "__main__":
    unittest.main()

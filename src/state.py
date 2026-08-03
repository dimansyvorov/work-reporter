from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


PIPELINE_STEPS = [
    ("init", "Подготовка"),
    ("jira_sprint", "Jira: спринт"),
    ("jira_issues", "Jira: задачи"),
    ("jira_epics", "Jira: эпики"),
    ("jira_worklogs", "Jira: worklogs"),
    ("gitlab", "GitLab: merge requests"),
    ("commits", "GitLab: коммиты по MR"),
    ("metrics", "Расчёт метрик и рейтингов"),
    ("save", "Сохранение отчёта"),
]


@dataclass
class AppState:
    status: str = "starting"  # starting | collecting | ready | error
    message: str = "Запуск…"
    steps: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    report: dict[str, Any] | None = None
    updated_at: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _collecting: bool = False

    def _touch(self) -> None:
        self.updated_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "message": self.message,
                "steps": list(self.steps),
                "error": self.error,
                "ready": self.status == "ready",
                "collecting": self._collecting or self.status in {"starting", "collecting"},
                "updated_at": self.updated_at,
                "stale_seconds": round(max(0.0, time.time() - self.updated_at), 1),
            }

    def begin_collection(self, message: str = "Обновляю данные…") -> bool:
        """Start a new collection cycle. Returns False if already collecting."""
        with self._lock:
            if self._collecting or self.status == "collecting":
                return False
            self._collecting = True
            self.status = "collecting"
            self.message = message
            self.error = None
            self.steps = [
                {"key": key, "label": label, "state": "pending"}
                for key, label in PIPELINE_STEPS
            ]
            self._touch()
            return True

    def set_status(self, status: str, message: str) -> None:
        with self._lock:
            self.status = status
            self.message = message
            self._touch()

    def add_step(self, key: str, label: str, state: str = "pending") -> None:
        with self._lock:
            for step in self.steps:
                if step["key"] == key:
                    step["label"] = label
                    step["state"] = state
                    self._touch()
                    return
            self.steps.append({"key": key, "label": label, "state": state})
            self._touch()

    def update_step(self, key: str, state: str, label: str | None = None) -> None:
        with self._lock:
            for step in self.steps:
                if step["key"] == key:
                    step["state"] = state
                    if label is not None:
                        step["label"] = label
                    self._touch()
                    return
            # Unknown key — append so progress is never silent
            self.steps.append(
                {"key": key, "label": label or key, "state": state}
            )
            self._touch()

    def run_step(self, key: str, label: str | None = None) -> None:
        with self._lock:
            for step in self.steps:
                if step["key"] == key:
                    step["state"] = "running"
                    if label is not None:
                        step["label"] = label
                    self._touch()
                    return
            self.steps.append(
                {"key": key, "label": label or key, "state": "running"}
            )
            self._touch()

    def set_ready(self, report: dict[str, Any], message: str = "Отчёт готов") -> None:
        with self._lock:
            self.report = report
            self.status = "ready"
            self.message = message
            self.error = None
            self._collecting = False
            self._touch()

    def set_error(self, error: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = error
            self.message = "Ошибка сбора данных"
            self._collecting = False
            self._touch()

    def get_report(self) -> dict[str, Any] | None:
        with self._lock:
            return self.report

    def is_collecting(self) -> bool:
        with self._lock:
            return self._collecting or self.status == "collecting"

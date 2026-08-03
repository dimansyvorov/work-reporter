from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


class JsonFileCache:
    """Small process-safe JSON blob cache for repeatable API payloads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = {}
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            self._data = {}
        return self._data

    def get(self, key: str) -> Any | None:
        with _LOCK:
            return self._load().get(key)

    def set(self, key: str, value: Any) -> None:
        with _LOCK:
            data = self._load()
            data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def set_many(self, updates: dict[str, Any]) -> None:
        if not updates:
            return
        with _LOCK:
            data = self._load()
            data.update(updates)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)

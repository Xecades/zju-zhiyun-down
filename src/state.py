import json
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            except Exception:
                self._data = {}

    def is_downloaded(self, course_id: str, sub_id: str) -> bool:
        return bool(self._data.get(course_id, {}).get(sub_id))

    def mark_downloaded(self, course_id: str, sub_id: str, info: dict[str, Any]):
        self._data.setdefault(course_id, {})[sub_id] = info
        self._write()

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

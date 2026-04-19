from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


class SceneStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {"version": 1, "console": {}, "scenes": {}}

    def load(self) -> None:
        if self.path.exists():
            self._data = json.loads(self.path.read_text())

    def upsert(
        self,
        bank: str,
        number: int,
        *,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        input_summary: dict[str, str] | None = None,
        notes: str = "",
    ) -> None:
        key = f"{bank}:{number}"
        now = datetime.now(UTC).isoformat()
        existing = self._data["scenes"].get(key, {})
        self._data["scenes"][key] = {
            "bank": bank,
            "number": number,
            "name": name,
            "description": description,
            "tags": tags or [],
            "created_at": existing.get("created_at", now),
            "last_used_at": existing.get("last_used_at"),
            "use_count": existing.get("use_count", 0),
            "notes": notes,
            "input_summary": input_summary or {},
        }

    def mark_recalled(self, bank: str, number: int) -> None:
        key = f"{bank}:{number}"
        entry = self._data["scenes"].get(key)
        if entry is None:
            return
        entry["last_used_at"] = datetime.now(UTC).isoformat()
        entry["use_count"] = entry.get("use_count", 0) + 1

    def get(self, bank: str, number: int) -> dict | None:
        return self._data["scenes"].get(f"{bank}:{number}")

    def list_(
        self,
        bank: str | None = None,
        query: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        items = list(self._data["scenes"].values())
        if bank:
            items = [s for s in items if s["bank"] == bank]
        if query:
            q = query.lower()
            items = [
                s
                for s in items
                if q in s["name"].lower() or q in (s.get("description") or "").lower()
            ]
        if tags:
            items = [s for s in items if set(tags).issubset(set(s.get("tags") or []))]
        return items

    def flush(self) -> None:
        fd, tmp_path = tempfile.mkstemp(prefix="scenes-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

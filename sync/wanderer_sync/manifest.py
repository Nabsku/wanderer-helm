"""Durable, privacy-safe idempotency state."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Manifest:
    """Small JSON manifest keyed by source identity and content digest."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "processed": {}}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("version") != 1:
                raise ValueError(f"unsupported manifest format: {path}")
            processed = loaded.get("processed", {})
            if not isinstance(processed, dict):
                raise ValueError(f"invalid processed section in manifest: {path}")
            self.data = {"version": 1, "processed": processed}

    @property
    def processed(self) -> dict[str, Any]:
        return self.data["processed"]

    def has_uploaded(self, key: str, digest: str) -> bool:
        item = self.processed.get(key)
        return isinstance(item, dict) and item.get("sha256") == digest and item.get("uploaded") is True

    def is_uploaded(self, key: str) -> bool:
        item = self.processed.get(key)
        return isinstance(item, dict) and item.get("uploaded") is True

    def record(self, key: str) -> dict[str, Any] | None:
        item = self.processed.get(key)
        return item if isinstance(item, dict) else None

    def mark_uploaded(
        self,
        key: str,
        digest: str,
        *,
        filename: str,
        source: str,
        trail_id: str | None = None,
    ) -> None:
        self.processed[key] = {
            "sha256": digest,
            "filename": filename,
            "source": source,
            "uploaded": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if trail_id is not None:
            self.processed[key]["trail_id"] = trail_id

    def mark_linked(
        self,
        key: str,
        *,
        filename: str,
        source: str,
        trail_id: str,
    ) -> None:
        self.processed[key] = {
            "sha256": None,
            "filename": filename,
            "source": source,
            "uploaded": True,
            "linked": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.processed[key]["trail_id"] = trail_id

    def update_metadata(
        self,
        key: str,
        *,
        trail_id: str | None = None,
        activity_name: str | None = None,
        activity_type: str | None = None,
        category: str | None = None,
        photo_ids: list[str] | None = None,
    ) -> None:
        item = self.record(key)
        if item is None:
            raise KeyError(f"manifest record not found: {key}")
        if trail_id is not None:
            item["trail_id"] = trail_id
        if activity_name is not None:
            item["activity_name"] = activity_name
        if activity_type is not None:
            item["activity_type"] = activity_type
        if category is not None:
            item["category"] = category
        if photo_ids is not None:
            item["photo_ids"] = list(dict.fromkeys(photo_ids))
        item["updated_at"] = datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

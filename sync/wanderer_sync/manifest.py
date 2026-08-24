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

    def mark_uploaded(self, key: str, digest: str, *, filename: str, source: str) -> None:
        self.processed[key] = {
            "sha256": digest,
            "filename": filename,
            "source": source,
            "uploaded": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

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

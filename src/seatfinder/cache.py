"""Tiny JSON disk cache for city, movie and showtime lookups."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Config


class JsonCache:
    def __init__(self, config: Config, *, enabled: bool = True) -> None:
        self.root = config.cache_dir / "cache"
        self.ttl = config.cache_ttl
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - float(payload.get("stored_at", 0)) > self.ttl:
            return None
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps({"stored_at": time.time(), "value": value}, ensure_ascii=False),
            encoding="utf-8",
        )
        temp.replace(path)

    def get_or_set(self, key: str, producer: Callable[[], Any], *, ttl: int | None = None) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        if ttl is None or ttl >= self.ttl:
            self.set(key, value)
        return value

    def clear(self) -> None:
        if not self.root.exists():
            return
        for path in self.root.glob("*.json"):
            path.unlink(missing_ok=True)

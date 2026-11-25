"""JSON helpers with stable, readable output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def dumps(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def envelope(
    command: str,
    *,
    ok: bool,
    data: Any = None,
    query: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    error: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "command": command,
        "ok": ok,
        "generated_at": now_iso(),
        "query": query or {},
        "data": data,
        "warnings": warnings or [],
        "error": error,
        "meta": meta or {},
    }

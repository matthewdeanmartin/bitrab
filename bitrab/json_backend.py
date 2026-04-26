"""JSON helpers with an optional ``orjson`` fast path."""

from __future__ import annotations

import json as stdlib_json
from typing import Any, Callable

try:
    import orjson as orjson_backend
except ImportError:  # pragma: no cover - exercised by packaging, not test envs
    orjson_backend = None  # type: ignore[assignment]


def loads(data: str | bytes | bytearray | memoryview) -> Any:
    """Deserialize JSON using ``orjson`` when available."""
    if orjson_backend is not None:
        return orjson_backend.loads(data)
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data).decode("utf-8")
    return stdlib_json.loads(data)


def dumps(obj: Any, *, default: Callable[[Any], Any] | None = None, indent: int | None = None) -> str:
    """Serialize *obj* to text, preferring ``orjson`` when available."""
    if orjson_backend is not None:
        option = 0
        if indent:
            option |= orjson_backend.OPT_INDENT_2
        return orjson_backend.dumps(obj, default=default, option=option).decode("utf-8")
    return stdlib_json.dumps(obj, default=default, indent=indent)

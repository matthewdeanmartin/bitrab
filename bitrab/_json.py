"""JSON helpers with an optional ``orjson`` fast path."""

from __future__ import annotations

import json as stdlib_json
from typing import Any, Callable

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover - exercised by packaging, not test envs
    _orjson = None


def loads(data: str | bytes | bytearray | memoryview) -> Any:
    """Deserialize JSON using ``orjson`` when available."""
    if _orjson is not None:
        return _orjson.loads(data)
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data).decode("utf-8")
    return stdlib_json.loads(data)


def dumps(obj: Any, *, default: Callable[[Any], Any] | None = None, indent: int | None = None) -> str:
    """Serialize *obj* to text, preferring ``orjson`` when available."""
    if _orjson is not None:
        option = 0
        if indent:
            option |= _orjson.OPT_INDENT_2
        return _orjson.dumps(obj, default=default, option=option).decode("utf-8")
    return stdlib_json.dumps(obj, default=default, indent=indent)

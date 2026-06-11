from __future__ import annotations

import re
from typing import Any

from bitrab.exceptions import GitlabRunnerError

INTERPOLATION_RE = re.compile(r"\$\[\[\s*([^\]]+?)\s*\]\]")
WHOLE_VALUE_RE = re.compile(r"^\$\[\[\s*([^\]]+?)\s*\]\]$")


def interpolate_inputs(value: Any, inputs: dict[str, str], source: str) -> Any:
    """Recursively interpolate supported GitLab config input expressions."""
    if isinstance(value, str):
        return _interpolate_string(value, inputs, source)
    if isinstance(value, list):
        return [interpolate_inputs(item, inputs, source) for item in value]
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = interpolate_inputs(key, inputs, source) if isinstance(key, str) else key
            result[new_key] = interpolate_inputs(item, inputs, source)
        return result
    return value


def _interpolate_string(value: str, inputs: dict[str, str], source: str) -> str:
    whole_match = WHOLE_VALUE_RE.fullmatch(value)
    if whole_match:
        return _resolve_expression(whole_match.group(1), inputs, source)

    def replace(match: re.Match[str]) -> str:
        return _resolve_expression(match.group(1), inputs, source)

    return INTERPOLATION_RE.sub(replace, value)


def _resolve_expression(expression: str, inputs: dict[str, str], source: str) -> str:
    expression = expression.strip()
    prefix = "inputs."
    if not expression.startswith(prefix):
        raise GitlabRunnerError(f"{source}: unsupported interpolation expression {expression!r}")

    name = expression[len(prefix) :].strip()
    if not name:
        raise GitlabRunnerError(f"{source}: empty input interpolation expression")
    if name not in inputs:
        raise GitlabRunnerError(f"{source}: unknown input reference {name!r}")
    return inputs[name]

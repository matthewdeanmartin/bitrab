from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bitrab.exceptions import GitlabRunnerError


@dataclass(frozen=True)
class InputDefinition:
    """Definition for one GitLab-style config input."""

    name: str
    type: str = "string"
    default: str | None = None
    description: str | None = None
    options: list[str] = field(default_factory=list)
    required: bool = True


def parse_input_definitions(spec: dict[str, Any], source: str) -> dict[str, InputDefinition]:
    """Parse ``spec:inputs`` metadata from a config header document."""
    inputs_raw = spec.get("inputs", {})
    if inputs_raw in (None, {}):
        return {}
    if not isinstance(inputs_raw, dict):
        raise GitlabRunnerError(f"{source}: spec:inputs must be a mapping")

    definitions: dict[str, InputDefinition] = {}
    for name, raw_definition in inputs_raw.items():
        input_name = str(name)
        if raw_definition is None:
            raw_definition = {}
        if not isinstance(raw_definition, dict):
            raw_definition = {"default": raw_definition}

        input_type = str(raw_definition.get("type", "string"))
        if input_type not in {"string"}:
            raise GitlabRunnerError(
                f"{source}: input {input_name!r} uses unsupported type {input_type!r}; only string inputs are supported"
            )

        default = raw_definition.get("default")
        default_value = _coerce_scalar(default, source, input_name, "default") if "default" in raw_definition else None

        options_raw = raw_definition.get("options", [])
        if options_raw in (None, []):
            options: list[str] = []
        elif isinstance(options_raw, list):
            options = [_coerce_scalar(option, source, input_name, "option") for option in options_raw]
        else:
            raise GitlabRunnerError(f"{source}: input {input_name!r} options must be a list")

        if default_value is not None and options and default_value not in options:
            raise GitlabRunnerError(
                f"{source}: input {input_name!r} default {default_value!r} is not one of the allowed options"
            )

        description = raw_definition.get("description")
        definitions[input_name] = InputDefinition(
            name=input_name,
            type=input_type,
            default=default_value,
            description=str(description) if description is not None else None,
            options=options,
            required="default" not in raw_definition,
        )

    return definitions


def resolve_inputs(
    definitions: dict[str, InputDefinition],
    provided: dict[str, Any] | None,
    source: str,
) -> dict[str, str]:
    """Resolve provided include input values against input definitions."""
    provided = provided or {}
    if not isinstance(provided, dict):
        raise GitlabRunnerError(f"{source}: include inputs must be a mapping")

    unknown = sorted(str(name) for name in provided if str(name) not in definitions)
    if unknown:
        joined = ", ".join(repr(name) for name in unknown)
        raise GitlabRunnerError(f"{source}: unknown input(s): {joined}")

    resolved: dict[str, str] = {}
    for name, definition in definitions.items():
        if name in provided:
            value = _coerce_scalar(provided[name], source, name, "value")
        elif definition.default is not None:
            value = definition.default
        elif definition.required:
            raise GitlabRunnerError(f"{source}: missing required input {name!r}")
        else:
            continue

        if definition.options and value not in definition.options:
            allowed = ", ".join(repr(option) for option in definition.options)
            raise GitlabRunnerError(f"{source}: input {name!r} value {value!r} is not one of: {allowed}")
        resolved[name] = value

    return resolved


def _coerce_scalar(value: Any, source: str, input_name: str, field_name: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return str(value)
    raise GitlabRunnerError(
        f"{source}: input {input_name!r} {field_name} must be a scalar string, boolean, or number"
    )

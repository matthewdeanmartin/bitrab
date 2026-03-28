"""ARCH-3: Capability validation layer.

Checks a raw GitLab CI config dict for features that bitrab cannot execute
locally, emitting structured diagnostics before any execution takes place.

Usage::

    from bitrab.config.capabilities import check_capabilities, DiagnosticLevel

    diagnostics = check_capabilities(raw_config)
    errors   = [d for d in diagnostics if d.level == DiagnosticLevel.ERROR]
    warnings = [d for d in diagnostics if d.level == DiagnosticLevel.WARNING]
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DiagnosticLevel(str, Enum):
    ERROR = "error"  # Feature cannot be emulated; execution should be aborted
    WARNING = "warning"  # Feature will be silently ignored during local execution


@dataclass(frozen=True)
class CapabilityDiagnostic:
    level: DiagnosticLevel
    feature: str
    message: str

    def __str__(self) -> str:
        icon = "❌" if self.level == DiagnosticLevel.ERROR else "⚠️ "
        return f"{icon} [{self.feature}] {self.message}"


# ---------------------------------------------------------------------------
# Reserved top-level keywords that are NOT job definitions
# ---------------------------------------------------------------------------

_TOP_LEVEL_NON_JOBS = {
    "stages",
    "variables",
    "default",
    "include",
    "image",
    "services",
    "before_script",
    "after_script",
    "cache",
    "artifacts",
    "workflow",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUPPORTED_RULES_KEYS = {"if", "when", "exists", "allow_failure", "variables"}
_UNSUPPORTED_RULES_KEYS = {"changes"}


def _iter_jobs(raw_config: dict[str, Any]):
    """Yield (name, job_dict) for every job definition in *raw_config*."""
    for name, value in raw_config.items():
        if name not in _TOP_LEVEL_NON_JOBS and isinstance(value, dict):
            yield name, value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_capabilities(raw_config: dict[str, Any]) -> list[CapabilityDiagnostic]:
    """Inspect *raw_config* and return a list of capability diagnostics.

    Args:
        raw_config: The raw configuration dictionary produced by
            ``ConfigurationLoader.load_config()``.

    Returns:
        A list of :class:`CapabilityDiagnostic` objects.  An empty list means
        the config uses only features that bitrab supports.
    """
    diags: list[CapabilityDiagnostic] = []

    # ------------------------------------------------------------------
    # 1. Top-level checks
    # ------------------------------------------------------------------

    # include: component — not supported
    includes = raw_config.get("include", [])
    if isinstance(includes, (str, dict)):
        includes = [includes]
    if isinstance(includes, list):
        for entry in includes:
            if isinstance(entry, dict):
                if "component" in entry:
                    diags.append(
                        CapabilityDiagnostic(
                            level=DiagnosticLevel.ERROR,
                            feature="include:component",
                            message="Component includes are not supported locally. Remove or replace with a local include.",
                        )
                    )
                elif "remote" in entry or "template" in entry:
                    diags.append(
                        CapabilityDiagnostic(
                            level=DiagnosticLevel.WARNING,
                            feature="include:remote/template",
                            message=(f"Remote/template include ({next(k for k in entry if k in ('remote', 'template'))!r}) will be skipped; only local includes are fetched."),
                        )
                    )
                elif "project" in entry:
                    diags.append(
                        CapabilityDiagnostic(
                            level=DiagnosticLevel.WARNING,
                            feature="include:project",
                            message="Cross-project includes are not supported and will be skipped.",
                        )
                    )

    # Top-level inputs: block — not supported
    if "inputs" in raw_config:
        diags.append(
            CapabilityDiagnostic(
                level=DiagnosticLevel.ERROR,
                feature="inputs",
                message="Pipeline-level 'inputs:' blocks are not supported locally.",
            )
        )

    # Top-level image/services — warn (ignored)
    if "image" in raw_config:
        diags.append(
            CapabilityDiagnostic(
                level=DiagnosticLevel.WARNING,
                feature="image",
                message="Top-level 'image:' is defined but will be ignored (no container execution).",
            )
        )
    if "services" in raw_config:
        diags.append(
            CapabilityDiagnostic(
                level=DiagnosticLevel.WARNING,
                feature="services",
                message="Top-level 'services:' is defined but will be ignored (no container execution).",
            )
        )

    # workflow: rules — not emulated
    if "workflow" in raw_config:
        diags.append(
            CapabilityDiagnostic(
                level=DiagnosticLevel.WARNING,
                feature="workflow",
                message="'workflow:' is defined but has no effect locally (no pipeline source context).",
            )
        )

    # ------------------------------------------------------------------
    # 2. Per-job checks
    # ------------------------------------------------------------------

    for job_name, job_data in _iter_jobs(raw_config):
        # trigger: — cannot run child/multi-project pipelines locally
        if "trigger" in job_data:
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.ERROR,
                    feature="trigger",
                    message=f"Job '{job_name}': 'trigger:' jobs cannot be executed locally.",
                )
            )

        # inputs: at job level
        if "inputs" in job_data:
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.ERROR,
                    feature="inputs",
                    message=f"Job '{job_name}': job-level 'inputs:' is not supported locally.",
                )
            )

        # image / services — warn (ignored)
        if "image" in job_data:
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.WARNING,
                    feature="image",
                    message=f"Job '{job_name}': 'image:' will be ignored (no container execution).",
                )
            )
        if "services" in job_data:
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.WARNING,
                    feature="services",
                    message=f"Job '{job_name}': 'services:' will be ignored (no container execution).",
                )
            )

        # resource_group — no mutual exclusion enforced locally
        if "resource_group" in job_data:
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.WARNING,
                    feature="resource_group",
                    message=(f"Job '{job_name}': 'resource_group:' is defined but mutual exclusion is not enforced locally."),
                )
            )

        # environment — no deployment tracking locally
        if "environment" in job_data:
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.WARNING,
                    feature="environment",
                    message=f"Job '{job_name}': 'environment:' is defined but deployment tracking is not available locally.",
                )
            )

        # rules: — check for unsupported sub-keys
        rules = job_data.get("rules")
        if isinstance(rules, list):
            for i, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    continue
                unsupported = _UNSUPPORTED_RULES_KEYS & rule.keys()
                if unsupported:
                    diags.append(
                        CapabilityDiagnostic(
                            level=DiagnosticLevel.WARNING,
                            feature="rules:changes",
                            message=(f"Job '{job_name}', rule {i + 1}: 'changes:' conditions are not yet evaluated locally and will be skipped."),
                        )
                    )

        # pages — no GitLab Pages deployment locally
        if job_name == "pages":
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.WARNING,
                    feature="pages",
                    message="The 'pages' job will run its script locally but no GitLab Pages deployment will occur.",
                )
            )

        # release — no release creation locally
        if "release" in job_data:
            diags.append(
                CapabilityDiagnostic(
                    level=DiagnosticLevel.WARNING,
                    feature="release",
                    message=f"Job '{job_name}': 'release:' block will be ignored (no GitLab release API locally).",
                )
            )

    return diags

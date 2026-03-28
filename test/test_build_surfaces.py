from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
JUSTFILE = ROOT / "Justfile"

CORE_TARGETS = {
    "help",
    "fix",
    "fix-ci",
    "verify",
    "fast-verify",
    "triage",
    "repro",
    "bugs",
    "check",
    "check-human",
    "check-ci",
    "check-llm",
    "full-verify",
    "ruff",
    "mypy",
    "pylint",
    "bandit",
    "smoke",
    "test",
}


def _make_targets(path: Path) -> set[str]:
    targets: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(("\t", " ", "#", ".")):
            continue
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name and not any(ch.isspace() for ch in name):
            targets.add(name)
    return targets


def _just_targets(path: Path) -> set[str]:
    targets: set[str] = set()
    pattern = re.compile(r"^([A-Za-z0-9_-]+):")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            targets.add(match.group(1))
    return targets


def test_makefile_and_justfile_share_core_build_targets() -> None:
    make_targets = _make_targets(MAKEFILE)
    just_targets = _just_targets(JUSTFILE)

    assert CORE_TARGETS.issubset(make_targets)
    assert CORE_TARGETS.issubset(just_targets)


def test_makefile_check_no_longer_pulls_networked_schema_refresh() -> None:
    makefile_text = MAKEFILE.read_text(encoding="utf-8")

    assert "check: check-human" in makefile_text
    assert "check: mypy test pylint bandit pre-commit update-schema" not in makefile_text


def test_makefile_and_justfile_use_read_only_markdown_checks() -> None:
    makefile_text = MAKEFILE.read_text(encoding="utf-8")
    justfile_text = JUSTFILE.read_text(encoding="utf-8")

    assert "mdformat --check" in makefile_text
    assert "mdformat --check" in justfile_text


def test_makefile_exposes_job_listing_target() -> None:
    make_targets = _make_targets(MAKEFILE)

    assert "list-jobs" in make_targets

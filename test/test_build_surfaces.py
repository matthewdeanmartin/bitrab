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
    "quality-gate",
    "quality-gate-serial",
}

BITRAB_CI = ROOT / ".bitrab-ci.yml"


def make_targets(path: Path) -> set[str]:
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


def just_targets(path: Path) -> set[str]:
    targets: set[str] = set()
    pattern = re.compile(r"^([A-Za-z0-9_-]+):")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            targets.add(match.group(1))
    return targets


def test_makefile_and_justfile_share_core_build_targets() -> None:
    m_targets = make_targets(MAKEFILE)
    j_targets = just_targets(JUSTFILE)

    assert CORE_TARGETS.issubset(m_targets)
    assert CORE_TARGETS.issubset(j_targets)


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
    targets = make_targets(MAKEFILE)

    assert "list-jobs" in targets


def test_human_targets_do_not_force_no_color() -> None:
    makefile_text = MAKEFILE.read_text(encoding="utf-8")
    justfile_text = JUSTFILE.read_text(encoding="utf-8")

    make_pytest_only = makefile_text.split(".PHONY: pytest-only", 1)[1].split(".PHONY: pytest", 1)[0]
    just_pytest_only = justfile_text.split("pytest-only:", 1)[1].split("pytest:", 1)[0]

    assert "$(NO_COLOR_ENV) $(VENV) pytest test -vv" not in make_pytest_only
    assert "--color=no" not in make_pytest_only
    assert "{{NO_COLOR_ENV}} {{venv}} pytest test -vv" not in just_pytest_only
    assert "--color=no" not in just_pytest_only


def test_makefile_and_justfile_expose_shared_bitrab_quality_gate() -> None:
    makefile_text = MAKEFILE.read_text(encoding="utf-8")
    justfile_text = JUSTFILE.read_text(encoding="utf-8")

    assert "bitrab -c $(BITRAB_CONFIG) validate" in makefile_text
    assert "bitrab -c $(BITRAB_CONFIG) run --no-tui --parallel $(QUALITY_GATE_PARALLEL)" in makefile_text
    assert "--no-worktrees" in makefile_text
    assert "bitrab -c {{BITRAB_CONFIG}} validate" in justfile_text
    assert "bitrab -c {{BITRAB_CONFIG}} run --no-tui --parallel {{QUALITY_GATE_PARALLEL}}" in justfile_text
    assert "--no-worktrees" in justfile_text

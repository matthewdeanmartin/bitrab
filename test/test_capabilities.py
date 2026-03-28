"""Tests for ARCH-3: Capability validation layer."""

from __future__ import annotations

from bitrab.config.capabilities import CapabilityDiagnostic, DiagnosticLevel, check_capabilities

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _errors(diags):
    return [d for d in diags if d.level == DiagnosticLevel.ERROR]


def _warnings(diags):
    return [d for d in diags if d.level == DiagnosticLevel.WARNING]


def _features(diags):
    return {d.feature for d in diags}


# ---------------------------------------------------------------------------
# Clean config produces no diagnostics
# ---------------------------------------------------------------------------


def test_clean_config_no_diagnostics():
    raw = {
        "stages": ["build", "test"],
        "build_job": {"stage": "build", "script": ["make"]},
        "test_job": {"stage": "test", "script": ["pytest"]},
    }
    assert check_capabilities(raw) == []


# ---------------------------------------------------------------------------
# include: component  → ERROR
# ---------------------------------------------------------------------------


def test_include_component_is_error():
    raw = {
        "include": [{"component": "gitlab.com/my/component@1.0"}],
        "job": {"script": ["echo hi"]},
    }
    diags = check_capabilities(raw)
    errors = _errors(diags)
    assert len(errors) == 1
    assert errors[0].feature == "include:component"


def test_include_local_no_diagnostic():
    raw = {
        "include": [{"local": "/ci/shared.yml"}],
        "job": {"script": ["echo hi"]},
    }
    diags = check_capabilities(raw)
    assert not _errors(diags)
    assert "include:component" not in _features(diags)


def test_include_remote_is_warning():
    raw = {
        "include": [{"remote": "https://example.com/ci.yml"}],
        "job": {"script": ["echo hi"]},
    }
    diags = check_capabilities(raw)
    assert not _errors(diags)
    warns = _warnings(diags)
    assert any(d.feature == "include:remote/template" for d in warns)


def test_include_project_is_warning():
    raw = {
        "include": [{"project": "mygroup/myrepo", "file": "/ci.yml"}],
        "job": {"script": ["echo hi"]},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "include:project" for d in _warnings(diags))


# ---------------------------------------------------------------------------
# inputs: → ERROR
# ---------------------------------------------------------------------------


def test_top_level_inputs_is_error():
    raw = {
        "inputs": {"env": {"default": "staging"}},
        "job": {"script": ["echo hi"]},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "inputs" and d.level == DiagnosticLevel.ERROR for d in diags)


def test_job_level_inputs_is_error():
    raw = {
        "job": {"script": ["echo hi"], "inputs": {"token": {}}},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "inputs" and d.level == DiagnosticLevel.ERROR for d in diags)


# ---------------------------------------------------------------------------
# image / services → WARNING
# ---------------------------------------------------------------------------


def test_top_level_image_is_warning():
    raw = {
        "image": "python:3.11",
        "job": {"script": ["python -m pytest"]},
    }
    diags = check_capabilities(raw)
    warns = _warnings(diags)
    assert any(d.feature == "image" for d in warns)
    assert not _errors(diags)


def test_top_level_services_is_warning():
    raw = {
        "services": ["postgres:14"],
        "job": {"script": ["psql -c 'SELECT 1'"]},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "services" for d in _warnings(diags))


def test_job_level_image_is_warning():
    raw = {
        "job": {"image": "node:18", "script": ["npm test"]},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "image" for d in _warnings(diags))
    assert not _errors(diags)


def test_job_level_services_is_warning():
    raw = {
        "job": {"services": ["redis:7"], "script": ["redis-cli ping"]},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "services" for d in _warnings(diags))


# ---------------------------------------------------------------------------
# trigger: → ERROR
# ---------------------------------------------------------------------------


def test_trigger_job_is_error():
    raw = {
        "deploy_child": {"trigger": {"project": "mygroup/child", "branch": "main"}},
    }
    diags = check_capabilities(raw)
    errors = _errors(diags)
    assert any(d.feature == "trigger" for d in errors)


# ---------------------------------------------------------------------------
# resource_group → WARNING
# ---------------------------------------------------------------------------


def test_resource_group_is_warning():
    raw = {
        "deploy": {"script": ["./deploy.sh"], "resource_group": "production"},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "resource_group" for d in _warnings(diags))
    assert not _errors(diags)


# ---------------------------------------------------------------------------
# environment → WARNING
# ---------------------------------------------------------------------------


def test_environment_is_warning():
    raw = {
        "deploy": {"script": ["./deploy.sh"], "environment": {"name": "production"}},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "environment" for d in _warnings(diags))
    assert not _errors(diags)


# ---------------------------------------------------------------------------
# workflow → WARNING
# ---------------------------------------------------------------------------


def test_workflow_is_warning():
    raw = {
        "workflow": {"rules": [{"if": '$CI_COMMIT_BRANCH == "main"'}]},
        "job": {"script": ["echo hi"]},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "workflow" for d in _warnings(diags))
    assert not _errors(diags)


# ---------------------------------------------------------------------------
# rules:changes → WARNING
# ---------------------------------------------------------------------------


def test_rules_changes_is_warning():
    raw = {
        "test": {
            "script": ["pytest"],
            "rules": [{"changes": ["src/**/*.py"], "when": "always"}],
        }
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "rules:changes" for d in _warnings(diags))
    assert not _errors(diags)


def test_rules_without_changes_no_warning():
    raw = {
        "test": {
            "script": ["pytest"],
            "rules": [{"if": '$CI_COMMIT_BRANCH == "main"', "when": "always"}],
        }
    }
    diags = check_capabilities(raw)
    assert "rules:changes" not in _features(diags)


# ---------------------------------------------------------------------------
# pages job → WARNING
# ---------------------------------------------------------------------------


def test_pages_job_is_warning():
    raw = {
        "pages": {"script": ["mkdocs build"], "artifacts": {"paths": ["public"]}},
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "pages" for d in _warnings(diags))
    assert not _errors(diags)


# ---------------------------------------------------------------------------
# release block → WARNING
# ---------------------------------------------------------------------------


def test_release_block_is_warning():
    raw = {
        "create_release": {
            "script": ["echo done"],
            "release": {"tag_name": "v1.0", "description": "Release notes"},
        }
    }
    diags = check_capabilities(raw)
    assert any(d.feature == "release" for d in _warnings(diags))
    assert not _errors(diags)


# ---------------------------------------------------------------------------
# Multiple issues in one config
# ---------------------------------------------------------------------------


def test_multiple_issues_all_reported():
    raw = {
        "include": [{"component": "gitlab.com/comp@1"}],
        "workflow": {"rules": []},
        "deploy": {
            "trigger": {"project": "grp/proj"},
            "image": "alpine",
        },
    }
    diags = check_capabilities(raw)
    features = _features(diags)
    assert "include:component" in features
    assert "workflow" in features
    assert "trigger" in features
    assert "image" in features
    # Must have at least one error (component + trigger)
    assert len(_errors(diags)) >= 2


# ---------------------------------------------------------------------------
# CapabilityDiagnostic __str__
# ---------------------------------------------------------------------------


def test_error_str_has_icon():
    d = CapabilityDiagnostic(DiagnosticLevel.ERROR, "trigger", "Cannot run locally.")
    assert "❌" in str(d)
    assert "trigger" in str(d)


def test_warning_str_has_icon():
    d = CapabilityDiagnostic(DiagnosticLevel.WARNING, "image", "Will be ignored.")
    assert "⚠️" in str(d)
    assert "image" in str(d)

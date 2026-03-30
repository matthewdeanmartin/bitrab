"""Tests for D5: extends: keyword (job inheritance)."""

from __future__ import annotations

import pytest

from bitrab.exceptions import GitlabRunnerError
from bitrab.plan import PipelineProcessor


def _proc(raw: dict) -> object:
    return PipelineProcessor().process_config(raw)


def _job(pipeline, name: str):
    return next(j for j in pipeline.jobs if j.name == name)


class TestExtendsBasic:
    def test_single_extends_inherits_script(self):
        raw = {
            "stages": ["test"],
            ".base": {"script": ["echo base"]},
            "child": {"extends": ".base", "stage": "test"},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.script == ["echo base"]

    def test_single_extends_child_overrides_stage(self):
        raw = {
            "stages": ["build", "test"],
            ".base": {"script": ["echo"], "stage": "test"},
            "child": {"extends": ".base", "stage": "build"},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.stage == "build"

    def test_single_extends_child_overrides_variables(self):
        raw = {
            "stages": ["test"],
            ".base": {"script": ["echo"], "variables": {"FOO": "base", "BAR": "base"}},
            "child": {"extends": ".base", "variables": {"FOO": "child"}},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.variables["FOO"] == "child"
        assert j.variables["BAR"] == "base"

    def test_extends_does_not_appear_in_job_config(self):
        raw = {
            "stages": ["test"],
            ".base": {"script": ["echo"]},
            "child": {"extends": ".base"},
        }
        p = _proc(raw)
        j = _job(p, "child")
        # JobConfig has no 'extends' attribute — just verifying no crash
        assert not hasattr(j, "extends")

    def test_hidden_template_not_emitted_as_job(self):
        raw = {
            "stages": ["test"],
            ".base": {"script": ["echo base"]},
            "real_job": {"extends": ".base", "stage": "test"},
        }
        p = _proc(raw)
        names = [j.name for j in p.jobs]
        assert "real_job" in names
        assert ".base" not in names
        assert len(p.jobs) == 1


class TestExtendsMultipleParents:
    def test_multiple_extends_later_parent_wins(self):
        raw = {
            "stages": ["test"],
            ".first": {"script": ["echo first"], "variables": {"X": "first"}},
            ".second": {"variables": {"X": "second", "Y": "second"}},
            "child": {"extends": [".first", ".second"]},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.variables["X"] == "second"
        assert j.variables["Y"] == "second"
        assert j.script == ["echo first"]

    def test_multiple_extends_child_wins_over_all(self):
        raw = {
            "stages": ["test"],
            ".a": {"script": ["echo a"], "variables": {"V": "a"}},
            ".b": {"variables": {"V": "b"}},
            "child": {"extends": [".a", ".b"], "variables": {"V": "child"}, "script": ["echo child"]},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.script == ["echo child"]
        assert j.variables["V"] == "child"


class TestExtendsDeepMerge:
    def test_nested_dict_merged_not_replaced(self):
        raw = {
            "stages": ["test"],
            ".base": {"variables": {"A": "1", "B": "2"}, "script": ["echo"]},
            "child": {"extends": ".base", "variables": {"C": "3"}},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.variables["A"] == "1"
        assert j.variables["B"] == "2"
        assert j.variables["C"] == "3"

    def test_list_fully_replaced_not_appended(self):
        raw = {
            "stages": ["test"],
            ".base": {"script": ["echo base1", "echo base2"]},
            "child": {"extends": ".base", "script": ["echo child"]},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.script == ["echo child"]


class TestExtendsChaining:
    def test_three_level_chain_resolves_correctly(self):
        raw = {
            "stages": ["test"],
            ".grandparent": {"script": ["echo gp"], "variables": {"A": "gp"}},
            ".parent": {"extends": ".grandparent", "variables": {"B": "parent"}},
            "child": {"extends": ".parent", "variables": {"C": "child"}},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.script == ["echo gp"]
        assert j.variables["A"] == "gp"
        assert j.variables["B"] == "parent"
        assert j.variables["C"] == "child"

    def test_chain_with_override_at_each_level(self):
        raw = {
            "stages": ["test"],
            ".gp": {"script": ["echo gp"], "variables": {"X": "gp", "Y": "gp"}},
            ".p": {"extends": ".gp", "variables": {"X": "p"}},
            "child": {"extends": ".p", "variables": {"Y": "child"}},
        }
        p = _proc(raw)
        j = _job(p, "child")
        assert j.variables["X"] == "p"
        assert j.variables["Y"] == "child"


class TestExtendsErrors:
    def test_circular_reference_raises_error(self):
        raw = {
            "stages": ["test"],
            "job_a": {"extends": "job_b", "script": ["echo a"]},
            "job_b": {"extends": "job_a", "script": ["echo b"]},
        }
        with pytest.raises(GitlabRunnerError, match="circular reference"):
            _proc(raw)

    def test_unknown_base_raises_error(self):
        raw = {
            "stages": ["test"],
            "child": {"extends": ".nonexistent", "script": ["echo"]},
        }
        with pytest.raises(GitlabRunnerError, match="unknown job or template"):
            _proc(raw)

    def test_self_reference_raises_error(self):
        raw = {
            "stages": ["test"],
            "job": {"extends": "job", "script": ["echo"]},
        }
        with pytest.raises(GitlabRunnerError, match="circular reference"):
            _proc(raw)


class TestExtendsEndToEnd:
    def test_extends_without_script_in_child(self):
        """Child has no script — inherits from base."""
        raw = {
            "stages": ["test"],
            ".base": {"script": ["inherited"], "stage": "test"},
            "child": {"extends": ".base"},
        }
        p = _proc(raw)
        assert len(p.jobs) == 1
        j = _job(p, "child")
        assert j.script == ["inherited"]

    def test_real_job_and_hidden_job_with_same_prefix(self):
        """A hidden .job and a real job.name can coexist."""
        raw = {
            "stages": ["test"],
            ".template": {"script": ["echo tpl"]},
            "real": {"extends": ".template"},
            "also_real": {"script": ["echo real"]},
        }
        p = _proc(raw)
        names = {j.name for j in p.jobs}
        assert names == {"real", "also_real"}
        assert ".template" not in names

"""Tests for parallel: N, parallel: matrix:, and parallel backend config."""

from __future__ import annotations

from bitrab.models.pipeline import PipelineConfig
from pathlib import Path

from bitrab.mutation import ParallelBackendConfig, load_parallel_config, load_worktree_config
from bitrab.plan import PipelineProcessor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_config(jobs: dict, stages: list[str] | None = None) -> dict:
    """Build a minimal raw config dict from job definitions."""
    cfg: dict = {}
    if stages:
        cfg["stages"] = stages
    cfg.update(jobs)
    return cfg


def _process(raw: dict) -> PipelineConfig:
    return PipelineProcessor().process_config(raw)


# ---------------------------------------------------------------------------
# parallel: N
# ---------------------------------------------------------------------------


class TestParallelN:
    def test_expands_to_n_jobs(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo hi"],
                    "parallel": 3,
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 3

    def test_job_names(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo hi"],
                    "parallel": 3,
                }
            }
        )
        pipeline = _process(raw)
        names = [j.name for j in pipeline.jobs]
        assert names == ["test 1/3", "test 2/3", "test 3/3"]

    def test_ci_node_variables(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo hi"],
                    "parallel": 2,
                }
            }
        )
        pipeline = _process(raw)
        j1, j2 = pipeline.jobs
        assert j1.variables["CI_NODE_INDEX"] == "1"
        assert j1.variables["CI_NODE_TOTAL"] == "2"
        assert j2.variables["CI_NODE_INDEX"] == "2"
        assert j2.variables["CI_NODE_TOTAL"] == "2"

    def test_parallel_fields_set(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo hi"],
                    "parallel": 4,
                }
            }
        )
        pipeline = _process(raw)
        for i, job in enumerate(pipeline.jobs, 1):
            assert job.parallel_total == 4
            assert job.parallel_index == i

    def test_parallel_1_creates_one_job(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo hi"],
                    "parallel": 1,
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 1
        assert pipeline.jobs[0].name == "test 1/1"

    def test_clamped_to_200(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo hi"],
                    "parallel": 999,
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 200

    def test_no_parallel_keyword_unchanged(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo hi"],
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 1
        assert pipeline.jobs[0].name == "test"
        assert pipeline.jobs[0].parallel_total == 0

    def test_preserves_stage(self):
        raw = _make_raw_config(
            {"build": {"stage": "build", "script": ["make"], "parallel": 2}},
            stages=["build"],
        )
        pipeline = _process(raw)
        assert all(j.stage == "build" for j in pipeline.jobs)

    def test_preserves_other_fields(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["pytest"],
                    "parallel": 2,
                    "allow_failure": True,
                    "timeout": 60,
                }
            }
        )
        pipeline = _process(raw)
        for job in pipeline.jobs:
            assert job.allow_failure is True
            assert job.timeout == 60.0
            assert job.script == ["pytest"]


# ---------------------------------------------------------------------------
# parallel: matrix:
# ---------------------------------------------------------------------------


class TestParallelMatrix:
    def test_single_var_list(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo $DB"],
                    "parallel": {"matrix": [{"DB": ["postgres", "mysql", "sqlite"]}]},
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 3
        db_vals = [j.variables["DB"] for j in pipeline.jobs]
        assert sorted(db_vals) == ["mysql", "postgres", "sqlite"]

    def test_cartesian_product(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo $A $B"],
                    "parallel": {"matrix": [{"A": ["1", "2"], "B": ["x", "y"]}]},
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 4  # 2 * 2
        combos = [(j.variables["A"], j.variables["B"]) for j in pipeline.jobs]
        assert sorted(combos) == [("1", "x"), ("1", "y"), ("2", "x"), ("2", "y")]

    def test_multiple_matrix_entries(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "parallel": {
                        "matrix": [
                            {"DB": ["pg", "mysql"]},
                            {"RUNTIME": ["node", "deno"]},
                        ]
                    },
                }
            }
        )
        pipeline = _process(raw)
        # 2 from first entry + 2 from second entry = 4
        assert len(pipeline.jobs) == 4

    def test_job_naming(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "parallel": {"matrix": [{"DB": ["pg", "mysql"]}]},
                }
            }
        )
        pipeline = _process(raw)
        names = sorted(j.name for j in pipeline.jobs)
        assert names == ["test: [DB=mysql]", "test: [DB=pg]"]

    def test_multi_var_naming(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "parallel": {"matrix": [{"A": ["1"], "Z": ["9"]}]},
                }
            }
        )
        pipeline = _process(raw)
        # Keys should be sorted alphabetically in the name
        assert pipeline.jobs[0].name == "test: [A=1, Z=9]"

    def test_ci_node_variables_set(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "parallel": {"matrix": [{"DB": ["pg", "mysql", "sqlite"]}]},
                }
            }
        )
        pipeline = _process(raw)
        for i, job in enumerate(pipeline.jobs, 1):
            assert job.variables["CI_NODE_INDEX"] == str(i)
            assert job.variables["CI_NODE_TOTAL"] == "3"

    def test_scalar_values(self):
        """Scalar (non-list) values should be treated as single-element lists."""
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "parallel": {"matrix": [{"DB": "postgres"}]},
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 1
        assert pipeline.jobs[0].variables["DB"] == "postgres"

    def test_numeric_values(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "parallel": {"matrix": [{"VERSION": [14, 15, 16]}]},
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 3
        versions = [j.variables["VERSION"] for j in pipeline.jobs]
        assert sorted(versions) == ["14", "15", "16"]

    def test_matrix_variables_merge_with_job_vars(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "variables": {"COMMON": "shared"},
                    "parallel": {"matrix": [{"DB": ["pg", "mysql"]}]},
                }
            }
        )
        pipeline = _process(raw)
        for job in pipeline.jobs:
            assert job.variables["COMMON"] == "shared"
            assert "DB" in job.variables

    def test_empty_matrix_no_expansion(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["echo"],
                    "parallel": {"matrix": []},
                }
            }
        )
        pipeline = _process(raw)
        assert len(pipeline.jobs) == 1
        assert pipeline.jobs[0].name == "test"


# ---------------------------------------------------------------------------
# needs resolution for expanded jobs
# ---------------------------------------------------------------------------


class TestNeedsResolution:
    def test_needs_expanded_to_all_instances(self):
        raw = _make_raw_config(
            {
                "build": {
                    "stage": "build",
                    "script": ["make"],
                    "parallel": 2,
                },
                "deploy": {
                    "stage": "deploy",
                    "script": ["deploy"],
                    "needs": ["build"],
                },
            },
            stages=["build", "deploy"],
        )
        pipeline = _process(raw)
        deploy = [j for j in pipeline.jobs if j.name == "deploy"][0]
        assert sorted(deploy.needs) == ["build 1/2", "build 2/2"]

    def test_needs_matrix_expanded(self):
        raw = _make_raw_config(
            {
                "test": {
                    "stage": "test",
                    "script": ["pytest"],
                    "parallel": {"matrix": [{"DB": ["pg", "mysql"]}]},
                },
                "report": {
                    "stage": "deploy",
                    "script": ["report"],
                    "needs": ["test"],
                },
            },
            stages=["test", "deploy"],
        )
        pipeline = _process(raw)
        report = [j for j in pipeline.jobs if j.name == "report"][0]
        assert len(report.needs) == 2
        assert all("test:" in n for n in report.needs)

    def test_needs_direct_reference_unchanged(self):
        """If a needs reference matches an existing job name exactly, keep it."""
        raw = _make_raw_config(
            {
                "build": {
                    "stage": "build",
                    "script": ["make"],
                },
                "test": {
                    "stage": "test",
                    "script": ["test"],
                    "needs": ["build"],
                },
            },
            stages=["build", "test"],
        )
        pipeline = _process(raw)
        test = [j for j in pipeline.jobs if j.name == "test"][0]
        assert test.needs == ["build"]

    def test_dependencies_expanded(self):
        raw = _make_raw_config(
            {
                "build": {
                    "stage": "build",
                    "script": ["make"],
                    "parallel": 2,
                    "artifacts": {"paths": ["dist/"]},
                },
                "deploy": {
                    "stage": "deploy",
                    "script": ["deploy"],
                    "dependencies": ["build"],
                },
            },
            stages=["build", "deploy"],
        )
        pipeline = _process(raw)
        deploy = [j for j in pipeline.jobs if j.name == "deploy"][0]
        assert sorted(deploy.dependencies) == ["build 1/2", "build 2/2"]


# ---------------------------------------------------------------------------
# Mixed: jobs with and without parallel
# ---------------------------------------------------------------------------


class TestMixedJobs:
    def test_mixed_expanded_and_normal(self):
        raw = _make_raw_config(
            {
                "lint": {"stage": "test", "script": ["lint"]},
                "test": {
                    "stage": "test",
                    "script": ["pytest"],
                    "parallel": 3,
                },
            },
            stages=["test"],
        )
        pipeline = _process(raw)
        names = sorted(j.name for j in pipeline.jobs)
        assert "lint" in names
        assert "test 1/3" in names
        assert "test 2/3" in names
        assert "test 3/3" in names
        assert len(pipeline.jobs) == 4


# ---------------------------------------------------------------------------
# ParallelBackendConfig
# ---------------------------------------------------------------------------


class TestParallelBackendConfig:
    def test_default_is_process(self):
        cfg = ParallelBackendConfig()
        assert cfg.backend == "process"

    def test_thread_backend(self):
        cfg = ParallelBackendConfig(backend="thread")
        assert cfg.backend == "thread"

    def test_invalid_falls_back_to_process(self):
        cfg = ParallelBackendConfig(backend="invalid")
        assert cfg.backend == "process"


class TestLoadParallelConfig:
    def test_missing_pyproject(self, tmp_path):
        cfg = load_parallel_config(tmp_path)
        assert cfg.backend == "process"

    def test_default_when_absent(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        cfg = load_parallel_config(tmp_path)
        assert cfg.backend == "process"

    def test_process_backend(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.bitrab]\nparallel_backend = "process"\n')
        cfg = load_parallel_config(tmp_path)
        assert cfg.backend == "process"

    def test_thread_backend(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.bitrab]\nparallel_backend = "thread"\n')
        cfg = load_parallel_config(tmp_path)
        assert cfg.backend == "thread"

    def test_invalid_toml_returns_default(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("not : valid : toml !!!")
        cfg = load_parallel_config(tmp_path)
        assert cfg.backend == "process"

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.bitrab]\nparallel_backend = "THREAD"\n')
        cfg = load_parallel_config(tmp_path)
        assert cfg.backend == "thread"


class TestLoadWorktreeConfig:
    def test_default_root_is_none(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        cfg = load_worktree_config(tmp_path)
        assert cfg.enabled
        assert cfg.root is None

    def test_relative_root_resolves_from_project_dir(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.bitrab]\nworktree_root = ".cache/worktrees"\n')
        cfg = load_worktree_config(tmp_path)
        assert cfg.root == tmp_path / ".cache" / "worktrees"

    def test_home_root_expands(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        (tmp_path / "pyproject.toml").write_text('[tool.bitrab]\nworktree_root = "~/.bitrab/worktrees"\n')
        cfg = load_worktree_config(tmp_path)
        assert cfg.root == Path(home) / ".bitrab" / "worktrees"

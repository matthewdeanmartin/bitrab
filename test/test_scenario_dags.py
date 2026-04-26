"""Tests for the DAG shapes described in spec/scenarios.md.

Each scenario is modelled as a minimal .gitlab-ci.yml that captures the
topological structure from the spec.  Tests verify:

  - The DAG is parsed correctly (needs:, stages, job counts)
  - The pipeline executes without raising (or raises when expected)
  - Output files / artifacts confirm execution order was respected
  - Key `when` / `allow_failure` conditions behave correctly

No external tooling is installed — jobs use echo / exit / mkdir.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bitrab.config.loader import ConfigurationLoader
from bitrab.exceptions import JobExecutionError
from bitrab.execution.artifacts import artifact_dir
from bitrab.execution.stage_runner import has_dag_jobs
from bitrab.plan import LocalGitLabRunner, PipelineProcessor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text(textwrap.dedent(content))
    return p


def runner(tmp_path: Path) -> LocalGitLabRunner:
    return LocalGitLabRunner(base_path=tmp_path)


def pipeline(tmp_path: Path):
    loader = ConfigurationLoader(base_path=tmp_path)
    raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
    return PipelineProcessor().process_config(raw)


def job_names(pipeline) -> set[str]:
    return {j.name for j in pipeline.jobs}


def needs(pipeline, job_name: str) -> list[str]:
    return next(j.needs for j in pipeline.jobs if j.name == job_name)


# ===========================================================================
# Scenario 1 — Spec → Code → Tests → Docs (Closed Loop Generation)
# DAG: spec → codegen → testgen → run_tests → docgen → publish
# ===========================================================================


class TestScenario1ClosedLoopGeneration:
    CI = """\
        stages:
          - spec
          - codegen
          - testgen
          - run_tests
          - docgen
          - publish

        spec:
          stage: spec
          script:
            - echo "spec" > spec.json
          artifacts:
            paths: [spec.json]
            when: on_success

        codegen:
          stage: codegen
          needs: [spec]
          script:
            - echo "generated code" > generated.py
          artifacts:
            paths: [generated.py]
            when: on_success

        testgen:
          stage: testgen
          needs: [codegen]
          script:
            - echo "generated tests" > test_generated.py

        run_tests:
          stage: run_tests
          needs: [testgen]
          script:
            - echo "all tests pass" > test_results.txt

        docgen:
          stage: docgen
          needs: [run_tests]
          script:
            - echo "docs" > docs.md

        publish:
          stage: publish
          needs: [docgen]
          script:
            - echo "published" > published.txt
        """

    def test_pipeline_is_a_dag(self, tmp_path):
        write(tmp_path, self.CI)
        assert has_dag_jobs(pipeline(tmp_path))

    def test_linear_chain_jobs_parsed(self, tmp_path):
        write(tmp_path, self.CI)
        names = job_names(pipeline(tmp_path))
        assert names == {"spec", "codegen", "testgen", "run_tests", "docgen", "publish"}

    def test_needs_chain(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "codegen") == ["spec"]
        assert needs(p, "testgen") == ["codegen"]
        assert needs(p, "run_tests") == ["testgen"]
        assert needs(p, "docgen") == ["run_tests"]
        assert needs(p, "publish") == ["docgen"]

    def test_full_pipeline_executes(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "spec.json").exists()
        assert (tmp_path / "generated.py").exists()
        assert (tmp_path / "test_results.txt").exists()
        assert (tmp_path / "published.txt").exists()

    def test_testgen_failure_blocks_publish(self, tmp_path):
        ci = """\
            stages: [spec, codegen, testgen, publish]
            spec:
              stage: spec
              script: [echo spec]
            codegen:
              stage: codegen
              needs: [spec]
              script: [echo code]
            testgen:
              stage: testgen
              needs: [codegen]
              script: [exit 1]
            publish:
              stage: publish
              needs: [testgen]
              script:
                - echo "should not run" > published.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "published.txt").exists()

    def test_spec_artifact_collected(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        dest = artifact_dir(tmp_path, "spec")
        assert (dest / "spec.json").exists()

    def test_codegen_artifact_collected(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        dest = artifact_dir(tmp_path, "codegen")
        assert (dest / "generated.py").exists()


# ===========================================================================
# Scenario 2 — Historical Regression Matrix (Time DAG)
# DAG: current_code → [test_v1, test_v2, test_v3] → detect_break → changelog
# ===========================================================================


class TestScenario2RegressionMatrix:
    CI = """\
        stages:
          - prepare
          - compat_test
          - analysis
          - report

        current_code:
          stage: prepare
          script:
            - echo "HEAD" > version.txt

        test_v1:
          stage: compat_test
          needs: [current_code]
          script:
            - echo "compat v1 ok" > compat_v1.txt

        test_v2:
          stage: compat_test
          needs: [current_code]
          script:
            - echo "compat v2 ok" > compat_v2.txt

        test_v3:
          stage: compat_test
          needs: [current_code]
          script:
            - echo "compat v3 ok" > compat_v3.txt

        detect_break:
          stage: analysis
          needs: [test_v1, test_v2, test_v3]
          script:
            - echo "no breaks detected" > break_report.txt

        changelog:
          stage: report
          needs: [detect_break]
          script:
            - echo "changelog generated" > CHANGELOG.md
        """

    def test_parallel_compat_jobs_parsed(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "test_v1") == ["current_code"]
        assert needs(p, "test_v2") == ["current_code"]
        assert needs(p, "test_v3") == ["current_code"]

    def test_detect_break_waits_for_all_compat(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert set(needs(p, "detect_break")) == {"test_v1", "test_v2", "test_v3"}

    def test_fan_out_fan_in_executes(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        for v in ["v1", "v2", "v3"]:
            assert (tmp_path / f"compat_{v}.txt").exists()
        assert (tmp_path / "break_report.txt").exists()
        assert (tmp_path / "CHANGELOG.md").exists()

    def test_single_compat_failure_blocks_detect_break(self, tmp_path):
        ci = """\
            stages: [prepare, compat_test, analysis]
            current_code:
              stage: prepare
              script: [echo HEAD]
            test_v1:
              stage: compat_test
              needs: [current_code]
              script: [exit 1]
            test_v2:
              stage: compat_test
              needs: [current_code]
              script:
                - echo ok > compat_v2.txt
            detect_break:
              stage: analysis
              needs: [test_v1, test_v2]
              script:
                - echo "should not run" > break_report.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)

    def test_allow_failure_on_one_version_continues(self, tmp_path):
        ci = """\
            stages: [prepare, compat_test, analysis]
            current_code:
              stage: prepare
              script: [echo HEAD]
            test_v1:
              stage: compat_test
              needs: [current_code]
              allow_failure: true
              script: [exit 1]
            test_v2:
              stage: compat_test
              needs: [current_code]
              script:
                - echo ok > compat_v2.txt
            detect_break:
              stage: analysis
              needs: [test_v2]
              when: always
              script:
                - echo "partial results" > break_report.txt
            """
        write(tmp_path, ci)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "break_report.txt").exists()


# ===========================================================================
# Scenario 3 — External World Sampling Pipeline
# DAG: fetch_external_data → normalize → analyze → publish_report
# ===========================================================================


class TestScenario3ExternalSampling:
    CI = """\
        stages:
          - fetch
          - normalize
          - analyze
          - publish

        fetch_external_data:
          stage: fetch
          script:
            - echo "value=42" > raw_data.json
          artifacts:
            paths: [raw_data.json]
            when: on_success

        normalize:
          stage: normalize
          needs: [fetch_external_data]
          script:
            - echo "value=42 normalized=true" > normalized.json
          artifacts:
            paths: [normalized.json]
            when: on_success

        analyze:
          stage: analyze
          needs: [normalize]
          script:
            - echo "mean=42" > analysis.txt
          artifacts:
            paths: [analysis.txt]
            when: on_success

        publish_report:
          stage: publish
          needs: [analyze]
          script:
            - echo "# Report" > report.md
        """

    def test_linear_etl_dag(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "normalize") == ["fetch_external_data"]
        assert needs(p, "analyze") == ["normalize"]
        assert needs(p, "publish_report") == ["analyze"]

    def test_pipeline_executes_in_order(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "raw_data.json").exists()
        assert (tmp_path / "normalized.json").exists()
        assert (tmp_path / "analysis.txt").exists()
        assert (tmp_path / "report.md").exists()

    def test_artifacts_at_each_stage(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (artifact_dir(tmp_path, "fetch_external_data") / "raw_data.json").exists()
        assert (artifact_dir(tmp_path, "normalize") / "normalized.json").exists()
        assert (artifact_dir(tmp_path, "analyze") / "analysis.txt").exists()

    def test_fetch_failure_blocks_whole_pipeline(self, tmp_path):
        ci = """\
            stages: [fetch, publish]
            fetch_external_data:
              stage: fetch
              script: [exit 1]
            publish_report:
              stage: publish
              needs: [fetch_external_data]
              script:
                - echo done > report.md
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "report.md").exists()


# ===========================================================================
# Scenario 4 — Personal Quantification / "Life CI"
# DAG: ingest_strava + ingest_github + ingest_mastodon
#       → score_day → update_status_file → commit_publish
# Bonus: low_score → trigger_intervention; high_score → unlock_reward
# ===========================================================================


class TestScenario4LifeCI:
    CI = """\
        stages:
          - ingest
          - score
          - update
          - publish
          - react

        ingest_strava:
          stage: ingest
          script:
            - echo "km=10" > strava.txt

        ingest_github:
          stage: ingest
          script:
            - echo "commits=5" > github.txt

        ingest_mastodon:
          stage: ingest
          script:
            - echo "posts=3" > mastodon.txt

        score_day:
          stage: score
          needs: [ingest_strava, ingest_github, ingest_mastodon]
          script:
            - echo "score=85" > daily_score.txt

        update_status_file:
          stage: update
          needs: [score_day]
          script:
            - echo "status updated" > status.md

        commit_publish:
          stage: publish
          needs: [update_status_file]
          script:
            - echo "published" > site.html

        unlock_reward:
          stage: react
          needs: [score_day]
          when: always
          script:
            - echo "reward unlocked" > reward.txt
        """

    def test_three_ingest_sources_no_needs(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "ingest_strava") == []
        assert needs(p, "ingest_github") == []
        assert needs(p, "ingest_mastodon") == []

    def test_score_day_waits_for_all_ingests(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert set(needs(p, "score_day")) == {
            "ingest_strava",
            "ingest_github",
            "ingest_mastodon",
        }

    def test_full_life_ci_pipeline(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "strava.txt").exists()
        assert (tmp_path / "github.txt").exists()
        assert (tmp_path / "mastodon.txt").exists()
        assert (tmp_path / "daily_score.txt").exists()
        assert (tmp_path / "status.md").exists()
        assert (tmp_path / "site.html").exists()
        assert (tmp_path / "reward.txt").exists()

    def test_ingest_failure_blocks_scoring(self, tmp_path):
        ci = """\
            stages: [ingest, score]
            ingest_strava:
              stage: ingest
              script: [exit 1]
            ingest_github:
              stage: ingest
              script: [echo ok]
            score_day:
              stage: score
              needs: [ingest_strava, ingest_github]
              script:
                - echo "should not run" > score.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "score.txt").exists()


# ===========================================================================
# Scenario 5 — Monte Carlo / Simulation Pipeline
# DAG: seed → [simulate_1..N] → aggregate → decision
# ===========================================================================


class TestScenario5MonteCarlo:
    CI = """\
        stages:
          - seed
          - simulate
          - aggregate
          - decide

        seed:
          stage: seed
          script:
            - echo "rng_seed=42" > seed.txt
          artifacts:
            paths: [seed.txt]
            when: on_success

        simulate_1:
          stage: simulate
          needs: [seed]
          script:
            - echo "result=0.72" > sim_1.txt
          artifacts:
            paths: [sim_1.txt]
            when: on_success

        simulate_2:
          stage: simulate
          needs: [seed]
          script:
            - echo "result=0.68" > sim_2.txt
          artifacts:
            paths: [sim_2.txt]
            when: on_success

        simulate_3:
          stage: simulate
          needs: [seed]
          script:
            - echo "result=0.81" > sim_3.txt
          artifacts:
            paths: [sim_3.txt]
            when: on_success

        aggregate:
          stage: aggregate
          needs: [simulate_1, simulate_2, simulate_3]
          script:
            - echo "mean=0.737" > aggregated.txt
          artifacts:
            paths: [aggregated.txt]
            when: on_success

        decision:
          stage: decide
          needs: [aggregate]
          script:
            - echo "proceed=true" > decision.txt
        """

    def test_all_simulations_depend_on_seed(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        for sim in ["simulate_1", "simulate_2", "simulate_3"]:
            assert needs(p, sim) == ["seed"]

    def test_aggregate_waits_for_all_sims(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert set(needs(p, "aggregate")) == {"simulate_1", "simulate_2", "simulate_3"}

    def test_monte_carlo_pipeline_runs(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        for i in range(1, 4):
            assert (tmp_path / f"sim_{i}.txt").exists()
        assert (tmp_path / "aggregated.txt").exists()
        assert (tmp_path / "decision.txt").exists()

    def test_seed_artifact_collected(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (artifact_dir(tmp_path, "seed") / "seed.txt").exists()

    def test_simulation_failure_blocks_aggregate(self, tmp_path):
        ci = """\
            stages: [seed, simulate, aggregate]
            seed:
              stage: seed
              script: [echo seed]
            simulate_1:
              stage: simulate
              needs: [seed]
              script: [exit 1]
            simulate_2:
              stage: simulate
              needs: [seed]
              script: [echo ok]
            aggregate:
              stage: aggregate
              needs: [simulate_1, simulate_2]
              script:
                - echo "should not run" > aggregated.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "aggregated.txt").exists()

    def test_allow_failure_on_outlier_sim(self, tmp_path):
        """One bad simulation run doesn't block aggregation if allow_failure."""
        ci = """\
            stages: [seed, simulate, aggregate]
            seed:
              stage: seed
              script: [echo seed]
            simulate_1:
              stage: simulate
              needs: [seed]
              allow_failure: true
              script: [exit 1]
            simulate_2:
              stage: simulate
              needs: [seed]
              script:
                - echo ok > sim_2.txt
            aggregate:
              stage: aggregate
              needs: [simulate_2]
              when: always
              script:
                - echo done > aggregated.txt
            """
        write(tmp_path, ci)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "aggregated.txt").exists()


# ===========================================================================
# Scenario 6 — CI as Scientific Experiment Runner
# DAG: hypothesis_A → experiment_A → results_A
#      hypothesis_B → experiment_B → results_B
#                                        ↓
#                                    compare → publish
# ===========================================================================


class TestScenario6ScientificExperiment:
    CI = """\
        stages:
          - hypothesis
          - experiment
          - results
          - compare
          - publish

        hypothesis_A:
          stage: hypothesis
          script:
            - 'echo "H_A=uv is faster" > hypothesis_a.txt'

        hypothesis_B:
          stage: hypothesis
          script:
            - 'echo "H_B=pip is leaner" > hypothesis_b.txt'

        experiment_A:
          stage: experiment
          needs: [hypothesis_A]
          script:
            - echo "uv install time=1.2s" > experiment_a.txt
          artifacts:
            paths: [experiment_a.txt]
            when: on_success

        experiment_B:
          stage: experiment
          needs: [hypothesis_B]
          script:
            - echo "pip install time=4.8s" > experiment_b.txt
          artifacts:
            paths: [experiment_b.txt]
            when: on_success

        results_A:
          stage: results
          needs: [experiment_A]
          script:
            - echo "A wins on speed" > results_a.txt
          artifacts:
            paths: [results_a.txt]
            when: on_success

        results_B:
          stage: results
          needs: [experiment_B]
          script:
            - echo "B wins on size" > results_b.txt
          artifacts:
            paths: [results_b.txt]
            when: on_success

        compare:
          stage: compare
          needs: [results_A, results_B]
          script:
            - echo "comparison done" > comparison.txt

        publish:
          stage: publish
          needs: [compare]
          script:
            - echo "paper submitted" > paper.txt
        """

    def test_two_independent_hypothesis_chains(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "experiment_A") == ["hypothesis_A"]
        assert needs(p, "experiment_B") == ["hypothesis_B"]
        assert needs(p, "results_A") == ["experiment_A"]
        assert needs(p, "results_B") == ["experiment_B"]

    def test_compare_merges_both_chains(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert set(needs(p, "compare")) == {"results_A", "results_B"}

    def test_full_experiment_pipeline(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "experiment_a.txt").exists()
        assert (tmp_path / "experiment_b.txt").exists()
        assert (tmp_path / "results_a.txt").exists()
        assert (tmp_path / "results_b.txt").exists()
        assert (tmp_path / "comparison.txt").exists()
        assert (tmp_path / "paper.txt").exists()

    def test_chain_A_failure_blocks_compare(self, tmp_path):
        """Compare depends directly on both experiments; experiment_A failing
        puts it in failed_jobs, so compare is skipped.
        """
        ci = """\
            stages: [hypothesis, experiment, compare]
            hypothesis_A:
              stage: hypothesis
              script: [echo HA]
            hypothesis_B:
              stage: hypothesis
              script: [echo HB]
            experiment_A:
              stage: experiment
              needs: [hypothesis_A]
              script: [exit 1]
            experiment_B:
              stage: experiment
              needs: [hypothesis_B]
              script:
                - echo ok > experiment_b.txt
            compare:
              stage: compare
              needs: [experiment_A, experiment_B]
              script:
                - echo "should not run" > comparison.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "comparison.txt").exists()


# ===========================================================================
# Scenario 7 — Constraint Solving / Search DAG
# DAG: generate_candidates → evaluate → prune → expand → converge
# ===========================================================================


class TestScenario7ConstraintSearch:
    CI = """\
        stages:
          - generate
          - evaluate
          - prune
          - expand
          - converge

        generate_candidates:
          stage: generate
          script:
            - echo "candidates=[A,B,C,D]" > candidates.txt
          artifacts:
            paths: [candidates.txt]
            when: on_success

        evaluate:
          stage: evaluate
          needs: [generate_candidates]
          script:
            - echo "scores=[0.8,0.4,0.9,0.3]" > scores.txt
          artifacts:
            paths: [scores.txt]
            when: on_success

        prune:
          stage: prune
          needs: [evaluate]
          script:
            - echo "kept=[A,C]" > pruned.txt
          artifacts:
            paths: [pruned.txt]
            when: on_success

        expand:
          stage: expand
          needs: [prune]
          script:
            - echo "expanded=[A1,A2,C1,C2]" > expanded.txt
          artifacts:
            paths: [expanded.txt]
            when: on_success

        converge:
          stage: converge
          needs: [expand]
          script:
            - echo "best=C1" > best_solution.txt
        """

    def test_linear_search_dag(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "evaluate") == ["generate_candidates"]
        assert needs(p, "prune") == ["evaluate"]
        assert needs(p, "expand") == ["prune"]
        assert needs(p, "converge") == ["expand"]

    def test_search_pipeline_runs(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "candidates.txt").exists()
        assert (tmp_path / "scores.txt").exists()
        assert (tmp_path / "pruned.txt").exists()
        assert (tmp_path / "expanded.txt").exists()
        assert (tmp_path / "best_solution.txt").exists()

    def test_evaluate_failure_blocks_convergence(self, tmp_path):
        ci = """\
            stages: [generate, evaluate, converge]
            generate_candidates:
              stage: generate
              script: [echo gen]
            evaluate:
              stage: evaluate
              needs: [generate_candidates]
              script: [exit 1]
            converge:
              stage: converge
              needs: [evaluate]
              script:
                - echo "should not run" > best_solution.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "best_solution.txt").exists()

    def test_each_stage_artifact_collected(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        for job, file in [
            ("generate_candidates", "candidates.txt"),
            ("evaluate", "scores.txt"),
            ("prune", "pruned.txt"),
            ("expand", "expanded.txt"),
        ]:
            assert (artifact_dir(tmp_path, job) / file).exists(), f"missing artifact for {job}"


# ===========================================================================
# Scenario 8 — DAG as Workflow Engine (Human-in-the-Loop)
# DAG: task_A → approval_gate (manual) → task_B → notify
# ===========================================================================


class TestScenario8WorkflowEngine:
    CI = """\
        stages:
          - prepare
          - gate
          - execute
          - notify

        task_A:
          stage: prepare
          script:
            - echo "work done" > task_a.txt

        approval_gate:
          stage: gate
          needs: [task_A]
          when: manual
          script:
            - echo "approved" > approved.txt

        task_B:
          stage: execute
          needs: [approval_gate]
          when: manual
          script:
            - echo "executed after approval" > task_b.txt

        notify:
          stage: notify
          needs: [task_B]
          script:
            - echo "notified" > notify.txt
        """

    def test_approval_gate_is_manual(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        gate = next(j for j in p.jobs if j.name == "approval_gate")
        assert gate.when == "manual"

    def test_task_b_is_also_manual(self, tmp_path):
        """task_B is guarded by when:manual so it won't run automatically."""
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        task_b = next(j for j in p.jobs if j.name == "task_B")
        assert task_b.when == "manual"

    def test_task_b_depends_on_approval_gate(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "task_B") == ["approval_gate"]

    def test_manual_jobs_not_executed_automatically(self, tmp_path):
        """Both manual jobs are skipped; only task_A runs automatically."""
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "task_a.txt").exists()
        assert not (tmp_path / "approved.txt").exists()
        assert not (tmp_path / "task_b.txt").exists()

    def test_task_a_runs_before_gate(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "task_a.txt").exists()

    def test_job_filter_to_pre_gate_work(self, tmp_path):
        """Filtering to just task_A confirms the pre-gate job runs cleanly."""
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(
            maximum_degree_of_parallelism=1,
            job_filter=["task_A"],
        )
        assert (tmp_path / "task_a.txt").exists()
        assert not (tmp_path / "task_b.txt").exists()


# ===========================================================================
# Scenario 9 — Documentation Truth Pipeline
# DAG: code → extract_api → generate_docs → validate_examples → publish
# Twist: validate_examples failure blocks publish (docs as executable truth)
# ===========================================================================


class TestScenario9DocumentationTruth:
    CI = """\
        stages:
          - extract
          - generate
          - validate
          - publish

        extract_api:
          stage: extract
          script:
            - 'echo "api_functions=foo,bar" > api_spec.yaml'
          artifacts:
            paths: [api_spec.yaml]
            when: on_success

        generate_docs:
          stage: generate
          needs: [extract_api]
          script:
            - echo "# API Docs" > docs.md
          artifacts:
            paths: [docs.md]
            when: on_success

        validate_examples:
          stage: validate
          needs: [generate_docs]
          script:
            - echo "all examples valid" > validation.txt

        publish:
          stage: publish
          needs: [validate_examples]
          script:
            - echo "docs published" > published_docs.txt
        """

    def test_dag_chain(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "generate_docs") == ["extract_api"]
        assert needs(p, "validate_examples") == ["generate_docs"]
        assert needs(p, "publish") == ["validate_examples"]

    def test_full_docs_pipeline(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "api_spec.yaml").exists()
        assert (tmp_path / "docs.md").exists()
        assert (tmp_path / "validation.txt").exists()
        assert (tmp_path / "published_docs.txt").exists()

    def test_invalid_examples_block_publish(self, tmp_path):
        ci = """\
            stages: [extract, generate, validate, publish]
            extract_api:
              stage: extract
              script: [echo api]
            generate_docs:
              stage: generate
              needs: [extract_api]
              script: [echo docs]
            validate_examples:
              stage: validate
              needs: [generate_docs]
              script: [exit 1]
            publish:
              stage: publish
              needs: [validate_examples]
              script:
                - echo "should not run" > published_docs.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "published_docs.txt").exists()

    def test_api_artifact_available_to_generate_docs(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (artifact_dir(tmp_path, "extract_api") / "api_spec.yaml").exists()


# ===========================================================================
# Scenario 10 — Security Drift / Policy Enforcement DAG
# DAG: scan → detect_drift → classify → auto_remediate → report
# ===========================================================================


class TestScenario10SecurityDrift:
    CI = """\
        stages:
          - scan
          - drift
          - classify
          - remediate
          - report

        scan:
          stage: scan
          script:
            - echo "findings=3" > scan_results.txt
          artifacts:
            paths: [scan_results.txt]
            when: on_success

        detect_drift:
          stage: drift
          needs: [scan]
          script:
            - echo "drift_detected=true" > drift.txt
          artifacts:
            paths: [drift.txt]
            when: on_success

        classify:
          stage: classify
          needs: [detect_drift]
          script:
            - echo "severity=medium" > classification.txt
          artifacts:
            paths: [classification.txt]
            when: on_success

        auto_remediate:
          stage: remediate
          needs: [classify]
          script:
            - echo "remediation applied" > remediation.txt

        report:
          stage: report
          needs: [auto_remediate]
          script:
            - echo "report generated" > security_report.txt
        """

    def test_security_dag_chain(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "detect_drift") == ["scan"]
        assert needs(p, "classify") == ["detect_drift"]
        assert needs(p, "auto_remediate") == ["classify"]
        assert needs(p, "report") == ["auto_remediate"]

    def test_full_security_pipeline(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "scan_results.txt").exists()
        assert (tmp_path / "drift.txt").exists()
        assert (tmp_path / "classification.txt").exists()
        assert (tmp_path / "remediation.txt").exists()
        assert (tmp_path / "security_report.txt").exists()

    def test_scan_failure_blocks_remediation(self, tmp_path):
        ci = """\
            stages: [scan, remediate]
            scan:
              stage: scan
              script: [exit 1]
            auto_remediate:
              stage: remediate
              needs: [scan]
              script:
                - echo "should not run" > remediation.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "remediation.txt").exists()

    def test_report_runs_always_for_audit(self, tmp_path):
        """Audit report should run even when remediation fails."""
        ci = """\
            stages: [scan, remediate, report]
            scan:
              stage: scan
              script: [echo findings]
            auto_remediate:
              stage: remediate
              needs: [scan]
              script: [exit 1]
            report:
              stage: report
              needs: [auto_remediate]
              when: always
              script:
                - echo "audit report" > security_report.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        # The always report job still produces output
        assert (tmp_path / "security_report.txt").exists()


# ===========================================================================
# Scenario 11 — Artifact Evolution Pipeline
# DAG: input → transform_1 → transform_2 → transform_3 → compare_outputs
# Example: Markdown → HTML → PDF → EPUB → compare
# ===========================================================================


class TestScenario11ArtifactEvolution:
    CI = """\
        stages:
          - source
          - html
          - pdf
          - epub
          - compare

        input:
          stage: source
          script:
            - echo "# Hello World" > doc.md
          artifacts:
            paths: [doc.md]
            when: on_success

        transform_to_html:
          stage: html
          needs: [input]
          script:
            - echo "<h1>Hello World</h1>" > doc.html
          artifacts:
            paths: [doc.html]
            when: on_success

        transform_to_pdf:
          stage: pdf
          needs: [transform_to_html]
          script:
            - echo "%PDF-1.4 Hello World" > doc.pdf
          artifacts:
            paths: [doc.pdf]
            when: on_success

        transform_to_epub:
          stage: epub
          needs: [transform_to_pdf]
          script:
            - echo "EPUB Hello World" > doc.epub
          artifacts:
            paths: [doc.epub]
            when: on_success

        compare_outputs:
          stage: compare
          needs: [transform_to_epub]
          script:
            - echo "all formats verified" > comparison.txt
        """

    def test_progressive_refinement_dag(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "transform_to_html") == ["input"]
        assert needs(p, "transform_to_pdf") == ["transform_to_html"]
        assert needs(p, "transform_to_epub") == ["transform_to_pdf"]
        assert needs(p, "compare_outputs") == ["transform_to_epub"]

    def test_artifact_chain_produces_all_formats(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "doc.md").exists()
        assert (tmp_path / "doc.html").exists()
        assert (tmp_path / "doc.pdf").exists()
        assert (tmp_path / "doc.epub").exists()
        assert (tmp_path / "comparison.txt").exists()

    def test_artifacts_stored_at_each_transform(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (artifact_dir(tmp_path, "input") / "doc.md").exists()
        assert (artifact_dir(tmp_path, "transform_to_html") / "doc.html").exists()
        assert (artifact_dir(tmp_path, "transform_to_pdf") / "doc.pdf").exists()
        assert (artifact_dir(tmp_path, "transform_to_epub") / "doc.epub").exists()

    def test_mid_chain_failure_blocks_compare(self, tmp_path):
        ci = """\
            stages: [source, html, compare]
            input:
              stage: source
              script: [echo md]
            transform_to_html:
              stage: html
              needs: [input]
              script: [exit 1]
            compare_outputs:
              stage: compare
              needs: [transform_to_html]
              script:
                - echo "should not run" > comparison.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "comparison.txt").exists()


# ===========================================================================
# Scenario 12 — LLM Debate / Ensemble DAG
# DAG: prompt → [model_A, model_B, model_C] → judge → final_output
# Twist: judge_disagrees → re_prompt (modelled as allow_failure + always)
# ===========================================================================


class TestScenario12LLMEnsemble:
    CI = """\
        stages:
          - prompt
          - infer
          - judge
          - output

        prompt:
          stage: prompt
          script:
            - 'echo "Q=What is 2+2" > prompt.txt'
          artifacts:
            paths: [prompt.txt]
            when: on_success

        model_A:
          stage: infer
          needs: [prompt]
          script:
            - echo "A_answer=4" > response_a.txt
          artifacts:
            paths: [response_a.txt]
            when: on_success

        model_B:
          stage: infer
          needs: [prompt]
          script:
            - echo "B_answer=4" > response_b.txt
          artifacts:
            paths: [response_b.txt]
            when: on_success

        model_C:
          stage: infer
          needs: [prompt]
          script:
            - echo "C_answer=5" > response_c.txt
          artifacts:
            paths: [response_c.txt]
            when: on_success

        judge:
          stage: judge
          needs: [model_A, model_B, model_C]
          script:
            - echo "consensus=4" > verdict.txt

        final_output:
          stage: output
          needs: [judge]
          script:
            - echo "answer=4" > final.txt
        """

    def test_all_models_depend_on_prompt(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        for model in ["model_A", "model_B", "model_C"]:
            assert needs(p, model) == ["prompt"]

    def test_judge_aggregates_all_models(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert set(needs(p, "judge")) == {"model_A", "model_B", "model_C"}

    def test_ensemble_pipeline_runs(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        for resp in ["response_a.txt", "response_b.txt", "response_c.txt"]:
            assert (tmp_path / resp).exists()
        assert (tmp_path / "verdict.txt").exists()
        assert (tmp_path / "final.txt").exists()

    def test_single_model_failure_blocks_judge(self, tmp_path):
        ci = """\
            stages: [prompt, infer, judge]
            prompt:
              stage: prompt
              script: [echo q]
            model_A:
              stage: infer
              needs: [prompt]
              script: [exit 1]
            model_B:
              stage: infer
              needs: [prompt]
              script: [echo ok]
            judge:
              stage: judge
              needs: [model_A, model_B]
              script:
                - echo "should not run" > verdict.txt
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "verdict.txt").exists()

    def test_degraded_ensemble_with_allow_failure(self, tmp_path):
        """Judge runs on available model outputs if one model allow_fails."""
        ci = """\
            stages: [prompt, infer, judge]
            prompt:
              stage: prompt
              script: [echo q]
            model_A:
              stage: infer
              needs: [prompt]
              allow_failure: true
              script: [exit 1]
            model_B:
              stage: infer
              needs: [prompt]
              script:
                - echo ok > response_b.txt
            model_C:
              stage: infer
              needs: [prompt]
              script:
                - echo ok > response_c.txt
            judge:
              stage: judge
              needs: [model_B, model_C]
              when: always
              script:
                - echo "partial consensus" > verdict.txt
            """
        write(tmp_path, ci)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "verdict.txt").exists()


# ===========================================================================
# Scenario 13 — Genetic Algorithm Pipeline
# DAG: population → [mutate_1..N] → evaluate → select → next_gen
# ===========================================================================


class TestScenario13GeneticAlgorithm:
    CI = """\
        stages:
          - init
          - mutate
          - evaluate
          - select
          - next_gen

        population:
          stage: init
          script:
            - echo "pop=[A,B,C,D,E]" > population.txt
          artifacts:
            paths: [population.txt]
            when: on_success

        mutate_1:
          stage: mutate
          needs: [population]
          script:
            - echo "mutant_1=[A1,B1]" > mutant_1.txt
          artifacts:
            paths: [mutant_1.txt]
            when: on_success

        mutate_2:
          stage: mutate
          needs: [population]
          script:
            - echo "mutant_2=[C1,D1]" > mutant_2.txt
          artifacts:
            paths: [mutant_2.txt]
            when: on_success

        mutate_3:
          stage: mutate
          needs: [population]
          script:
            - echo "mutant_3=[E1,A2]" > mutant_3.txt
          artifacts:
            paths: [mutant_3.txt]
            when: on_success

        evaluate:
          stage: evaluate
          needs: [mutate_1, mutate_2, mutate_3]
          script:
            - echo "fitness=[0.9,0.7,0.8,0.6,0.95,0.4]" > fitness.txt
          artifacts:
            paths: [fitness.txt]
            when: on_success

        select:
          stage: select
          needs: [evaluate]
          script:
            - echo "survivors=[A1,E1,A2]" > survivors.txt

        next_gen:
          stage: next_gen
          needs: [select]
          script:
            - echo "generation=2" > next_gen.txt
        """

    def test_all_mutants_depend_on_population(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        for m in ["mutate_1", "mutate_2", "mutate_3"]:
            assert needs(p, m) == ["population"]

    def test_evaluate_waits_for_all_mutants(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert set(needs(p, "evaluate")) == {"mutate_1", "mutate_2", "mutate_3"}

    def test_genetic_pipeline_runs(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        for i in range(1, 4):
            assert (tmp_path / f"mutant_{i}.txt").exists()
        assert (tmp_path / "fitness.txt").exists()
        assert (tmp_path / "survivors.txt").exists()
        assert (tmp_path / "next_gen.txt").exists()

    def test_mutant_artifacts_collected(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        for i, f in enumerate(["mutant_1.txt", "mutant_2.txt", "mutant_3.txt"], 1):
            assert (artifact_dir(tmp_path, f"mutate_{i}") / f).exists()


# ===========================================================================
# Scenario 14 — Git-as-Database Pipeline
# DAG: read_repo_data → compute → write_new_state → commit
# ===========================================================================


class TestScenario14GitAsDatabase:
    CI = """\
        stages:
          - read
          - compute
          - write
          - commit

        read_repo_data:
          stage: read
          script:
            - echo "historical=[1,2,3,4,5]" > historical.json
          artifacts:
            paths: [historical.json]
            when: on_success

        compute:
          stage: compute
          needs: [read_repo_data]
          script:
            - echo "new_entry=6" > new_state.json
          artifacts:
            paths: [new_state.json]
            when: on_success

        write_new_state:
          stage: write
          needs: [compute]
          script:
            - echo "historical=[1,2,3,4,5,6]" > updated_history.json

        commit:
          stage: commit
          needs: [write_new_state]
          script:
            - echo "committed" > commit.log
        """

    def test_git_db_dag(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "compute") == ["read_repo_data"]
        assert needs(p, "write_new_state") == ["compute"]
        assert needs(p, "commit") == ["write_new_state"]

    def test_pipeline_runs_in_order(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "historical.json").exists()
        assert (tmp_path / "new_state.json").exists()
        assert (tmp_path / "updated_history.json").exists()
        assert (tmp_path / "commit.log").exists()

    def test_read_artifact_available_downstream(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (artifact_dir(tmp_path, "read_repo_data") / "historical.json").exists()
        assert (artifact_dir(tmp_path, "compute") / "new_state.json").exists()

    def test_compute_failure_prevents_commit(self, tmp_path):
        ci = """\
            stages: [read, compute, commit]
            read_repo_data:
              stage: read
              script: [echo data]
            compute:
              stage: compute
              needs: [read_repo_data]
              script: [exit 1]
            commit:
              stage: commit
              needs: [compute]
              script:
                - echo "should not run" > commit.log
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "commit.log").exists()


# ===========================================================================
# Scenario 15 — Self-Modifying Pipeline
# DAG: analyze_pipeline → generate_new_ci → validate_new_ci → commit
# (We can't actually trigger the next run, but we model the generation step)
# ===========================================================================


class TestScenario15SelfModifyingPipeline:
    CI = """\
        stages:
          - analyze
          - generate
          - validate
          - commit

        analyze_pipeline:
          stage: analyze
          script:
            - echo "bottleneck=test_stage" > analysis.txt
          artifacts:
            paths: [analysis.txt]
            when: on_success

        generate_new_ci:
          stage: generate
          needs: [analyze_pipeline]
          script:
            - 'echo "stages=[build,test,deploy]" > new_ci.yml'
          artifacts:
            paths: [new_ci.yml]
            when: on_success

        validate_new_ci:
          stage: validate
          needs: [generate_new_ci]
          script:
            - echo "new CI is valid" > validation.txt

        commit:
          stage: commit
          needs: [validate_new_ci]
          script:
            - echo "committed new .gitlab-ci.yml" > commit.log
        """

    def test_self_modifying_dag(self, tmp_path):
        write(tmp_path, self.CI)
        p = pipeline(tmp_path)
        assert needs(p, "generate_new_ci") == ["analyze_pipeline"]
        assert needs(p, "validate_new_ci") == ["generate_new_ci"]
        assert needs(p, "commit") == ["validate_new_ci"]

    def test_pipeline_runs_and_generates_new_ci(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "analysis.txt").exists()
        assert (tmp_path / "new_ci.yml").exists()
        assert (tmp_path / "validation.txt").exists()
        assert (tmp_path / "commit.log").exists()

    def test_generated_ci_artifact_stored(self, tmp_path):
        write(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (artifact_dir(tmp_path, "generate_new_ci") / "new_ci.yml").exists()

    def test_invalid_generation_blocks_commit(self, tmp_path):
        ci = """\
            stages: [analyze, generate, commit]
            analyze_pipeline:
              stage: analyze
              script: [echo analysis]
            generate_new_ci:
              stage: generate
              needs: [analyze_pipeline]
              script: [exit 1]
            commit:
              stage: commit
              needs: [generate_new_ci]
              script:
                - echo "should not run" > commit.log
            """
        write(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "commit.log").exists()

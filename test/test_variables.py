"""Tests for bitrab.execution.variables."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch


from bitrab.execution.variables import (
    _derive_git_variables,
    _git_head_metadata,
    _project_identity_from_remote,
    load_dotenv_files,
    parse_dotenv,
    VariableManager,
)
from bitrab.models.pipeline import JobConfig


class TestParseDotenv:
    def test_basic_key_value(self):
        assert parse_dotenv("FOO=bar") == {"FOO": "bar"}

    def test_multiple_lines(self):
        result = parse_dotenv("A=1\nB=2\nC=3")
        assert result == {"A": "1", "B": "2", "C": "3"}

    def test_empty_string(self):
        assert parse_dotenv("") == {}

    def test_comment_lines_skipped(self):
        result = parse_dotenv("# comment\nFOO=bar\n# another comment")
        assert result == {"FOO": "bar"}

    def test_indented_comment_skipped(self):
        result = parse_dotenv("   # comment\nFOO=bar")
        assert result == {"FOO": "bar"}

    def test_empty_lines_skipped(self):
        result = parse_dotenv("\n\nFOO=bar\n\n")
        assert result == {"FOO": "bar"}

    def test_export_prefix_stripped(self):
        assert parse_dotenv("export FOO=bar") == {"FOO": "bar"}

    def test_export_with_spaces(self):
        assert parse_dotenv("export   FOO=bar") == {"FOO": "bar"}

    def test_double_quoted_value(self):
        assert parse_dotenv('FOO="bar baz"') == {"FOO": "bar baz"}

    def test_single_quoted_value(self):
        assert parse_dotenv("FOO='bar baz'") == {"FOO": "bar baz"}

    def test_mismatched_quotes_not_stripped(self):
        # Only matching outer quotes are stripped
        result = parse_dotenv("FOO='bar\"")
        assert result == {"FOO": "'bar\""}

    def test_value_with_equals_sign(self):
        # Only the first = is the separator
        result = parse_dotenv("FOO=bar=baz")
        assert result == {"FOO": "bar=baz"}

    def test_no_equals_skipped(self):
        result = parse_dotenv("NOEQUALSSIGN\nFOO=bar")
        assert result == {"FOO": "bar"}

    def test_empty_key_skipped(self):
        result = parse_dotenv("=value\nFOO=bar")
        assert result == {"FOO": "bar"}

    def test_whitespace_around_key(self):
        result = parse_dotenv("  FOO  =bar")
        assert result == {"FOO": "bar"}

    def test_later_value_overrides_earlier(self):
        result = parse_dotenv("FOO=first\nFOO=second")
        assert result == {"FOO": "second"}

    def test_value_with_spaces_preserved(self):
        # No quotes: value is stripped of surrounding whitespace
        result = parse_dotenv("FOO=  hello  ")
        assert result == {"FOO": "hello"}

    def test_single_char_quoted_not_stripped(self):
        # len < 2, quote stripping doesn't apply
        result = parse_dotenv('FOO="')
        assert result == {"FOO": '"'}

    def test_no_escape_processing(self):
        # GitLab doesn't process escapes — backslash-n stays literal
        result = parse_dotenv(r'FOO=hello\nworld')
        assert result == {"FOO": r"hello\nworld"}


class TestLoadDotenvFiles:
    def test_no_files_returns_empty(self, tmp_path):
        result = load_dotenv_files(tmp_path)
        assert result == {}

    def test_loads_dotenv_file(self, tmp_path):
        (tmp_path / ".env").write_text("FOO=from_env\n", encoding="utf-8")
        result = load_dotenv_files(tmp_path)
        assert result == {"FOO": "from_env"}

    def test_loads_bitrab_env_file(self, tmp_path):
        (tmp_path / ".bitrab.env").write_text("BAR=from_bitrab\n", encoding="utf-8")
        result = load_dotenv_files(tmp_path)
        assert result == {"BAR": "from_bitrab"}

    def test_bitrab_env_overrides_dotenv(self, tmp_path):
        (tmp_path / ".env").write_text("FOO=from_env\n", encoding="utf-8")
        (tmp_path / ".bitrab.env").write_text("FOO=from_bitrab\n", encoding="utf-8")
        result = load_dotenv_files(tmp_path)
        assert result == {"FOO": "from_bitrab"}

    def test_both_files_merged(self, tmp_path):
        (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
        (tmp_path / ".bitrab.env").write_text("B=2\n", encoding="utf-8")
        result = load_dotenv_files(tmp_path)
        assert result == {"A": "1", "B": "2"}

    def test_skips_directories_named_dotenv(self, tmp_path):
        # If .env is a directory rather than a file, it should be silently skipped
        (tmp_path / ".env").mkdir()
        result = load_dotenv_files(tmp_path)
        assert result == {}


class TestProjectIdentityFromRemote:
    def test_empty_remote_returns_empty_tuple(self):
        assert _project_identity_from_remote("") == ("", "", "")

    def test_ssh_remote(self):
        ns, path, url = _project_identity_from_remote("git@gitlab.com:myorg/myrepo.git")
        assert ns == "myorg"
        assert path == "myorg/myrepo"
        assert url == "https://gitlab.com/myorg/myrepo"

    def test_https_remote(self):
        ns, path, url = _project_identity_from_remote("https://gitlab.com/myorg/myrepo.git")
        assert ns == "myorg"
        assert path == "myorg/myrepo"
        # .git suffix stripped
        assert not url.endswith(".git")

    def test_https_without_git_suffix(self):
        ns, path, url = _project_identity_from_remote("https://gitlab.com/myorg/myrepo")
        assert ns == "myorg"
        assert path == "myorg/myrepo"

    def test_unrecognised_url_returns_empty_tuple(self):
        assert _project_identity_from_remote("not-a-url") == ("", "", "")


class TestGitHeadMetadata:
    def test_returns_six_tuple_on_success(self, tmp_path):
        # If we're in a real git repo the call succeeds — just check shape
        result = _git_head_metadata(Path.cwd())
        assert isinstance(result, tuple)
        assert len(result) == 6

    def test_returns_empty_strings_for_non_repo(self, tmp_path):
        result = _git_head_metadata(tmp_path)
        assert result == ("", "", "", "", "", "")

    def test_returns_empty_strings_on_git_failure(self, tmp_path):
        with patch("bitrab.execution.variables._git", return_value=""):
            result = _git_head_metadata(tmp_path)
        assert result == ("", "", "", "", "", "")

    def test_returns_empty_strings_when_wrong_field_count(self, tmp_path):
        # git output with wrong number of \x1f separators
        with patch("bitrab.execution.variables._git", return_value="only\x1ffour\x1ffields"):
            result = _git_head_metadata(tmp_path)
        assert result == ("", "", "", "", "", "")


class TestDeriveGitVariables:
    def test_returns_all_expected_keys(self):
        result = _derive_git_variables(Path.cwd())
        expected_keys = {
            "CI_COMMIT_SHA",
            "CI_COMMIT_SHORT_SHA",
            "CI_COMMIT_BRANCH",
            "CI_COMMIT_TAG",
            "CI_COMMIT_REF_NAME",
            "CI_COMMIT_REF_SLUG",
            "CI_COMMIT_TITLE",
            "CI_COMMIT_MESSAGE",
            "CI_COMMIT_AUTHOR",
            "CI_COMMIT_TIMESTAMP",
            "CI_PROJECT_NAMESPACE",
            "CI_PROJECT_PATH",
            "CI_PROJECT_URL",
            "CI_PROJECT_PATH_SLUG",
        }
        assert expected_keys <= result.keys()

    def test_empty_strings_for_non_repo(self, tmp_path):
        result = _derive_git_variables(tmp_path)
        assert result["CI_COMMIT_SHA"] == ""
        assert result["CI_COMMIT_SHORT_SHA"] == ""
        assert result["CI_COMMIT_BRANCH"] == ""

    def test_short_sha_is_first_8_chars(self):
        fake_sha = "abcdef1234567890"
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = (fake_sha, "Author", "a@b.com", "2024-01-01", "msg", "msg")
            with patch("bitrab.execution.variables._git", return_value="main"):
                result = _derive_git_variables(Path.cwd())
        assert result["CI_COMMIT_SHORT_SHA"] == fake_sha[:8]

    def test_tag_takes_precedence_over_branch_for_ref_name(self):
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = ("abc123", "A", "a@b.com", "ts", "title", "msg")
            # _git is called multiple times: branch, tag, remote
            with patch("bitrab.execution.variables._git", side_effect=["main", "v1.0.0", ""]):
                result = _derive_git_variables(Path.cwd())
        assert result["CI_COMMIT_REF_NAME"] == "v1.0.0"
        assert result["CI_COMMIT_TAG"] == "v1.0.0"

    def test_branch_used_when_no_tag(self):
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = ("abc123", "A", "a@b.com", "ts", "title", "msg")
            with patch("bitrab.execution.variables._git", side_effect=["feature/my-branch", "", ""]):
                result = _derive_git_variables(Path.cwd())
        assert result["CI_COMMIT_REF_NAME"] == "feature/my-branch"
        assert result["CI_COMMIT_TAG"] == ""

    def test_ref_slug_replaces_slashes(self):
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = ("abc123", "A", "a@b.com", "ts", "title", "msg")
            with patch("bitrab.execution.variables._git", side_effect=["feature/my-branch", "", ""]):
                result = _derive_git_variables(Path.cwd())
        assert "/" not in result["CI_COMMIT_REF_SLUG"]
        assert result["CI_COMMIT_REF_SLUG"] == "feature-my-branch"

    def test_ref_slug_truncated_to_63_chars(self):
        long_branch = "a" * 80
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = ("abc123", "A", "a@b.com", "ts", "title", "msg")
            with patch("bitrab.execution.variables._git", side_effect=[long_branch, "", ""]):
                result = _derive_git_variables(Path.cwd())
        assert len(result["CI_COMMIT_REF_SLUG"]) <= 63

    def test_author_formatted_correctly(self):
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = ("abc123", "Jane Doe", "jane@example.com", "ts", "title", "msg")
            with patch("bitrab.execution.variables._git", side_effect=["main", "", ""]):
                result = _derive_git_variables(Path.cwd())
        assert result["CI_COMMIT_AUTHOR"] == "Jane Doe <jane@example.com>"

    def test_author_empty_when_no_author_name(self):
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = ("abc123", "", "", "ts", "title", "msg")
            with patch("bitrab.execution.variables._git", side_effect=["main", "", ""]):
                result = _derive_git_variables(Path.cwd())
        assert result["CI_COMMIT_AUTHOR"] == ""

    def test_project_path_slug_lowercased(self):
        with patch("bitrab.execution.variables._git_head_metadata") as mock_meta:
            mock_meta.return_value = ("abc123", "A", "a@b.com", "ts", "title", "msg")
            with patch("bitrab.execution.variables._git", side_effect=["main", "", "git@gitlab.com:MyOrg/MyRepo.git"]):
                result = _derive_git_variables(Path.cwd())
        assert result["CI_PROJECT_PATH_SLUG"] == result["CI_PROJECT_PATH_SLUG"].lower()


class TestVariableManager:
    def _make_job(self, name="test-job", stage="test", variables=None) -> JobConfig:
        return JobConfig(name=name, stage=stage, variables=variables or {})

    def test_prepare_environment_sets_job_name(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        job = self._make_job(name="my-job")
        env = vm.prepare_environment(job)
        assert env["CI_JOB_NAME"] == "my-job"

    def test_prepare_environment_sets_job_stage(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        job = self._make_job(stage="deploy")
        env = vm.prepare_environment(job)
        assert env["CI_JOB_STAGE"] == "deploy"

    def test_prepare_environment_sets_ci_true(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        env = vm.prepare_environment(self._make_job())
        assert env["CI"] == "true"
        assert env["GITLAB_CI"] == "true"

    def test_prepare_environment_sets_project_dir(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        env = vm.prepare_environment(self._make_job())
        assert env["CI_PROJECT_DIR"] == str(tmp_path)

    def test_prepare_environment_job_id_increments(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        env1 = vm.prepare_environment(self._make_job())
        env2 = vm.prepare_environment(self._make_job())
        assert int(env2["CI_JOB_ID"]) > int(env1["CI_JOB_ID"])

    def test_job_variables_override_base(self, tmp_path):
        vm = VariableManager(base_variables={"MY_VAR": "base"}, project_dir=tmp_path)
        job = self._make_job(variables={"MY_VAR": "job"})
        env = vm.prepare_environment(job)
        assert env["MY_VAR"] == "job"

    def test_base_variables_applied(self, tmp_path):
        vm = VariableManager(base_variables={"CUSTOM": "custom_val"}, project_dir=tmp_path)
        env = vm.prepare_environment(self._make_job())
        assert env["CUSTOM"] == "custom_val"

    def test_dotenv_file_loaded(self, tmp_path):
        (tmp_path / ".env").write_text("SECRET=mysecret\n", encoding="utf-8")
        vm = VariableManager(project_dir=tmp_path)
        env = vm.prepare_environment(self._make_job())
        assert env["SECRET"] == "mysecret"

    def test_base_variables_override_dotenv(self, tmp_path):
        (tmp_path / ".env").write_text("FOO=from_env\n", encoding="utf-8")
        vm = VariableManager(base_variables={"FOO": "from_base"}, project_dir=tmp_path)
        env = vm.prepare_environment(self._make_job())
        assert env["FOO"] == "from_base"

    def test_leaked_ci_job_vars_stripped(self, tmp_path):
        # Simulate nested run: parent bitrab set CI_JOB_DIR in environment
        leaked = {
            "CI_JOB_DIR": "/parent/job/dir",
            "CI_JOB_ID": "9999",
            "CI_JOB_STAGE": "leaked_stage",
            "CI_JOB_NAME": "leaked_name",
            "CI_JOB_URL": "https://leaked.url",
        }
        with patch.dict(os.environ, leaked):
            vm = VariableManager(project_dir=tmp_path)
            env = vm.prepare_environment(self._make_job(name="real-job", stage="real-stage"))

        # Per-job vars must come from the current job, not the parent
        assert env["CI_JOB_NAME"] == "real-job"
        assert env["CI_JOB_STAGE"] == "real-stage"
        assert env.get("CI_JOB_DIR") != "/parent/job/dir"

    def test_no_base_variables_defaults_to_empty(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        assert vm.base_variables == {}

    def test_no_project_dir_defaults_to_cwd(self):
        vm = VariableManager()
        assert vm.project_dir == Path.cwd()

    def test_project_name_matches_dir_name(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        env = vm.prepare_environment(self._make_job())
        assert env["CI_PROJECT_NAME"] == tmp_path.name

    def test_prepare_environment_does_not_mutate_shared_base(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        job = self._make_job(variables={"MUTATE_CHECK": "yes"})
        vm.prepare_environment(job)
        # The shared base should not contain the job variable
        assert "MUTATE_CHECK" not in vm._shared_base_env

    def test_ci_pipeline_id_is_numeric_string(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        env = vm.prepare_environment(self._make_job())
        assert env["CI_PIPELINE_ID"].isdigit()

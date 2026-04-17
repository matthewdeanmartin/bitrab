from __future__ import annotations

import os
import re
import subprocess  # nosec B404
import time
from pathlib import Path

from bitrab.models.pipeline import JobConfig

_REMOTE_URL_RE = re.compile(r"[:/]([^/]+)/([^/.]+?)(?:\.git)?$")
_GIT_FIELD_SEP = "\x1f"


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse a dotenv-format string into a key→value dict.

    Follows the subset of dotenv syntax that GitLab's ``artifacts: reports:
    dotenv:`` uses:

    - Lines starting with ``#`` (after optional whitespace) are comments.
    - Empty lines are ignored.
    - ``KEY=VALUE`` — value is everything after the first ``=``.
    - Values may be optionally quoted with single or double quotes; quoted
      values have the surrounding quotes stripped (no escape processing, since
      GitLab itself does not process escapes in dotenv report files).
    - ``export KEY=VALUE`` prefix is accepted.
    - Keys must be non-empty; invalid lines are silently skipped.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip optional leading "export "
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip matching outer quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def load_dotenv_files(project_dir: Path) -> dict[str, str]:
    """Load variables from ``.env`` and ``.bitrab.env`` in *project_dir*.

    This simulates GitLab's CI/CD Settings > Variables: developers put local
    secrets and overrides in a ``.env`` file that is gitignored, and those
    values are available to every job just like they would be if a GitLab
    administrator had added them as project-level CI/CD variables.

    Resolution order (later entries win):
      1. ``.env``          — general project secrets / overrides
      2. ``.bitrab.env``   — bitrab-specific local overrides (takes precedence)

    Neither file is required.  Missing files are silently skipped.
    """
    combined: dict[str, str] = {}
    for name in (".env", ".bitrab.env"):
        candidate = project_dir / name
        if candidate.is_file():
            try:
                combined.update(parse_dotenv(candidate.read_text(encoding="utf-8")))
            except OSError:
                pass
    return combined


# A simple incrementing counter used to generate unique-per-process job IDs.
# GitLab uses globally unique integer IDs; we just need something non-empty and
# distinct across jobs within a single run.
_job_id_counter = 0


def _git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stripped stdout, or '' on any failure."""
    try:
        result = subprocess.run(  # nosec B603 B607
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _git_head_metadata(project_dir: Path) -> tuple[str, str, str, str, str, str]:
    """Return HEAD-derived metadata in one git call.

    The tuple contains:
      sha, author_name, author_email, timestamp, commit_title, commit_message
    """
    output = _git(
        [
            "log",
            "-1",
            f"--pretty=%H{_GIT_FIELD_SEP}%an{_GIT_FIELD_SEP}%ae{_GIT_FIELD_SEP}%cI{_GIT_FIELD_SEP}%s{_GIT_FIELD_SEP}%B",
            "HEAD",
        ],
        project_dir,
    )
    if not output:
        return ("", "", "", "", "", "")
    parts = output.split(_GIT_FIELD_SEP, 5)
    if len(parts) != 6:
        return ("", "", "", "", "", "")
    return tuple(parts)  # type: ignore[return-value]


def _project_identity_from_remote(remote_url: str) -> tuple[str, str, str]:
    """Derive namespace, path, and HTTP-ish URL from a git remote URL."""
    if not remote_url:
        return "", "", ""

    m = _REMOTE_URL_RE.search(remote_url)
    if not m:
        return "", "", ""

    project_namespace = m.group(1)
    project_path = f"{m.group(1)}/{m.group(2)}"
    project_url = re.sub(r"git@([^:]+):(.+?)(?:\.git)?$", r"https://\1/\2", remote_url)
    if project_url.endswith(".git"):
        project_url = project_url[:-4]
    return project_namespace, project_path, project_url


def _derive_git_variables(project_dir: Path) -> dict[str, str]:
    """
    Populate the GitLab CI_COMMIT_* / CI_PROJECT_* variables that GitLab
    derives from the repository at pipeline-trigger time.

    All values fall back to empty string when git is unavailable or the
    directory is not a git repo, so scripts that test for variable emptiness
    (``[ -n "$CI_COMMIT_TAG" ]``) behave the same as they would in GitLab when
    there is no tag.
    """
    sha, author_name, author_email, timestamp, commit_title, commit_message = _git_head_metadata(project_dir)
    if not sha:
        return {
            "CI_COMMIT_SHA": "",
            "CI_COMMIT_SHORT_SHA": "",
            "CI_COMMIT_BRANCH": "",
            "CI_COMMIT_TAG": "",
            "CI_COMMIT_REF_NAME": "",
            "CI_COMMIT_REF_SLUG": "",
            "CI_COMMIT_TITLE": "",
            "CI_COMMIT_MESSAGE": "",
            "CI_COMMIT_AUTHOR": "",
            "CI_COMMIT_TIMESTAMP": "",
            "CI_PROJECT_NAMESPACE": "",
            "CI_PROJECT_PATH": "",
            "CI_PROJECT_URL": "",
            "CI_PROJECT_PATH_SLUG": "",
        }

    short_sha = sha[:8] if sha else ""
    branch = _git(["branch", "--show-current"], project_dir)
    tag = _git(["describe", "--tags", "--exact-match", "HEAD"], project_dir)
    ref_name = tag if tag else branch
    ref_slug = ref_name.replace("/", "-")[:63]  # GitLab slugifies refs

    # Remote URL → derive CI_PROJECT_NAMESPACE / CI_PROJECT_PATH
    remote_url = _git(["remote", "get-url", "origin"], project_dir)
    project_namespace, project_path, project_url = _project_identity_from_remote(remote_url)

    return {
        # Commit identity
        "CI_COMMIT_SHA": sha,
        "CI_COMMIT_SHORT_SHA": short_sha,
        "CI_COMMIT_BRANCH": branch,
        "CI_COMMIT_TAG": tag,
        "CI_COMMIT_REF_NAME": ref_name,
        "CI_COMMIT_REF_SLUG": ref_slug,
        "CI_COMMIT_TITLE": commit_title,
        "CI_COMMIT_MESSAGE": commit_message,
        "CI_COMMIT_AUTHOR": f"{author_name} <{author_email}>" if author_name else "",
        "CI_COMMIT_TIMESTAMP": timestamp,
        # Project identity (derived from remote URL)
        "CI_PROJECT_NAMESPACE": project_namespace,
        "CI_PROJECT_PATH": project_path,
        "CI_PROJECT_URL": project_url,
        "CI_PROJECT_PATH_SLUG": project_path.replace("/", "-").lower(),
    }


class VariableManager:
    """
    Manages environment preparation for job execution.

    Attributes:
        base_variables: Base environment variables.
        gitlab_ci_vars: Simulated GitLab CI built-in variables.
        project_dir: The project root directory.
    """

    def __init__(self, base_variables: dict[str, str] | None = None, project_dir: Path | None = None):
        self.base_variables = base_variables or {}
        self.project_dir = project_dir or Path.cwd()
        self.gitlab_ci_vars = self._get_gitlab_ci_variables()

        # Load .env / .bitrab.env from the project root.  These simulate
        # GitLab CI/CD Settings > Variables so developers can keep local
        # secrets out of the repo while still having them available to jobs.
        # Resolution order: os.environ → built-in CI vars → .env → .bitrab.env
        # → pipeline variables (base_variables).  Pipeline variables win so
        # that what's in .gitlab-ci.yml is always authoritative.
        self.dotenv_vars = load_dotenv_files(self.project_dir)

        # Pre-compute the shared base environment (os.environ + built-ins + base vars).
        # Strip per-job CI vars that may be leaking in from a parent bitrab/GitLab
        # process — otherwise a nested run inherits stale per-job state (e.g. a
        # pytest job inside `bitrab run` seeing the outer job's CI_JOB_DIR).
        base = os.environ.copy()
        for leaked in ("CI_JOB_DIR", "CI_JOB_ID", "CI_JOB_STAGE", "CI_JOB_NAME", "CI_JOB_URL"):
            base.pop(leaked, None)
        self._shared_base_env = base
        self._shared_base_env.update(self.gitlab_ci_vars)
        self._shared_base_env.update(self.dotenv_vars)
        self._shared_base_env.update(self.base_variables)

    def _get_gitlab_ci_variables(self) -> dict[str, str]:
        """
        Get GitLab CI built-in variables that we can simulate locally.

        Git-derived variables (CI_COMMIT_SHA, CI_COMMIT_BRANCH, CI_COMMIT_TAG,
        etc.) are populated by running git commands against the project
        directory.  All values fall back to empty string when git is
        unavailable or the directory is not a repo — matching what GitLab
        itself would expose when those conditions are absent (e.g. no tag on a
        non-tagged commit).
        """
        pipeline_id = str(int(time.time()))  # stable within a single run, unique enough locally

        base: dict[str, str] = {
            "CI": "true",
            "GITLAB_CI": "true",
            "CI_SERVER": "yes",
            "CI_SERVER_NAME": "bitrab (local)",
            # Pipeline / job identifiers
            "CI_PIPELINE_ID": pipeline_id,
            "CI_PIPELINE_SOURCE": "local",
            # Project filesystem
            "CI_PROJECT_DIR": str(self.project_dir),
            "CI_PROJECT_NAME": self.project_dir.name,
            # Per-job values — overwritten in prepare_environment()
            "CI_JOB_ID": "",
            "CI_JOB_STAGE": "",
            "CI_JOB_NAME": "",
            "CI_JOB_URL": "",
        }

        base.update(_derive_git_variables(self.project_dir))
        return base

    def prepare_environment(self, job: JobConfig) -> dict[str, str]:
        """
        Prepare environment variables for job execution.

        Args:
            job: The job configuration.

        Returns:
            A dictionary of prepared environment variables.
        """
        global _job_id_counter
        _job_id_counter += 1

        # Start from the pre-computed base instead of os.environ.copy()
        env = self._shared_base_env.copy()

        # Apply job-specific variables
        env.update(job.variables)
        env["CI_JOB_STAGE"] = job.stage
        env["CI_JOB_NAME"] = job.name
        env["CI_JOB_ID"] = str(_job_id_counter)

        return env

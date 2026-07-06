from __future__ import annotations

import os
import re
import subprocess  # nosec B404
import time
from pathlib import Path

from bitrab.models.pipeline import JobConfig

REMOTE_URL_RE = re.compile(r"[:/]([^/]+)/([^/.]+?)(?:\.git)?$")
GIT_FIELD_SEP = "\x1f"


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
job_id_counter = 0


def git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stripped stdout, or '' on any failure."""
    try:
        result = subprocess.run(  # nosec B603 B607
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def git_head_metadata(project_dir: Path) -> tuple[str, str, str, str, str, str]:
    """Return HEAD-derived metadata in one git call.

    The tuple contains:
      sha, author_name, author_email, timestamp, commit_title, commit_message
    """
    output = git(
        [
            "log",
            "-1",
            f"--pretty=%H{GIT_FIELD_SEP}%an{GIT_FIELD_SEP}%ae{GIT_FIELD_SEP}%cI{GIT_FIELD_SEP}%s{GIT_FIELD_SEP}%B",
            "HEAD",
        ],
        project_dir,
    )
    if not output:
        return ("", "", "", "", "", "")
    parts = output.split(GIT_FIELD_SEP, 5)
    if len(parts) != 6:
        return ("", "", "", "", "", "")
    return tuple(parts)  # type: ignore[return-value]


def project_identity_from_remote(remote_url: str) -> tuple[str, str, str]:
    """Derive namespace, path, and HTTP-ish URL from a git remote URL."""
    if not remote_url:
        return "", "", ""

    m = REMOTE_URL_RE.search(remote_url)
    if not m:
        return "", "", ""

    project_namespace = m.group(1)
    project_path = f"{m.group(1)}/{m.group(2)}"
    project_url = re.sub(r"git@([^:]+):(.+?)(?:\.git)?$", r"https://\1/\2", remote_url)
    if project_url.endswith(".git"):
        project_url = project_url[:-4]
    return project_namespace, project_path, project_url


def derive_github_actions_variables() -> dict[str, str]:
    """Map GitHub Actions environment variables to their GitLab CI equivalents.

    When running inside a GitHub Actions workflow (``GITHUB_ACTIONS=true``),
    many GitLab CI variable names are unpopulated because the runner is GitHub,
    not GitLab.  This function reads the standard ``GITHUB_*`` vars that GitHub
    injects automatically and returns a dict of GitLab-style ``CI_*`` names so
    that pipeline scripts written for GitLab work unchanged on GitHub.

    Only variables that are actually set in the environment are mapped; missing
    GitHub vars produce empty strings (same as GitLab does when a condition is
    absent, e.g. no tag on a non-tagged push).

    Returns an empty dict when ``GITHUB_ACTIONS`` is not ``"true"``.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return {}

    env = os.environ

    sha = env.get("GITHUB_SHA", "")
    short_sha = sha[:8] if sha else ""

    # GITHUB_REF is "refs/heads/<branch>" or "refs/tags/<tag>"
    github_ref = env.get("GITHUB_REF", "")
    github_ref_name = env.get("GITHUB_REF_NAME", "")  # bare name, available since 2021
    github_ref_type = env.get("GITHUB_REF_TYPE", "")  # "branch" or "tag"

    if github_ref_type == "tag":
        branch = ""
        tag = github_ref_name
    elif github_ref_type == "branch":
        branch = github_ref_name
        tag = ""
    else:
        # Fallback: parse from GITHUB_REF
        if github_ref.startswith("refs/tags/"):
            tag = github_ref[len("refs/tags/") :]
            branch = ""
        elif github_ref.startswith("refs/heads/"):
            branch = github_ref[len("refs/heads/") :]
            tag = ""
        else:
            branch = github_ref_name
            tag = ""

    ref_name = tag if tag else branch
    ref_slug = ref_name.replace("/", "-")[:63]

    # GITHUB_REPOSITORY is "owner/repo"
    github_repo = env.get("GITHUB_REPOSITORY", "")
    github_server_url = env.get("GITHUB_SERVER_URL", "https://github.com")
    if github_repo:
        parts = github_repo.split("/", 1)
        project_namespace = parts[0] if parts else ""
        project_path = github_repo
        project_url = f"{github_server_url.rstrip('/')}/{github_repo}"
        project_path_slug = project_path.replace("/", "-").lower()
        project_name = parts[1] if len(parts) > 1 else github_repo
    else:
        project_namespace = ""
        project_path = ""
        project_url = ""
        project_path_slug = ""
        project_name = ""

    # Actor / committer info
    actor = env.get("GITHUB_ACTOR", "")

    # Pipeline / run identifiers
    run_id = env.get("GITHUB_RUN_ID", "")
    run_number = env.get("GITHUB_RUN_NUMBER", "")
    workflow = env.get("GITHUB_WORKFLOW", "")
    event_name = env.get("GITHUB_EVENT_NAME", "push")

    # CI_PIPELINE_SOURCE: map GitHub event names to GitLab pipeline source names
    source_map = {
        "push": "push",
        "pull_request": "merge_request_event",
        "schedule": "schedule",
        "workflow_dispatch": "web",
        "workflow_call": "pipeline",
        "repository_dispatch": "trigger",
        "release": "push",
    }
    pipeline_source = source_map.get(event_name, event_name)

    return {
        # Core CI flags — keep GITLAB_CI so scripts that check it still work
        "CI": "true",
        "GITLAB_CI": "true",
        "CI_SERVER": "yes",
        "CI_SERVER_NAME": "GitHub Actions (via bitrab)",
        # Commit identity
        "CI_COMMIT_SHA": sha,
        "CI_COMMIT_SHORT_SHA": short_sha,
        "CI_COMMIT_BRANCH": branch,
        "CI_COMMIT_TAG": tag,
        "CI_COMMIT_REF_NAME": ref_name,
        "CI_COMMIT_REF_SLUG": ref_slug,
        # Project identity
        "CI_PROJECT_NAMESPACE": project_namespace,
        "CI_PROJECT_PATH": project_path,
        "CI_PROJECT_URL": project_url,
        "CI_PROJECT_PATH_SLUG": project_path_slug,
        "CI_PROJECT_NAME": project_name,
        # Pipeline / job identifiers
        "CI_PIPELINE_ID": run_id,
        "CI_PIPELINE_IID": run_number,
        "CI_PIPELINE_SOURCE": pipeline_source,
        "CI_PIPELINE_URL": f"{project_url}/actions/runs/{run_id}" if project_url and run_id else "",
        # Runner / server info
        "CI_SERVER_URL": github_server_url,
        "CI_RUNNER_DESCRIPTION": f"GitHub Actions runner ({env.get('RUNNER_NAME', '')})",
        "CI_RUNNER_OS": env.get("RUNNER_OS", ""),
        "CI_RUNNER_ARCH": env.get("RUNNER_ARCH", ""),
        # Trigger actor
        "GITLAB_USER_LOGIN": actor,
        "GITLAB_USER_NAME": actor,
        "CI_COMMIT_AUTHOR": actor,
        # Workflow name maps loosely to GitLab's CI_JOB_NAME at the workflow level
        "CI_PIPELINE_NAME": workflow,
        # Pass through the raw GitHub vars so scripts can use them directly too
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": sha,
        "GITHUB_REF": github_ref,
        "GITHUB_REF_NAME": github_ref_name,
        "GITHUB_REF_TYPE": github_ref_type,
        "GITHUB_REPOSITORY": github_repo,
        "GITHUB_ACTOR": actor,
        "GITHUB_RUN_ID": run_id,
        "GITHUB_RUN_NUMBER": run_number,
        "GITHUB_WORKFLOW": workflow,
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_SERVER_URL": github_server_url,
        "GITHUB_WORKSPACE": env.get("GITHUB_WORKSPACE", ""),
    }


def derive_git_variables(project_dir: Path) -> dict[str, str]:
    """
    Populate the GitLab CI_COMMIT_* / CI_PROJECT_* variables that GitLab
    derives from the repository at pipeline-trigger time.

    All values fall back to empty string when git is unavailable or the
    directory is not a git repo, so scripts that test for variable emptiness
    (``[ -n "$CI_COMMIT_TAG" ]``) behave the same as they would in GitLab when
    there is no tag.
    """
    sha, author_name, author_email, timestamp, commit_title, commit_message = git_head_metadata(project_dir)
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
    branch = git(["branch", "--show-current"], project_dir)
    tag = git(["describe", "--tags", "--exact-match", "HEAD"], project_dir)
    ref_name = tag if tag else branch
    ref_slug = ref_name.replace("/", "-")[:63]  # GitLab slugifies refs

    # Remote URL → derive CI_PROJECT_NAMESPACE / CI_PROJECT_PATH
    remote_url = git(["remote", "get-url", "origin"], project_dir)
    project_namespace, project_path, project_url = project_identity_from_remote(remote_url)

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
        self.gitlab_ci_vars = self.get_gitlab_ci_variables()

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
        self.shared_base_env = base
        self.shared_base_env.update(self.gitlab_ci_vars)
        self.shared_base_env.update(self.dotenv_vars)
        self.shared_base_env.update(self.base_variables)

    def get_gitlab_ci_variables(self) -> dict[str, str]:
        """
        Get GitLab CI built-in variables that we can simulate locally.

        When running inside GitHub Actions (``GITHUB_ACTIONS=true``), the
        GitHub-provided environment variables are mapped to their GitLab CI
        equivalents automatically so that pipeline scripts work unchanged.

        Outside GitHub Actions, git-derived variables (CI_COMMIT_SHA,
        CI_COMMIT_BRANCH, CI_COMMIT_TAG, etc.) are populated by running git
        commands against the project directory.  All values fall back to empty
        string when git is unavailable or the directory is not a repo.
        """
        github_vars = derive_github_actions_variables()
        if github_vars:
            # On GitHub Actions: start from the GitHub-mapped vars, then layer
            # on the filesystem path (GitHub sets GITHUB_WORKSPACE but not
            # CI_PROJECT_DIR in GitLab's sense).
            base = github_vars.copy()
            base["CI_PROJECT_DIR"] = str(self.project_dir)
            # Per-job values — overwritten in prepare_environment()
            base.setdefault("CI_JOB_ID", "")
            base.setdefault("CI_JOB_STAGE", "")
            base.setdefault("CI_JOB_NAME", "")
            base.setdefault("CI_JOB_URL", "")
            # Commit metadata from GitHub env is already set; also run git so
            # CI_COMMIT_TITLE and CI_COMMIT_MESSAGE are populated (GitHub does
            # not expose these directly).
            git_vars = derive_git_variables(self.project_dir)
            for key in ("CI_COMMIT_TITLE", "CI_COMMIT_MESSAGE", "CI_COMMIT_TIMESTAMP", "CI_COMMIT_AUTHOR"):
                if git_vars.get(key):
                    base[key] = git_vars[key]
            return base

        pipeline_id = str(int(time.time()))  # stable within a single run, unique enough locally

        base = {
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

        base.update(derive_git_variables(self.project_dir))
        return base

    def prepare_environment(self, job: JobConfig) -> dict[str, str]:
        """
        Prepare environment variables for job execution.

        Args:
            job: The job configuration.

        Returns:
            A dictionary of prepared environment variables.
        """
        global job_id_counter
        job_id_counter += 1

        # Start from the pre-computed base instead of os.environ.copy()
        env = self.shared_base_env.copy()

        # Apply job-specific variables
        env.update(job.variables)
        env["CI_JOB_STAGE"] = job.stage
        env["CI_JOB_NAME"] = job.name
        env["CI_JOB_ID"] = str(job_id_counter)

        return env

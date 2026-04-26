from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import certifi
import urllib3
from ruamel.yaml import YAML

from bitrab.exceptions import GitlabRunnerError


class ConfigurationLoader:
    """
    Loads and processes GitLab CI configuration files.

    Attributes:
        base_path: The base path for resolving configuration files.
        yaml: YAML parser instance.
    """

    def __init__(self, base_path: Path | None = None):
        if not base_path:
            self.base_path = Path.cwd()
        else:
            self.base_path = base_path
        self.yaml = YAML(typ="safe")

    def load_config(self, config_path: Path | None = None) -> dict[str, Any]:
        """
        Load the main configuration file and process includes.

        When *config_path* is not supplied (or equals the default
        ``.gitlab-ci.yml``), the loader first checks whether a
        ``.bitrab-ci.yml`` file exists in the same directory.  If both files
        are present a warning is printed and ``.bitrab-ci.yml`` is used.

        Args:
            config_path: Path to the configuration file.

        Returns:
            The loaded and processed configuration.

        Raises:
            GitLabCIError: If the configuration file is not found or fails to load.
        """
        import warnings

        default_gitlab = self.base_path / ".gitlab-ci.yml"
        default_bitrab = self.base_path / ".bitrab-ci.yml"

        if config_path is None:
            # Auto-detect: prefer .bitrab-ci.yml when available
            if default_bitrab.exists():
                if default_gitlab.exists():
                    warnings.warn(
                        "Both .bitrab-ci.yml and .gitlab-ci.yml exist. Using .bitrab-ci.yml — remove .gitlab-ci.yml or pass -c .gitlab-ci.yml explicitly to suppress this warning.",
                        stacklevel=2,
                    )
                config_path = default_bitrab
            else:
                config_path = default_gitlab

        if not config_path.exists():
            raise GitlabRunnerError(f"Configuration file not found: {config_path}")

        config = self._load_yaml_file(config_path)
        config = self._process_includes(config, config_path.parent)

        return config

    def _fetch_remote_yaml(self, url: str) -> dict[str, Any]:
        """Fetch and parse a remote YAML file over HTTP/HTTPS.

        Args:
            url: The fully qualified URL to fetch.

        Returns:
            The parsed YAML content as a dict.

        Raises:
            GitlabRunnerError: On network errors or non-200 responses or YAML
                parse failures.
        """
        try:
            http = urllib3.PoolManager(ca_certs=certifi.where())
            response = http.request("GET", url, timeout=urllib3.Timeout(connect=10, read=30))
        except urllib3.exceptions.HTTPError as exc:
            raise GitlabRunnerError(f"Failed to fetch remote include {url!r}: {exc}") from exc

        if response.status != 200:
            raise GitlabRunnerError(f"Remote include {url!r} returned HTTP {response.status}")

        try:
            return self.yaml.load(io.BytesIO(response.data)) or {}
        except Exception as exc:
            raise GitlabRunnerError(f"Failed to parse YAML from remote include {url!r}: {exc}") from exc

    def _load_yaml_file(self, file_path: Path) -> dict[str, Any]:
        """
        Load a single YAML file.

        Args:
            file_path: Path to the YAML file.

        Returns:
            The loaded YAML content.

        Raises:
            GitLabCIError: If the file fails to load.
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                return self.yaml.load(f) or {}
        except Exception as e:
            raise GitlabRunnerError(f"Failed to load YAML file {file_path}: {e}") from e

    def _process_includes(
        self, config: dict[str, Any], base_dir: Path, seen_files: set[Path] | None = None
    ) -> dict[str, Any]:
        """
        Recursively process 'include' directives from a GitLab-style YAML config.

        Args:
            config: The configuration dictionary to process.
            base_dir: The base path to resolve relative includes.
            seen_files: Tracks already-included files to avoid infinite recursion.

        Returns:
            The merged configuration.
        """
        seen_files = seen_files or set()

        config = copy.deepcopy(config)
        includes = config.pop("include", [])
        if isinstance(includes, (str, dict)):
            includes = [includes]

        merged_config: dict[str, Any] = {}

        # Sentinel prefix for remote URLs stored in the seen_files path set
        _REMOTE_SENTINEL = "__remote__:"

        for include in includes:
            remote_url: str | None = None
            include_path: Path | None = None

            if isinstance(include, str):
                include_path = (base_dir / include).resolve()
            elif isinstance(include, dict) and "local" in include:
                include_path = (base_dir / include["local"]).resolve()
            elif isinstance(include, dict) and ("remote" in include or "url" in include):
                remote_url = include.get("remote") or include.get("url")
            elif isinstance(include, dict) and "component" in include:
                # ERROR-level: component includes pull in external GitLab registry
                # components that bitrab has no way to resolve locally.  Silently
                # skipping would leave the pipeline in an undefined state (jobs
                # from the component simply vanish), so we raise immediately.
                raise GitlabRunnerError(
                    "include: component is not supported locally. Remove it or replace it with a local include. (bitrab cannot fetch GitLab CI components from a registry.)"
                )
            else:
                # WARNING-level unsupported types (template, project): skip with no
                # crash.  These are informational-only features on GitLab that add
                # jobs we don't have access to locally.  The capability checker in
                # check_capabilities() will already have warned the user about these
                # before any execution reaches this point.
                continue

            if remote_url is not None:
                sentinel = Path(_REMOTE_SENTINEL + remote_url)
                if sentinel in seen_files:
                    continue
                seen_files.add(sentinel)
                included_config = self._fetch_remote_yaml(remote_url)
                included_config = self._process_includes(included_config, base_dir, seen_files)
                merged_config = self._merge_configs(merged_config, included_config)
                continue

            if include_path is None:
                raise GitlabRunnerError("include_path is None")
            if include_path in seen_files:
                continue  # Skip already processed files to prevent recursion

            seen_files.add(include_path)
            included_config = self._load_yaml_file(include_path)
            included_config = self._process_includes(included_config, include_path.parent, seen_files)
            merged_config = self._merge_configs(merged_config, included_config)

        # The current config overrides any previously merged includes
        merged_config = self._merge_configs(merged_config, config)
        return merged_config

    def collect_include_paths(self, config_path: Path | None = None) -> set[Path]:
        """Return the set of all local files transitively included by *config_path*.

        The config_path itself is NOT included in the returned set.
        Remote includes are not returned.

        Args:
            config_path: Path to the root configuration file.

        Returns:
            Set of resolved absolute Paths for all local includes.
        """
        if config_path is None:
            config_path = self.base_path / ".gitlab-ci.yml"

        seen: set[Path] = set()
        self._collect_local_includes(config_path, seen)
        return seen

    def _collect_local_includes(self, file_path: Path, seen: set[Path]) -> None:
        """Recursively collect local include paths into *seen*."""
        try:
            raw = self._load_yaml_file(file_path)
        except Exception:
            return

        includes = raw.get("include", [])
        if isinstance(includes, (str, dict)):
            includes = [includes]

        for include in includes:
            candidate: Path | None = None
            if isinstance(include, str):
                candidate = (file_path.parent / include).resolve()
            elif isinstance(include, dict) and "local" in include:
                candidate = (file_path.parent / include["local"]).resolve()

            if candidate is not None and candidate not in seen and candidate.exists():
                seen.add(candidate)
                self._collect_local_includes(candidate, seen)

    def _merge_configs(self, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        """
        Merge two configuration dictionaries.

        Args:
            base: The base configuration.
            overlay: The overlay configuration.

        Returns:
            The merged configuration.
        """
        result = base.copy()
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value
        return result

from __future__ import annotations

from pathlib import Path
from typing import Any

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

        Args:
            config_path: Path to the configuration file.

        Returns:
            The loaded and processed configuration.

        Raises:
            GitLabCIError: If the configuration file is not found or fails to load.
        """
        if config_path is None:
            config_path = self.base_path / ".gitlab-ci.yml"

        if not config_path.exists():
            raise GitlabRunnerError(f"Configuration file not found: {config_path}")

        config = self._load_yaml_file(config_path)
        config = self._process_includes(config, config_path.parent)

        return config

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

    def _process_includes(self, config: dict[str, Any], base_dir: Path, seen_files: set[Path] | None = None) -> dict[str, Any]:
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

        includes = config.pop("include", [])
        if isinstance(includes, (str, dict)):
            includes = [includes]

        merged_config: dict[str, Any] = {}

        for include in includes:
            if isinstance(include, str):
                include_path = (base_dir / include).resolve()
            elif isinstance(include, dict) and "local" in include:
                include_path = (base_dir / include["local"]).resolve()
            else:
                continue  # Unsupported include type

            if include_path in seen_files:
                continue  # Skip already processed files to prevent recursion

            seen_files.add(include_path)
            included_config = self._load_yaml_file(include_path)
            included_config = self._process_includes(included_config, include_path.parent, seen_files)
            merged_config = self._merge_configs(merged_config, included_config)

        # The current config overrides any previously merged includes
        merged_config = self._merge_configs(merged_config, config)
        return merged_config

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

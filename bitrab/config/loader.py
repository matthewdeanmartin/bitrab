from __future__ import annotations

import copy
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import certifi
import urllib3
from ruamel.yaml import YAML

from bitrab.config.interpolate import interpolate_inputs
from bitrab.config.inputs import InputDefinition, parse_input_definitions, resolve_inputs
from bitrab.exceptions import GitlabRunnerError
from bitrab.include_cache import DEFAULT_TTL_SECONDS, discard_cached, read_cached, write_cached
from bitrab.vendor import read_vendored

logger = logging.getLogger(__name__)
MAX_REMOTE_INCLUDE_BYTES = 5 * 1024 * 1024
MAX_REFERENCE_DEPTH = 10


@dataclass(frozen=True)
class Reference:
    """Unresolved GitLab ``!reference`` path produced by the YAML constructor."""

    path: tuple[str | int, ...]


def _construct_reference(constructor: Any, node: Any) -> Reference:
    values = constructor.construct_sequence(node, deep=True)
    if not values or not all(isinstance(value, (str, int)) for value in values):
        raise GitlabRunnerError("!reference must contain a non-empty sequence of keys")
    return Reference(tuple(values))


@dataclass(frozen=True)
class ConfigDocument:
    """Loaded YAML document set split into GitLab ``spec:`` metadata and body."""

    body: dict[str, Any]
    source: str
    spec: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, InputDefinition] = field(default_factory=dict)


def _resolve_config_auto(base_path: Path) -> Path:
    """Auto-detect the config file from three candidate locations.

    Priority (first found wins):
      1. ``.bitrab/.bitrab-ci.yml``
      2. ``.bitrab-ci.yml``
      3. ``.gitlab-ci.yml``

    A warning is logged whenever more than one candidate exists so the user
    knows which file was chosen.
    """
    dot_bitrab_dir = base_path / ".bitrab" / ".bitrab-ci.yml"
    root_bitrab = base_path / ".bitrab-ci.yml"
    gitlab = base_path / ".gitlab-ci.yml"

    candidates = [p for p in (dot_bitrab_dir, root_bitrab, gitlab) if p.exists()]

    if len(candidates) > 1:
        names = ", ".join(str(p.relative_to(base_path)) for p in candidates)
        chosen = candidates[0]
        logger.warning(
            "Multiple CI config files found (%s). Using %s — pass -c <path> explicitly to use a different one.",
            names,
            chosen.relative_to(base_path),
        )
        return chosen

    if candidates:
        return candidates[0]

    # Default to .gitlab-ci.yml even if it doesn't exist; the caller will
    # produce a clear "file not found" error.
    return gitlab


class ConfigurationLoader:
    """
    Loads and processes GitLab CI configuration files.

    Attributes:
        base_path: The base path for resolving configuration files.
        yaml: YAML parser instance.
    """

    def __init__(
        self,
        base_path: Path | None = None,
        offline: bool = False,
        no_include_cache: bool = False,
        include_cache_ttl: float = DEFAULT_TTL_SECONDS,
    ):
        if not base_path:
            self.base_path = Path.cwd()
        else:
            self.base_path = base_path
        self.offline = offline
        self.no_include_cache = no_include_cache
        self.include_cache_ttl = include_cache_ttl
        self.yaml = YAML(typ="safe")
        self.yaml.constructor.add_constructor("!reference", _construct_reference)
        self.last_resolved_root_inputs: dict[str, str] = {}

    def load_config(self, config_path: Path | None = None) -> dict[str, Any]:
        """
        Load the main configuration file and process includes.

        When *config_path* is not supplied the loader searches three locations
        in priority order and warns when more than one candidate exists:

        1. ``.bitrab/.bitrab-ci.yml``  — project-local bitrab config folder
        2. ``.bitrab-ci.yml``          — root-level bitrab override
        3. ``.gitlab-ci.yml``          — standard GitLab CI file (fallback)

        Args:
            config_path: Path to the configuration file.

        Returns:
            The loaded and processed configuration.

        Raises:
            GitlabRunnerError: If the configuration file is not found or fails to load.
        """
        return self.load_config_with_inputs(config_path=config_path)

    def load_config_with_inputs(
        self,
        config_path: Path | None = None,
        input_values: dict[str, Any] | None = None,
        prompt_missing_inputs: bool = False,
    ) -> dict[str, Any]:
        """
        Load the main configuration file with optional root pipeline inputs.

        Root inputs are compile-time values from the root ``spec:inputs`` header.
        They are resolved and interpolated before includes are processed so they
        can affect include inputs, job names, stages, scripts, and variables.
        """
        if config_path is None:
            config_path = _resolve_config_auto(self.base_path)

        if not config_path.exists():
            raise GitlabRunnerError(f"Configuration file not found: {config_path}")

        config_doc = self._load_yaml_file(config_path)
        resolved_inputs = self._resolve_root_inputs(config_doc, input_values, prompt_missing_inputs)
        self.last_resolved_root_inputs = resolved_inputs
        config = interpolate_inputs(config_doc.body, resolved_inputs, config_doc.source)
        config = self._process_includes(config, config_path.parent)
        config = self._resolve_references(config)

        return config

    def _fetch_remote_yaml(self, url: str) -> ConfigDocument:
        """Fetch and parse a remote YAML file over HTTP/HTTPS.

        Args:
            url: The fully qualified URL to fetch.

        Returns:
            The parsed YAML content as a dict.

        Raises:
            GitlabRunnerError: On network errors or non-200 responses or YAML
                parse failures.
        """
        vendored = read_vendored(self.base_path, url)
        if vendored is not None:
            try:
                return self._load_yaml_documents(io.BytesIO(vendored), url)
            except Exception as exc:
                raise GitlabRunnerError(f"Failed to parse YAML from vendored include {url!r}: {exc}") from exc

        if self.offline:
            raise GitlabRunnerError(
                f"Remote include {url!r} is not vendored and cannot be loaded in offline mode; "
                "run 'bitrab vendor' while network access is available"
            )

        if not self.no_include_cache:
            cached = read_cached(self.base_path, url, self.include_cache_ttl)
            if cached is not None:
                try:
                    return self._load_yaml_documents(io.BytesIO(cached), url)
                except Exception:
                    logger.warning("Discarding corrupt remote include cache entry for %s", url)
                    discard_cached(self.base_path, url)

        try:
            http = urllib3.PoolManager(ca_certs=certifi.where())
            retry = urllib3.util.Retry(
                total=3,
                connect=3,
                read=3,
                status=3,
                backoff_factor=0.25,
                status_forcelist=(500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}),
                raise_on_status=False,
            )
            response = http.request(
                "GET",
                url,
                timeout=urllib3.Timeout(connect=10, read=30),
                retries=retry,
                preload_content=False,
            )
            try:
                content_length = response.headers.get("Content-Length")
                try:
                    declared_size = int(content_length) if content_length is not None else None
                except (TypeError, ValueError):
                    declared_size = None
                if declared_size is not None and declared_size > MAX_REMOTE_INCLUDE_BYTES:
                    raise GitlabRunnerError(
                        f"Remote include {url!r} exceeds the {MAX_REMOTE_INCLUDE_BYTES}-byte size limit"
                    )
                data = response.read(MAX_REMOTE_INCLUDE_BYTES + 1)
            finally:
                response.release_conn()
        except urllib3.exceptions.HTTPError as exc:
            raise GitlabRunnerError(f"Failed to fetch remote include {url!r}: {exc}") from exc

        if response.status != 200:
            raise GitlabRunnerError(f"Remote include {url!r} returned HTTP {response.status}")

        if len(data) > MAX_REMOTE_INCLUDE_BYTES:
            raise GitlabRunnerError(f"Remote include {url!r} exceeds the {MAX_REMOTE_INCLUDE_BYTES}-byte size limit")

        try:
            document = self._load_yaml_documents(io.BytesIO(data), url)
        except Exception as exc:
            raise GitlabRunnerError(f"Failed to parse YAML from remote include {url!r}: {exc}") from exc
        if not self.no_include_cache:
            write_cached(self.base_path, url, data)
        return document

    def _resolve_references(self, config: dict[str, Any]) -> dict[str, Any]:
        """Resolve nested ``!reference`` nodes against the fully merged config."""
        root = copy.deepcopy(config)

        def lookup(reference: Reference) -> Any:
            current: Any = root
            for key in reference.path:
                try:
                    current = current[key]
                except (KeyError, IndexError, TypeError) as exc:
                    rendered = ", ".join(repr(part) for part in reference.path)
                    raise GitlabRunnerError(f"!reference [{rendered}] points to a missing value") from exc
            return copy.deepcopy(current)

        def resolve(value: Any, chain: tuple[tuple[str | int, ...], ...], depth: int) -> Any:
            if depth > MAX_REFERENCE_DEPTH:
                raise GitlabRunnerError(f"!reference nesting exceeds the depth limit of {MAX_REFERENCE_DEPTH}")
            if isinstance(value, Reference):
                if value.path in chain:
                    cycle = " -> ".join("[" + ", ".join(map(str, path)) + "]" for path in (*chain, value.path))
                    raise GitlabRunnerError(f"Circular !reference detected: {cycle}")
                return resolve(lookup(value), (*chain, value.path), depth + 1)
            if isinstance(value, list):
                result: list[Any] = []
                for item in value:
                    resolved = resolve(item, chain, depth)
                    if isinstance(item, Reference) and isinstance(resolved, list):
                        result.extend(resolved)
                    else:
                        result.append(resolved)
                return result
            if isinstance(value, dict):
                return {key: resolve(item, chain, depth) for key, item in value.items()}
            return value

        resolved = resolve(root, (), 0)
        if not isinstance(resolved, dict):
            raise GitlabRunnerError("Resolved pipeline configuration must be a mapping")
        return resolved

    def _load_yaml_file(self, file_path: Path) -> ConfigDocument:
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
                return self._load_yaml_documents(f, str(file_path))
        except Exception as e:
            raise GitlabRunnerError(f"Failed to load YAML file {file_path}: {e}") from e

    def _load_yaml_documents(self, stream: Any, source: str) -> ConfigDocument:
        """Load one GitLab config source, preserving optional ``spec:`` metadata."""
        documents = list(self.yaml.load_all(stream))
        documents = [{} if doc is None else doc for doc in documents]

        if not documents:
            return ConfigDocument(body={}, source=source)
        if not all(isinstance(doc, dict) for doc in documents):
            raise GitlabRunnerError(f"{source}: each YAML document must be a mapping")

        if len(documents) == 1:
            return ConfigDocument(body=documents[0], source=source)
        if len(documents) != 2:
            raise GitlabRunnerError(f"{source}: expected at most two YAML documents: a spec header and a pipeline body")

        header = documents[0]
        body = documents[1]
        if set(header) - {"spec"}:
            raise GitlabRunnerError(f"{source}: the first YAML document may only contain a spec: header")

        spec = header.get("spec", {})
        if spec in (None, {}):
            spec = {}
        if not isinstance(spec, dict):
            raise GitlabRunnerError(f"{source}: spec must be a mapping")

        return ConfigDocument(
            body=body,
            source=source,
            spec=spec,
            inputs=parse_input_definitions(spec, source),
        )

    def _process_includes(
        self, config: dict[str, Any], base_dir: Path, seen_includes: set[str] | None = None
    ) -> dict[str, Any]:
        """
        Recursively process 'include' directives from a GitLab-style YAML config.

        Args:
            config: The configuration dictionary to process.
            base_dir: The base path to resolve relative includes.
            seen_includes: Tracks already-processed include signatures to avoid recursion.

        Returns:
            The merged configuration.
        """
        seen_includes = seen_includes or set()

        config = copy.deepcopy(config)
        includes = config.pop("include", [])
        if isinstance(includes, (str, dict)):
            includes = [includes]

        merged_config: dict[str, Any] = {}

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
                raw_signature = self._include_signature("remote", remote_url, self._raw_include_inputs(include))
                if raw_signature in seen_includes:
                    continue
                seen_includes.add(raw_signature)
                included_doc = self._fetch_remote_yaml(remote_url)
                resolved_inputs = self._resolve_include_inputs(included_doc, include)
                included_body = interpolate_inputs(included_doc.body, resolved_inputs, included_doc.source)
                included_config = self._process_includes(included_body, base_dir, seen_includes)
                merged_config = self._merge_configs(merged_config, included_config)
                continue

            if include_path is None:
                raise GitlabRunnerError("include_path is None")
            included_doc = self._load_yaml_file(include_path)
            resolved_inputs = self._resolve_include_inputs(included_doc, include)
            signature = self._include_signature("local", str(include_path), resolved_inputs)
            if signature in seen_includes:
                continue  # Skip identical include expansions to prevent recursion
            seen_includes.add(signature)
            included_body = interpolate_inputs(included_doc.body, resolved_inputs, included_doc.source)
            included_config = self._process_includes(included_body, include_path.parent, seen_includes)
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
            raw = self._load_yaml_file(file_path).body
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

    def _resolve_include_inputs(self, included_doc: ConfigDocument, include: Any) -> dict[str, str]:
        provided = include.get("inputs") if isinstance(include, dict) else None
        if provided is None:
            provided = {}
        return resolve_inputs(included_doc.inputs, provided, included_doc.source)

    def _resolve_root_inputs(
        self,
        config_doc: ConfigDocument,
        input_values: dict[str, Any] | None,
        prompt_missing_inputs: bool,
    ) -> dict[str, str]:
        provided = dict(input_values or {})
        if prompt_missing_inputs:
            for name, definition in config_doc.inputs.items():
                if name in provided or definition.default is not None:
                    continue
                prompt = f"Input {name}"
                if definition.description:
                    prompt += f" ({definition.description})"
                provided[name] = input(f"{prompt}: ")
        return resolve_inputs(config_doc.inputs, provided, config_doc.source)

    @staticmethod
    def _include_signature(kind: str, location: str, inputs: dict[str, str]) -> str:
        input_part = ",".join(f"{key}={value!r}" for key, value in sorted(inputs.items()))
        return f"{kind}:{location}:{input_part}"

    @staticmethod
    def _raw_include_inputs(include: Any) -> dict[str, str]:
        if not isinstance(include, dict) or not isinstance(include.get("inputs"), dict):
            return {}
        return {str(key): repr(value) for key, value in include["inputs"].items()}

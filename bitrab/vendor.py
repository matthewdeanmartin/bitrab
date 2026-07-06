"""Remote include snapshots for reproducible, offline pipeline loading."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

import certifi
import urllib3
from ruamel.yaml import YAML

from bitrab.exceptions import GitlabRunnerError
from bitrab.toml_backend import load_file
from bitrab.utils.filelock import FileLock

SCHEMA_VERSION = 1
LOCK_NAME = "vendor.lock"
STORE_NAME = "vendor"
_UNSAFE_SEGMENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class VendorEntry:
    """One URL-to-file mapping recorded in ``vendor.lock``."""

    url: str
    sha256: str
    file: str
    fetched_at: str


@dataclass(frozen=True)
class VendorResult:
    """Summary returned by a vendor refresh."""

    entries: tuple[VendorEntry, ...]
    added: tuple[str, ...]
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]


def vendor_dir(project_root: Path) -> Path:
    """Return the project's bitrab state directory."""
    return project_root.resolve() / ".bitrab"


def lock_path(project_root: Path) -> Path:
    """Return the vendor lockfile path."""
    return vendor_dir(project_root) / LOCK_NAME


def sha256_bytes(data: bytes) -> str:
    """Return a lowercase SHA-256 digest for *data*."""
    return hashlib.sha256(data).hexdigest()


def load_lock(project_root: Path) -> dict[str, VendorEntry]:
    """Load and validate the vendor lockfile, keyed by URL."""
    path = lock_path(project_root)
    if not path.is_file():
        return {}
    raw = load_file(path)
    if raw.get("schema") != SCHEMA_VERSION:
        raise GitlabRunnerError(f"Unsupported vendor lock schema in {path}; expected schema = {SCHEMA_VERSION}")
    entries: dict[str, VendorEntry] = {}
    for item in raw.get("include", []):
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) for key in ("url", "sha256", "file", "fetched_at")
        ):
            raise GitlabRunnerError(f"Invalid include entry in vendor lockfile {path}")
        entry = VendorEntry(item["url"], item["sha256"], item["file"], item["fetched_at"])
        if entry.url in entries:
            raise GitlabRunnerError(f"Duplicate URL {entry.url!r} in vendor lockfile {path}")
        entries[entry.url] = entry
    return entries


def _safe_segment(value: str) -> str:
    value = _UNSAFE_SEGMENT.sub("_", unquote(value)).strip(". ")
    if not value or value in {".", ".."}:
        return "_"
    return value


def relative_payload_path(url: str) -> Path:
    """Map an HTTP(S) URL to a readable, cross-platform payload path."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GitlabRunnerError(f"Remote include must use an HTTP(S) URL: {url!r}")
    host = _safe_segment(parsed.hostname)
    if parsed.port is not None:
        host += f"_{parsed.port}"
    parts = [_safe_segment(part) for part in parsed.path.split("/") if part]
    if not parts:
        parts = ["index.yml"]
    if parsed.query or parsed.fragment:
        suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        leaf = Path(parts[-1])
        parts[-1] = f"{leaf.stem}.{suffix}{leaf.suffix}"
    return Path(STORE_NAME, host, *parts)


def entry_path(project_root: Path, entry: VendorEntry) -> Path:
    """Resolve an entry path while preventing lockfile path traversal."""
    state = vendor_dir(project_root)
    candidate = (state / entry.file).resolve()
    try:
        candidate.relative_to(state)
    except ValueError as exc:
        raise GitlabRunnerError(f"Vendor lock entry for {entry.url!r} escapes {state}") from exc
    return candidate


def read_vendored(project_root: Path, url: str) -> bytes | None:
    """Read and hash-check a vendored URL, or return ``None`` when unlocked."""
    state = vendor_dir(project_root)
    if not lock_path(project_root).is_file():
        return None
    with FileLock(state / "vendor.lock.lock"):
        entry = load_lock(project_root).get(url)
        if entry is None:
            return None
        path = entry_path(project_root, entry)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise GitlabRunnerError(f"Vendored include {url!r} is missing at {path}; run 'bitrab vendor'") from exc
        actual = sha256_bytes(data)
        if actual != entry.sha256:
            raise GitlabRunnerError(
                f"Vendored include {path} does not match vendor.lock (expected {entry.sha256}, got {actual}); "
                "run 'bitrab vendor' to refresh it"
            )
        return data


def fetch_url(url: str) -> bytes:
    """Download one remote include."""
    try:
        http = urllib3.PoolManager(ca_certs=certifi.where())
        response = http.request("GET", url, timeout=urllib3.Timeout(connect=10, read=30))
    except urllib3.exceptions.HTTPError as exc:
        raise GitlabRunnerError(f"Failed to fetch remote include {url!r}: {exc}") from exc
    if response.status != 200:
        raise GitlabRunnerError(f"Remote include {url!r} returned HTTP {response.status}")
    return bytes(response.data)


def _documents(data: bytes, source: str) -> list[dict[str, Any]]:
    try:
        yaml = YAML(typ="safe")
        # Vendoring only inspects include edges; preserve !reference sequences
        # as ordinary lists and leave semantic resolution to ConfigurationLoader.
        yaml.constructor.add_constructor(
            "!reference", lambda constructor, node: constructor.construct_sequence(node, deep=True)
        )
        docs = list(yaml.load_all(io.BytesIO(data)))
    except Exception as exc:
        raise GitlabRunnerError(f"Failed to parse YAML from {source!r}: {exc}") from exc
    result = [{} if doc is None else doc for doc in docs]
    if not all(isinstance(doc, dict) for doc in result):
        raise GitlabRunnerError(f"{source}: each YAML document must be a mapping")
    return result


def _body(data: bytes, source: str) -> dict[str, Any]:
    docs = _documents(data, source)
    if not docs:
        return {}
    if len(docs) == 1:
        return docs[0]
    if len(docs) == 2 and set(docs[0]) <= {"spec"}:
        return docs[1]
    raise GitlabRunnerError(f"{source}: expected at most two YAML documents: a spec header and a pipeline body")


def _includes(config: dict[str, Any]) -> list[Any]:
    includes = config.get("include", [])
    return [includes] if isinstance(includes, (str, dict)) else list(includes or [])


def _remote_url(include: Any) -> str | None:
    # Future include:template and include:project support should resolve those
    # references to URLs here, then reuse this same lockfile and payload path.
    if not isinstance(include, dict):
        return None
    value = include.get("remote") or include.get("url")
    if value is None:
        return None
    if not isinstance(value, str):
        raise GitlabRunnerError(f"Remote include URL must be a string, got {value!r}")
    return value


def _discover_root_remotes(config_path: Path) -> set[str]:
    """Discover remote URLs through the root configuration's local include graph."""
    remotes: set[str] = set()
    seen: set[Path] = set()

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            raise GitlabRunnerError(f"Failed to read YAML file {resolved}: {exc}") from exc
        for include in _includes(_body(data, str(resolved))):
            url = _remote_url(include)
            if url:
                remotes.add(url)
            elif isinstance(include, str):
                visit(resolved.parent / include)
            elif isinstance(include, dict) and isinstance(include.get("local"), str):
                visit(resolved.parent / include["local"])

    visit(config_path)
    return remotes


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _toml(entries: list[VendorEntry]) -> str:
    lines = [f"schema = {SCHEMA_VERSION}", ""]
    for entry in entries:
        lines.extend(
            [
                "[[include]]",
                f"url = {json.dumps(entry.url, ensure_ascii=False)}",
                f"sha256 = {json.dumps(entry.sha256)}",
                f"file = {json.dumps(entry.file)}",
                f"fetched_at = {json.dumps(entry.fetched_at)}",
                "",
            ]
        )
    return "\n".join(lines)


def vendor(config_path: Path, fetcher: Callable[[str], bytes] = fetch_url) -> VendorResult:
    """Refresh every remote include reachable from *config_path*."""
    config_path = config_path.resolve()
    root = config_path.parent
    state = vendor_dir(root)
    state.mkdir(parents=True, exist_ok=True)
    with FileLock(state / "vendor.lock.lock"):
        previous = load_lock(root)
        pending = list(sorted(_discover_root_remotes(config_path)))
        payloads: dict[str, bytes] = {}
        while pending:
            url = pending.pop(0)
            if url in payloads:
                continue
            data = fetcher(url)
            payloads[url] = data
            for include in _includes(_body(data, url)):
                child = _remote_url(include)
                if child and child not in payloads:
                    pending.append(child)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        entries: list[VendorEntry] = []
        added: list[str] = []
        changed: list[str] = []
        unchanged: list[str] = []
        used_paths: dict[str, str] = {}
        for url in sorted(payloads):
            data = payloads[url]
            digest = sha256_bytes(data)
            old = previous.get(url)
            relative_path = relative_payload_path(url)
            relative = relative_path.as_posix()
            if relative in used_paths and used_paths[relative] != url:
                suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
                relative_path = relative_path.with_name(f"{relative_path.stem}.{suffix}{relative_path.suffix}")
                relative = relative_path.as_posix()
            used_paths[relative] = url
            fetched_at = old.fetched_at if old and old.sha256 == digest else now
            entry = VendorEntry(url, digest, relative, fetched_at)
            _atomic_write(state / relative, data)
            entries.append(entry)
            if old is None:
                added.append(url)
            elif old.sha256 != digest:
                changed.append(url)
            else:
                unchanged.append(url)
        _atomic_write(lock_path(root), _toml(entries).encode("utf-8"))
    return VendorResult(tuple(entries), tuple(added), tuple(changed), tuple(unchanged))


def check_vendor(config_path: Path) -> list[str]:
    """Return drift and coverage errors without accessing the network."""
    config_path = config_path.resolve()
    root = config_path.parent
    errors: list[str] = []
    state = vendor_dir(root)
    with FileLock(state / "vendor.lock.lock"):
        try:
            entries = load_lock(root)
        except GitlabRunnerError as exc:
            return [str(exc)]
        for entry in entries.values():
            path = entry_path(root, entry)
            if not path.is_file():
                errors.append(f"Missing vendored file: {path} ({entry.url})")
                continue
            actual = sha256_bytes(path.read_bytes())
            if actual != entry.sha256:
                errors.append(f"Vendored file hash mismatch: {path} ({entry.url})")

        pending = list(sorted(_discover_root_remotes(config_path)))
        seen: set[str] = set()
        while pending:
            url = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            locked_entry = entries.get(url)
            if locked_entry is None:
                errors.append(f"Un-vendored remote include: {url}")
                continue
            path = entry_path(root, locked_entry)
            if not path.is_file() or sha256_bytes(path.read_bytes()) != locked_entry.sha256:
                continue
            for include in _includes(_body(path.read_bytes(), url)):
                child = _remote_url(include)
                if child:
                    pending.append(child)
    return errors

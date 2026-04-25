import json
import os
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from bitrab.config.validate_pipeline import GitLabCIValidator, validate_gitlab_ci_yaml


@pytest.fixture
def mock_schema():
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"stages": {"type": "array", "items": {"type": "string"}}, "jobs": {"type": "object"}},
    }


@pytest.fixture
def validator(tmp_path):
    return GitLabCIValidator(cache_dir=str(tmp_path))


def test_fetch_schema_from_url_success(validator, mock_schema):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(mock_schema).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        schema = validator._fetch_schema_from_url()
        assert schema == mock_schema


def test_fetch_schema_from_url_failure(validator):
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("test error")):
        schema = validator._fetch_schema_from_url()
        assert schema is None


def test_load_schema_from_cache_fresh(validator, mock_schema):
    validator.cache_file.write_text(json.dumps(mock_schema))

    # Mock mtime to be now
    with patch("time.time", return_value=time.time()):
        schema = validator._load_schema_from_cache()
        assert schema == mock_schema


def test_load_schema_from_cache_stale(validator, mock_schema):
    validator.cache_file.write_text(json.dumps(mock_schema))

    # Mock mtime to be 8 days ago
    stale_time = time.time() - (8 * 24 * 60 * 60)
    os.utime(validator.cache_file, (stale_time, stale_time))

    schema = validator._load_schema_from_cache()
    assert schema is None


def test_save_schema_to_cache(validator, mock_schema):
    validator._save_schema_to_cache(mock_schema)
    assert validator.cache_file.exists()
    assert json.loads(validator.cache_file.read_text()) == mock_schema


def test_get_schema_order(validator, mock_schema):
    # 1. Test cache hit
    with patch.object(validator, "_load_schema_from_cache", return_value=mock_schema):
        assert validator.get_schema() == mock_schema

    # 2. Test bundled package schema hit (cache miss)
    with patch.object(validator, "_load_schema_from_cache", return_value=None):
        with patch.object(validator, "_load_fallback_schema", return_value=mock_schema):
            with patch.object(validator, "_fetch_schema_from_url") as mock_fetch:
                validator.get_schema.cache_clear()
                assert validator.get_schema() == mock_schema
                mock_fetch.assert_not_called()
    # 3. Test URL fetch hit (cache and package schema miss)
    with patch.object(validator, "_load_schema_from_cache", return_value=None):
        with patch.object(validator, "_load_fallback_schema", return_value=None):
            with patch.object(validator, "_fetch_schema_from_url", return_value=mock_schema):
                with patch.object(validator, "_save_schema_to_cache") as mock_save:
                    validator.get_schema.cache_clear()
                    assert validator.get_schema() == mock_schema
                    mock_save.assert_called_once_with(mock_schema)


def test_get_schema_all_fail(validator):
    with patch.object(validator, "_load_schema_from_cache", return_value=None):
        with patch.object(validator, "_fetch_schema_from_url", return_value=None):
            with patch.object(validator, "_load_fallback_schema", return_value=None):
                validator.get_schema.cache_clear()
                with pytest.raises(RuntimeError, match="Could not load schema"):
                    validator.get_schema()


def test_validate_ci_config_valid(validator, mock_schema):
    yaml_content = "stages: [build, test]"
    with patch.object(validator, "get_schema", return_value=mock_schema):
        is_valid, errors = validator.validate_ci_config(yaml_content)
        assert is_valid is True
        assert not errors


def test_validate_ci_config_invalid(validator, mock_schema):
    # 'stages' should be an array according to our mock_schema
    yaml_content = "stages: build"
    with patch.object(validator, "get_schema", return_value=mock_schema):
        is_valid, errors = validator.validate_ci_config(yaml_content)
        assert is_valid is False
        assert len(errors) > 0
        assert "stages" in errors[0]


def test_validate_ci_config_pragma(validator):
    yaml_content = "# pragma: do-not-validate-schema\nstages: build"
    is_valid, errors = validator.validate_ci_config(yaml_content)
    assert is_valid is True
    assert not errors


def test_validate_ci_config_yaml_error(validator):
    yaml_content = "stages: [unbalanced"
    is_valid, errors = validator.validate_ci_config(yaml_content)
    assert is_valid is False
    assert any("YAML parsing error" in e for e in errors)


def test_validate_gitlab_ci_yaml_convenience(tmp_path, mock_schema):
    yaml_content = "stages: [build]"
    with patch("bitrab.config.validate_pipeline.GitLabCIValidator.get_schema", return_value=mock_schema):
        is_valid, errors = validate_gitlab_ci_yaml(yaml_content, cache_dir=str(tmp_path))
        assert is_valid is True


def test_load_fallback_schema(validator, mock_schema):
    # Verify _load_fallback_schema doesn't crash and returns None when file is absent
    result = validator._load_fallback_schema()
    assert result is None or isinstance(result, dict)

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bitrab.config.schema import find_yaml_files, print_validation_summary, run_validate_all, validate_single_file
from bitrab.config.validate_pipeline import ValidationResult


@pytest.fixture
def temp_yaml_dir(tmp_path):
    d = tmp_path / "yaml_files"
    d.mkdir()
    (d / "file1.yaml").write_text("stages: [build]")
    (d / "file2.yml").write_text("stages: [test]")
    (d / "not_yaml.txt").write_text("hello")
    sub = d / "subdir"
    sub.mkdir()
    (sub / "file3.yaml").write_text("stages: [deploy]")
    return d


def test_find_yaml_files(temp_yaml_dir):
    files = find_yaml_files(temp_yaml_dir)
    assert len(files) == 3
    assert all(f.suffix in [".yaml", ".yml"] for f in files)
    assert any(f.name == "file1.yaml" for f in files)
    assert any(f.name == "file2.yml" for f in files)
    assert any(f.name == "file3.yaml" for f in files)


def test_validate_single_file_exists(temp_yaml_dir):
    file_path = temp_yaml_dir / "file1.yaml"
    with patch("bitrab.config.schema.validate_gitlab_ci_yaml", return_value=(True, [])):
        result = validate_single_file(file_path)
        assert result.file_path == file_path
        assert result.is_valid is True
        assert not result.errors


def test_validate_single_file_not_exists(tmp_path):
    file_path = tmp_path / "nonexistent.yaml"
    result = validate_single_file(file_path)
    assert result.is_valid is False
    assert "File does not exist" in result.errors[0]


def test_validate_single_file_is_dir(tmp_path):
    dir_path = tmp_path / "somedir"
    dir_path.mkdir()
    result = validate_single_file(dir_path)
    assert result.is_valid is False
    assert "Path is not a file" in result.errors[0]


def test_print_validation_summary(capsys):
    results = [
        ValidationResult(file_path=Path("valid.yaml"), is_valid=True, errors=[]),
        ValidationResult(file_path=Path("invalid.yaml"), is_valid=False, errors=["Some error"]),
    ]
    print_validation_summary(results)
    captured = capsys.readouterr()
    assert "VALIDATION SUMMARY" in captured.out
    assert "Total files processed: 2" in captured.out
    assert "Valid files: 1" in captured.out
    assert "Invalid files: 1" in captured.out
    assert "invalid.yaml" in captured.out
    assert "Some error" in captured.out


def test_run_validate_all_serial(temp_yaml_dir, tmp_path):
    output_path = tmp_path / "results.json"
    with patch("bitrab.config.schema.validate_single_file") as mock_validate:
        mock_validate.return_value = ValidationResult(file_path=Path("test.yaml"), is_valid=True, errors=[])

        # Less than 5 files triggers serial processing
        exit_code = run_validate_all(temp_yaml_dir, output_path)

        assert exit_code == 0
        assert mock_validate.call_count == 3


def test_run_validate_all_no_files(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    exit_code = run_validate_all(empty_dir, tmp_path / "out.json")
    assert exit_code == 0


def test_run_validate_all_input_not_exists(tmp_path):
    exit_code = run_validate_all(tmp_path / "nope", tmp_path / "out.json")
    assert exit_code == 2


def test_run_validate_all_parallel(temp_yaml_dir, tmp_path):
    # Mock more than 5 files to trigger parallel path
    files = [temp_yaml_dir / f"file{i}.yaml" for i in range(10)]
    for f in files:
        f.write_text("stages: [test]")

    with patch("bitrab.config.schema.find_yaml_files", return_value=files):
        with patch("bitrab.config.schema.validate_single_file") as mock_validate:
            mock_validate.return_value = ValidationResult(file_path=Path("test.yaml"), is_valid=True, errors=[])

            # We need to mock the ProcessPoolExecutor to avoid real subprocesses in tests if possible,
            # or just let it run if it's fast.
            # Given it's unit tests, let's mock it.
            with patch("bitrab.config.schema.ProcessPoolExecutor") as mock_executor:
                mock_instance = mock_executor.return_value.__enter__.return_value

                # Mock futures
                mock_future = MagicMock()
                mock_future.result.return_value = ValidationResult(
                    file_path=Path("test.yaml"), is_valid=True, errors=[]
                )

                mock_instance.submit.return_value = mock_future

                # mock as_completed to return our futures
                with patch("bitrab.config.schema.as_completed", return_value=[mock_future] * 10):
                    exit_code = run_validate_all(temp_yaml_dir, tmp_path / "out.json")
                    assert exit_code == 0

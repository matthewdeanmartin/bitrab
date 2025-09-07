"""
Validates input folder and output folder assuming all yaml is pipeline yaml.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import orjson

from bitrab.config.validate_pipeline import GitLabCIValidator, ValidationResult, validate_gitlab_ci_yaml
from bitrab.utils.terminal_colors import Colors


def find_yaml_files(directory: Path) -> list[Path]:
    """
    Recursively find all YAML files in the given directory.

    Args:
        directory: Directory to search for YAML files.

    Returns:
        List of Path objects for YAML files.
    """
    yaml_files: list[Path] = []
    for pattern in ["**/*.yaml", "**/*.yml"]:
        # Only include actual files, not directories
        yaml_files.extend(path for path in directory.glob(pattern) if path.is_file())
    return sorted(yaml_files)


def validate_single_file(file_path: Path) -> ValidationResult:
    """
    Validate a single YAML file.

    Args:
        file_path: Path to the YAML file to validate.

    Returns:
        ValidationResult containing the validation outcome.
    """
    try:
        # Double-check that this is actually a file
        if not file_path.exists():
            return ValidationResult(file_path=file_path, is_valid=False, errors=[f"File does not exist: {file_path}"])

        if not file_path.is_file():
            return ValidationResult(file_path=file_path, is_valid=False, errors=[f"Path is not a file: {file_path}"])

        yaml_content = file_path.read_text(encoding="utf-8")
        is_valid, errors = validate_gitlab_ci_yaml(yaml_content)
        return ValidationResult(file_path=file_path, is_valid=is_valid, errors=errors)
    except Exception as e:
        return ValidationResult(file_path=file_path, is_valid=False, errors=[f"Failed to read file: {str(e)}"])


def write_results_to_output(results: list[ValidationResult], output_path: Path) -> None:
    """
    Write validation results to output file in JSON format.

    Args:
        results: List of validation results.
        output_path: Path to write the output file.
    """
    output_data: dict[str, Any] = {
        "summary": {
            "total_files": len(results),
            "valid_files": sum(1 for r in results if r.is_valid),
            "invalid_files": sum(1 for r in results if not r.is_valid),
        },
        "results": [
            {"file": str(result.file_path), "is_valid": result.is_valid, "errors": result.errors} for result in results
        ],
    }

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(orjson.dumps(output_data).decode())


def print_validation_summary(results: list[ValidationResult]) -> None:
    """
    Print a colored summary of validation results.

    Args:
        results: List of validation results.
    """
    total_files = len(results)
    valid_files = sum(1 for r in results if r.is_valid)
    invalid_files = total_files - valid_files

    print(f"\n{Colors.HEADER}{Colors.BOLD}=== VALIDATION SUMMARY ==={Colors.ENDC}")
    print(f"Total files processed: {Colors.OKBLUE}{total_files}{Colors.ENDC}")
    print(f"Valid files: {Colors.OKGREEN}{valid_files}{Colors.ENDC}")
    print(f"Invalid files: {Colors.FAIL}{invalid_files}{Colors.ENDC}")

    if invalid_files > 0:
        print(f"\n{Colors.WARNING}{Colors.BOLD}Files with errors:{Colors.ENDC}")
        for result in results:
            if not result.is_valid:
                print(f"\n{Colors.FAIL}✗ {result.file_path}{Colors.ENDC}")
                for error in result.errors:
                    print(f"  {Colors.FAIL}• {error}{Colors.ENDC}")
    else:
        print(f"\n{Colors.OKGREEN}{Colors.BOLD}✓ All files are valid!{Colors.ENDC}")


def run_validate_all(
    input_dir: Path,
    output_path: Path,
    parallelism: int | None = None,
) -> int:
    """
    Orchestrate the validation of all YAML files in input directory.

    Args:
        input_dir: Directory containing YAML files to validate.
        output_path: Path to write validation results.
        parallelism: Number of parallel processes (None for auto-detect).

    Returns:
        Exit code (0 for success, 1 for validation failures, 2 for errors).
    """
    try:
        # Validate input directory exists
        if not input_dir.exists():
            print(f"{Colors.FAIL}Error: Input directory does not exist: {input_dir}{Colors.ENDC}")
            return 2

        if not input_dir.is_dir():
            print(f"{Colors.FAIL}Error: Input path is not a directory: {input_dir}{Colors.ENDC}")
            return 2

        # Find all YAML files
        yaml_files = find_yaml_files(input_dir)

        if not yaml_files:
            print(f"{Colors.WARNING}Warning: No YAML files found in {input_dir}{Colors.ENDC}")
            return 0

        print(f"{Colors.OKCYAN}Found {len(yaml_files)} YAML files to validate{Colors.ENDC}")

        # Determine parallelism strategy
        if len(yaml_files) <= 5:
            # Serial processing for small number of files
            print(f"{Colors.OKBLUE}Using serial processing{Colors.ENDC}")
            results = []
            for yaml_file in yaml_files:
                print(f"Validating: {yaml_file}")
                result = validate_single_file(yaml_file)
                results.append(result)
        else:
            # prime the cache or we get n schema downloads and n attempts to save it to disk
            validator = GitLabCIValidator()
            validator.get_schema()

            # Parallel processing for larger number of files
            max_workers = parallelism if parallelism else os.cpu_count()
            print(f"{Colors.OKBLUE}Using parallel processing with {max_workers} workers{Colors.ENDC}")

            results = []
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                # Submit all validation tasks
                future_to_file = {
                    executor.submit(validate_single_file, yaml_file): yaml_file for yaml_file in yaml_files
                }

                # Collect results as they complete
                for future in as_completed(future_to_file):
                    yaml_file = future_to_file[future]
                    try:
                        result = future.result()
                        results.append(result)
                        status = (
                            f"{Colors.OKGREEN}✓{Colors.ENDC}" if result.is_valid else f"{Colors.FAIL}✗{Colors.ENDC}"
                        )
                        print(f"{status} {yaml_file}")
                    except Exception as e:
                        print(f"{Colors.FAIL}✗ {yaml_file} - Exception: {e}{Colors.ENDC}")
                        results.append(
                            ValidationResult(
                                file_path=yaml_file, is_valid=False, errors=[f"Processing exception: {str(e)}"]
                            )
                        )

        # Sort results by file path for consistent output
        results.sort(key=lambda r: r.file_path)

        # Write results to output file
        # write_results_to_output(results, output_path)
        # print(f"\n{Colors.OKCYAN}Results written to: {output_path}{Colors.ENDC}")

        # Print summary
        print_validation_summary(results)

        # Return appropriate exit code
        invalid_count = sum(1 for r in results if not r.is_valid)
        return 1 if invalid_count > 0 else 0

    except Exception as e:
        print(f"{Colors.FAIL}Error during validation: {e}{Colors.ENDC}")
        raise

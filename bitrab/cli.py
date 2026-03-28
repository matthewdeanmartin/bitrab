"""
Bitrab - Local GitLab CI Runner
A tool for running GitLab CI pipelines locally.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from bitrab.__about__ import __version__
from bitrab.console import configure_stdio
from bitrab.console import safe_print
from bitrab.exceptions import BitrabError, GitlabRunnerError

if TYPE_CHECKING:
    from bitrab.config.loader import ConfigurationLoader as ConfigurationLoaderType
    from bitrab.config.validate_pipeline import GitLabCIValidator as GitLabCIValidatorType
    from bitrab.plan import LocalGitLabRunner as LocalGitLabRunnerType
    from bitrab.plan import PipelineProcessor as PipelineProcessorType
else:
    ConfigurationLoaderType = Any
    GitLabCIValidatorType = Any
    LocalGitLabRunnerType = Any
    PipelineProcessorType = Any

configure_stdio()

__license__ = """MIT License

Copyright (c) 2025 Matthew Dean Martin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

DEBUG = False

ConfigurationLoader: type[ConfigurationLoaderType] | None = None
PipelineProcessor: type[PipelineProcessorType] | None = None
LocalGitLabRunner: type[LocalGitLabRunnerType] | None = None
GitLabCIValidator: type[GitLabCIValidatorType] | None = None
check_capabilities: Callable[[dict[str, Any]], list[Any]] | None = None


def _ensure_config_dependencies() -> None:
    """Populate config loading/parsing imports lazily so --help stays cheap."""
    global ConfigurationLoader, PipelineProcessor

    if ConfigurationLoader is None:
        from bitrab.config.loader import ConfigurationLoader as _ConfigurationLoader

        ConfigurationLoader = _ConfigurationLoader
    if PipelineProcessor is None:
        from bitrab.plan import PipelineProcessor as _PipelineProcessor

        PipelineProcessor = _PipelineProcessor


def _ensure_runner_dependency() -> None:
    """Populate pipeline runner imports lazily."""
    global LocalGitLabRunner

    if LocalGitLabRunner is None:
        from bitrab.plan import LocalGitLabRunner as _LocalGitLabRunner

        LocalGitLabRunner = _LocalGitLabRunner


def _ensure_validation_dependencies() -> None:
    """Populate validation/capabilities imports lazily."""
    global GitLabCIValidator, check_capabilities

    if GitLabCIValidator is None:
        from bitrab.config.validate_pipeline import GitLabCIValidator as _GitLabCIValidator

        GitLabCIValidator = _GitLabCIValidator
    if check_capabilities is None:
        from bitrab.config.capabilities import check_capabilities as _check_capabilities

        check_capabilities = _check_capabilities


def _get_configuration_loader() -> type[ConfigurationLoaderType]:
    _ensure_config_dependencies()
    return cast(type[ConfigurationLoaderType], ConfigurationLoader)


def _get_pipeline_processor() -> type[PipelineProcessorType]:
    _ensure_config_dependencies()
    return cast(type[PipelineProcessorType], PipelineProcessor)


def _get_local_gitlab_runner() -> type[LocalGitLabRunnerType]:
    _ensure_runner_dependency()
    return cast(type[LocalGitLabRunnerType], LocalGitLabRunner)


def _get_gitlab_ci_validator() -> type[GitLabCIValidatorType]:
    _ensure_validation_dependencies()
    return cast(type[GitLabCIValidatorType], GitLabCIValidator)


def _get_check_capabilities() -> Callable[[dict[str, Any]], list[Any]]:
    _ensure_validation_dependencies()
    return cast(Callable[[dict[str, Any]], list[Any]], check_capabilities)


def setup_logging(verbose: bool, quiet: bool) -> None:
    """Configure logging based on verbosity flags."""
    import logging

    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def load_and_process_config(config_path: Path) -> tuple[dict, Any]:
    """Load and process configuration, returning raw config and pipeline config."""
    try:
        loader = _get_configuration_loader()()
        processor = _get_pipeline_processor()()

        raw_config = loader.load_config(config_path)
        pipeline_config = processor.process_config(raw_config)

        return raw_config, pipeline_config
    except (BitrabError, GitlabRunnerError) as e:
        safe_print(f"❌ Configuration error: {e}", file=sys.stderr)
        if DEBUG:
            sys.exit(1)
        else:
            raise
    except Exception as e:
        safe_print(f"❌ Unexpected error loading config: {e}", file=sys.stderr)
        if DEBUG:
            sys.exit(1)
        else:
            raise


def cmd_run(args: argparse.Namespace) -> None:
    """Execute the pipeline or specific jobs."""
    from bitrab.tui.ci_mode import is_ci_mode, should_use_tui

    config_path = Path(args.config) if args.config else Path(".gitlab-ci.yml")

    if not config_path.exists():
        safe_print(f"❌ Configuration file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    try:
        runner = _get_local_gitlab_runner()(base_path=config_path.parent)

        job_filter: list[str] | None = args.jobs if args.jobs else None
        stage_filter: list[str] | None = args.stage if args.stage else None

        use_tui = should_use_tui(args)
        ci_mode = is_ci_mode() and not use_tui

        if args.dry_run:
            safe_print("🔎 Dry-run mode enabled — jobs will only report what would run and will succeed.")

        runner.run_pipeline(
            config_path=config_path,
            maximum_degree_of_parallelism=args.parallel,
            dry_run=args.dry_run,
            use_tui=use_tui,
            ci_mode=ci_mode,
            job_filter=job_filter,
            stage_filter=stage_filter,
        )

    except (BitrabError, GitlabRunnerError) as e:
        safe_print(f"❌ Execution error: {e}", file=sys.stderr)
        if DEBUG:
            sys.exit(1)
        else:
            raise
    except KeyboardInterrupt:
        safe_print("\n🛑 Pipeline execution interrupted by user", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        safe_print(f"❌ Unexpected error: {e}", file=sys.stderr)
        if DEBUG:
            sys.exit(1)
        else:
            raise


def cmd_list(args: argparse.Namespace) -> None:
    """List all jobs in the pipeline."""
    config_path = Path(args.config) if args.config else Path(".gitlab-ci.yml")

    if not config_path.exists():
        safe_print(f"❌ Configuration file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    _, pipeline_config = load_and_process_config(config_path)

    safe_print("📋 Pipeline Jobs:")
    safe_print(f"   Stages: {', '.join(pipeline_config.stages)}")
    safe_print()

    # Group jobs by stage
    jobs_by_stage: dict[str, list[Any]] = {}
    for job in pipeline_config.jobs:
        jobs_by_stage.setdefault(job.stage, []).append(job)

    for stage in pipeline_config.stages:
        stage_jobs = jobs_by_stage.get(stage, [])
        if stage_jobs:
            safe_print(f"🎯 Stage: {stage}")
            for job in stage_jobs:
                retry_info = ""
                if job.retry_max > 0:
                    retry_info = f" (retry: {job.retry_max})"
                safe_print(f"   • {job.name}{retry_info}")
            safe_print()
        else:
            safe_print(f"⏭️  Stage: {stage} (no jobs)")
            safe_print()


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate the pipeline configuration."""
    import json

    config_path = Path(args.config) if args.config else Path(".gitlab-ci.yml")

    if not config_path.exists():
        safe_print(f"❌ Configuration file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    try:
        # 1. Official Schema Validation
        safe_print(f"🔍 Validating {config_path} against GitLab CI schema...")
        validator = _get_gitlab_ci_validator()()
        yaml_content = config_path.read_text(encoding="utf-8")
        is_valid, schema_errors = validator.validate_ci_config(yaml_content)

        if not is_valid:
            safe_print("❌ Schema validation failed:")
            for error in schema_errors:
                safe_print(f"   • {error}")
            sys.exit(1)

        # 2. Capability validation (informational only — does not block execution)
        raw_config, pipeline_config = load_and_process_config(config_path)

        cap_diags = _get_check_capabilities()(raw_config)
        if cap_diags:
            safe_print("ℹ️  Local execution notes (these features behave differently or are skipped locally):")
            for d in cap_diags:
                safe_print(f"   • {d}")

        # 3. Structural/Semantic Validation
        errors = []
        warnings = []

        # Check for empty pipeline
        if not pipeline_config.jobs:
            errors.append("No jobs defined in pipeline")

        # Check job stages exist
        defined_stages = set(pipeline_config.stages)
        for job in pipeline_config.jobs:
            if job.stage not in defined_stages:
                warnings.append(f"Job '{job.name}' uses undefined stage '{job.stage}'")

        # Check for jobs without scripts
        for job in pipeline_config.jobs:
            if not job.script and not job.before_script and not job.after_script:
                warnings.append(f"Job '{job.name}' has no scripts to execute")

        # Report results
        if errors:
            safe_print("❌ Semantic validation failed:")
            for error in errors:
                safe_print(f"   • {error}")
            sys.exit(1)

        if warnings:
            safe_print("⚠️  Validation passed with warnings:")
            for warning in warnings:
                safe_print(f"   • {warning}")

        safe_print("✅ Configuration is valid")
        safe_print(f"   📊 Found {len(pipeline_config.jobs)} jobs across {len(pipeline_config.stages)} stages")

        if args.output_json:
            # Output pipeline config as JSON for further processing
            pipeline_dict = {
                "stages": pipeline_config.stages,
                "variables": pipeline_config.variables,
                "jobs": [
                    {
                        "name": job.name,
                        "stage": job.stage,
                        "script": job.script,
                        "variables": job.variables,
                        "before_script": job.before_script,
                        "after_script": job.after_script,
                        "retry_max": job.retry_max,
                        "retry_when": job.retry_when,
                        "retry_exit_codes": job.retry_exit_codes,
                    }
                    for job in pipeline_config.jobs
                ],
            }
            safe_print("\n📄 Pipeline configuration (JSON):")
            safe_print(json.dumps(pipeline_dict, indent=2))

    except Exception as e:
        safe_print(f"❌ Validation error: {e}", file=sys.stderr)
        if DEBUG:
            raise
        sys.exit(1)


def cmd_lint(_args: argparse.Namespace) -> None:
    """Lint the pipeline configuration using GitLab's API."""
    safe_print("🔍 GitLab CI Lint")
    safe_print("⚠️  Server-side linting not yet implemented")
    safe_print("   This would validate your .gitlab-ci.yml against GitLab's official linter")
    safe_print("   For now, use 'bitrab validate' for basic local validation")
    sys.exit(1)


def cmd_graph(args: argparse.Namespace) -> None:
    """Generate a visual dependency graph of the pipeline."""
    safe_print("📊 Pipeline Dependency Graph")
    if getattr(args, "dry_run", False):
        safe_print("🔎 Dry-run mode enabled — would generate the pipeline dependency graph without writing files.")
        return
    safe_print("⚠️  Graph generation not yet implemented")
    safe_print("   This would create a visual representation of job dependencies")
    sys.exit(1)


def cmd_debug(args: argparse.Namespace) -> None:
    """Debug pipeline configuration and execution environment."""
    config_path = Path(args.config) if args.config else Path(".gitlab-ci.yml")

    safe_print("🔧 Debug Information")
    safe_print(f"   Config file: {config_path.absolute()}")
    safe_print(f"   Config exists: {config_path.exists()}")
    safe_print(f"   Working directory: {Path.cwd()}")

    if config_path.exists():
        _, pipeline_config = load_and_process_config(config_path)
        safe_print(f"   Jobs found: {len(pipeline_config.jobs)}")
        safe_print(f"   Stages: {pipeline_config.stages}")
        safe_print(f"   Global variables: {len(pipeline_config.variables)}")


def cmd_clean(args: argparse.Namespace) -> None:
    """Clean up artifacts and temporary files."""
    safe_print("🧹 Clean Pipeline Artifacts")
    if getattr(args, "dry_run", False):
        safe_print("🔎 Dry-run mode enabled — would remove build artifacts, cache files, and temporary files.")
        return
    safe_print("⚠️  Cleanup not yet implemented")
    safe_print("   This would remove build artifacts, cache files, etc.")


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="bitrab",
        description="Local GitLab CI Runner - Execute GitLab CI pipelines locally",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  bitrab run                          # Run .gitlab-ci.yml
  bitrab run -c my-ci.yml             # Run specific config file
  bitrab run --dry-run                # Show what would be executed
  bitrab run --jobs build test        # Run specific jobs (if implemented)
  bitrab run --parallel 4             # Use 4 parallel workers
  bitrab list                         # List all jobs
  bitrab validate                     # Validate configuration
  bitrab validate --json              # Output pipeline as JSON

Version: {__version__}
""",
    )

    # Global options
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--license", action="store_true", help="Show license information")
    parser.add_argument(
        "-c", "--config", metavar="PATH", help="Path to GitLab CI configuration file (default: .gitlab-ci.yml)"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress non-error output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands", metavar="COMMAND")

    # Run command
    run_parser = subparsers.add_parser(
        "run", help="Execute the pipeline", description="Execute GitLab CI pipeline locally"
    )
    run_parser.add_argument("--dry-run", action="store_true", help="Show what would be executed without running")
    run_parser.add_argument(
        "--parallel",
        "-j",
        type=int,
        metavar="N",
        help="Number of parallel jobs per stage (default: number of CPU cores)",
    )
    run_parser.add_argument(
        "--jobs", nargs="*", metavar="JOB", help="Run only specified jobs (if not specified, run all jobs)"
    )
    run_parser.add_argument(
        "--stage",
        nargs="*",
        metavar="STAGE",
        help="Run only jobs in specified stages (if not specified, run all stages)",
    )
    run_parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Disable Textual TUI, use plain streaming output",
    )
    run_parser.set_defaults(func=cmd_run)

    # List command
    list_parser = subparsers.add_parser(
        "list", help="List all jobs in the pipeline", description="Display all jobs organized by stages"
    )
    list_parser.set_defaults(func=cmd_list)

    # Validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate pipeline configuration",
        description="Check pipeline configuration for errors and warnings",
    )
    validate_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Output validated pipeline configuration as JSON"
    )
    validate_parser.set_defaults(func=cmd_validate)

    # Lint command
    lint_parser = subparsers.add_parser(
        "lint",
        help="Lint configuration using GitLab API",
        description="Validate configuration against GitLab's official linter (not implemented)",
    )
    lint_parser.set_defaults(func=cmd_lint)

    # Graph command
    graph_parser = subparsers.add_parser(
        "graph",
        help="Generate pipeline dependency graph",
        description="Create visual representation of job dependencies (not implemented)",
    )
    graph_parser.add_argument(
        "--dry-run", action="store_true", help="Show what graph output would be generated without writing files"
    )
    graph_parser.set_defaults(func=cmd_graph)

    # Debug command
    debug_parser = subparsers.add_parser(
        "debug",
        help="Debug pipeline configuration",
        description="Show debug information about pipeline and environment",
    )
    debug_parser.set_defaults(func=cmd_debug)

    # Clean command
    clean_parser = subparsers.add_parser(
        "clean", help="Clean up artifacts", description="Remove build artifacts and temporary files (not implemented)"
    )
    clean_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be removed without deleting files"
    )
    clean_parser.set_defaults(func=cmd_clean)

    return parser


def main() -> None:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Handle license display
    if args.license:
        safe_print(__license__)
        sys.exit(0)

    # Configure logging
    setup_logging(args.verbose, args.quiet)

    # Handle no command (default to run)
    if not args.command:
        args.command = "run"
        args.func = cmd_run
        # Set defaults for run command
        args.dry_run = False
        args.parallel = None
        args.jobs = None
        args.stage = None
        args.no_tui = False

    # Execute the command
    try:
        args.func(args)
    except AttributeError:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

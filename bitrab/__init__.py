from bitrab.console import configure_stdio

configure_stdio()

__all__ = ["main"]


def main() -> None:
    """Lazily import the CLI entry point."""
    from bitrab.cli import main as cli_main

    cli_main()

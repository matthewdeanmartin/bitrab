__all__ = ["main"]


def main() -> None:
    """Lazily import the CLI entry point."""
    from bitrab.cli import main as cli_main

    cli_main()

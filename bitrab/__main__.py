import sys

from bitrab.cli import main

# emoji support
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

if __name__ == "__main__":
    main()

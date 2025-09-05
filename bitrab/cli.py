import sys
from pathlib import Path

from bitrab.plan import best_efforts_run


def run() -> None:
    print(sys.argv)
    config = str(sys.argv[-1:][0])
    print(f"Running {config} ...")
    best_efforts_run(Path(config))


if __name__ == "__main__":
    run()

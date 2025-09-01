import sys
from pathlib import Path

from bitrab.best_effort_runner import best_efforts_run


def run() -> None:
    print(sys.argv)
    config = str(sys.argv[-1:][0])
    print(f"Running {config} ...")
    if not config.endswith("ml"):
        config = ".gitlab-ci.yml"
    best_efforts_run(Path(config))


if __name__ == "__main__":
    run()

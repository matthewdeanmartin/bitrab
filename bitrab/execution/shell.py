from __future__ import annotations

# ANSI color codes for terminal output
import os
import subprocess  # nosec
import sys
import threading

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# Disable colors if NO_COLOR is set in the environment
if os.getenv("NO_COLOR"):
    GREEN = RED = RESET = ""


# Copy of the base environment variables
BASE_ENV = os.environ.copy()


def merge_env(env=None):
    """
    Merge os.environ and an env dict into a new dict.
    Values from env override os.environ on conflict.

    Args:
        env: Optional dict of environment variables.

    Returns:
        A merged dict suitable for subprocess calls.
    """
    if env:
        return {**BASE_ENV, **env}
    return BASE_ENV


def run_colored(script: str, env=None, cwd=None) -> int:
    """
    Run a script in a subprocess with colored output for stdout and stderr.

    Args:
        script: The script to execute.
        env: Optional environment variables for the subprocess.
        cwd: Optional working directory for the subprocess.

    Returns:
        The return code of the subprocess.

    Raises:
        subprocess.CalledProcessError: If the subprocess exits with a non-zero code.
    """
    env = merge_env(env)

    # Disable colors if NO_COLOR is set
    if os.getenv("NO_COLOR"):
        g, r, reset = "", "", ""
    else:
        g, r, reset = GREEN, RED, RESET

    def stream(pipe, color, target):
        """
        Stream output from a pipe to a target with optional color.

        Args:
            pipe: The pipe to read from.
            color: The color to apply to the output.
            target: The target to write the output to.
        """
        for line in iter(pipe.readline, ""):  # text mode here, so sentinel is ""
            if not line:
                break
            target.write(f"{color}{line}{reset}")
            target.flush()
        pipe.close()

    # Determine the bash executable based on the operating system
    if os.name == "nt":
        bash = [r"C:\Program Files\Git\bin\bash.exe"]
    else:
        bash = ["bash"]

    if os.environ.get("bitrab_RUN_LOAD_BASHRC"):
        bash.append("-l")
    # Start the subprocess
    with subprocess.Popen(  # nosec
        # , "-l"  # -l loads .bashrc and make it really, really slow.
        bash,  # bash reads script from stdin
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # to prevent \r
        bufsize=1,  # line-buffered
    ) as process:
        # Start threads to stream stdout and stderr in parallel
        threads = [
            threading.Thread(target=stream, args=(process.stdout, g, sys.stdout)),
            threading.Thread(target=stream, args=(process.stderr, r, sys.stderr)),
        ]
        for t in threads:
            t.start()

        # Feed the script and close stdin

        if os.name == "nt":
            script = script.replace("\r\n", "\n")

        if process.stdin:
            # without this it will keep going on errors
            robust_script_content = f"set -eo pipefail\n{script}"
            process.stdin.write(robust_script_content)
            process.stdin.close()

        # Wait for process to finish
        for t in threads:
            t.join()

        process.wait()

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, script)

        return process.returncode

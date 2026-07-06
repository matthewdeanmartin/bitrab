"""Cross-platform advisory file locking (stdlib only).

Used by every persistent store under ``.bitrab/`` (cache, fingerprints,
vendor snapshots) to guard against concurrent writers: parallel jobs in one
run, and concurrent bitrab processes (watch mode + a manual run).

Implementation:
  - Windows: ``msvcrt.locking`` on the first byte of the lock file.
  - POSIX: ``fcntl.flock`` with ``LOCK_EX``.

Both are advisory — all cooperating writers must go through :class:`FileLock`.
Acquisition is non-blocking with a poll loop so a timeout can be enforced
portably.  On timeout :class:`FileLockTimeout` is raised; callers are expected
to log a warning and *skip* the guarded step rather than fail the job.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import TracebackType

if sys.platform == "win32":
    import msvcrt

    def lock_fd(fd: int) -> None:
        """Try to lock *fd* without blocking. Raises OSError if already locked."""
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def unlock_fd(fd: int) -> None:
        """Release the lock held on *fd*."""
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def lock_fd(fd: int) -> None:
        """Try to lock *fd* without blocking. Raises OSError if already locked."""
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock_fd(fd: int) -> None:
        """Release the lock held on *fd*."""
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLockTimeout(TimeoutError):
    """Raised when a :class:`FileLock` cannot be acquired within its timeout."""


class FileLock:
    """A cross-platform advisory lock backed by a lock file.

    Usage::

        with FileLock(store_dir / "mykey.lock", timeout=30.0):
            ...  # exclusive access to the keyed resource

    The lock file itself is never deleted (deleting a lock file while another
    process holds an fd to it is a classic race on POSIX); it is a zero-byte
    sentinel that costs nothing to leave behind.

    Attributes:
        path: Location of the lock file (parent directories are created).
        timeout: Maximum seconds to wait for acquisition.
        poll_interval: Sleep between non-blocking acquisition attempts.
    """

    def __init__(self, path: Path, timeout: float = 30.0, poll_interval: float = 0.05):
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.fd: int | None = None

    def acquire(self) -> None:
        """Acquire the lock, polling until *timeout* elapses.

        Raises:
            FileLockTimeout: If the lock is still held elsewhere after
                *timeout* seconds.
        """
        if self.fd is not None:
            raise RuntimeError(f"FileLock {self.path} is already held by this instance")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            while True:
                try:
                    lock_fd(fd)
                    self.fd = fd
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise FileLockTimeout(
                            f"Could not acquire lock {self.path} within {self.timeout:.1f}s"
                        ) from None
                    time.sleep(self.poll_interval)
        except BaseException:
            if self.fd is None:
                os.close(fd)
            raise

    def release(self) -> None:
        """Release the lock if held. Safe to call when not held."""
        if self.fd is None:
            return
        try:
            unlock_fd(self.fd)
        finally:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

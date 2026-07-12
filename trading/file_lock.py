"""Small cross-platform advisory file lock for local runtime state."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import IO


class FileLock:
    """Hold an exclusive OS-backed lock for the lifetime of this context."""

    def __init__(self, path: Path, *, timeout: float | None = None):
        self.path = Path(path)
        self.timeout = timeout
        self._handle: IO[bytes] | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        while True:
            try:
                self._acquire(handle)
                self._handle = handle
                return self
            except (BlockingIOError, OSError):
                if deadline is not None and time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(f"timed out acquiring file lock: {self.path}")
                time.sleep(0.05)

    @staticmethod
    def _acquire(handle: IO[bytes]) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

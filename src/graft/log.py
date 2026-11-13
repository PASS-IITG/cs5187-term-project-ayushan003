"""JSON-lines logging to ``logs/<name>.jsonl``.

SPEC "Logs/heartbeat/stats": every log in this project is one JSON object per
line.  The log directory is ``<repo>/logs`` (git-ignored) unless overridden with
``GRAFT_LOG_DIR``.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, IO

from graft.config import REPO_ROOT

LOG_DIR_ENV = "GRAFT_LOG_DIR"
_LOGGERS: dict[Path, "JsonlLogger"] = {}
_LOGGERS_LOCK = threading.Lock()


def log_dir() -> Path:
    """Return the directory logs are written to (``GRAFT_LOG_DIR`` wins)."""
    override = os.environ.get(LOG_DIR_ENV)
    return Path(override).expanduser() if override else REPO_ROOT / "logs"


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


class JsonlLogger:
    """Append-only JSON-lines logger for one log name.

    Each :meth:`log` call writes one line holding ``ts``, ``event``, and the
    caller's fields.  Values that are not JSON-serialisable are written as their
    ``repr`` rather than raising, so logging never breaks a running campaign.
    """

    def __init__(self, name: str, directory: str | os.PathLike[str] | None = None):
        """Open ``<directory>/<name>.jsonl`` for appending.

        Args:
            name: log name; must be a bare file name (no path separators).
            directory: destination directory; defaults to :func:`log_dir`.

        Raises:
            ValueError: if ``name`` is empty or contains a path separator.
        """
        if not name or os.sep in name or (os.altsep and os.altsep in name) or "/" in name:
            raise ValueError(f"log name must be a bare file name, got {name!r}")
        if name in (".", ".."):
            raise ValueError(f"log name must be a bare file name, got {name!r}")
        self.name = name
        self.directory = Path(directory) if directory is not None else log_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{name}.jsonl"
        self._lock = threading.Lock()
        self._fh: IO[str] | None = self.path.open("a", encoding="utf-8", buffering=1)

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        """Write one record and return it.

        Args:
            event: short event name, e.g. ``"query"`` or ``"restart"``.
            **fields: extra JSON fields; ``ts`` and ``event`` are reserved.

        Returns:
            The record as written.

        Raises:
            ValueError: if the logger is closed.
        """
        record: dict[str, Any] = {"ts": utc_now(), "event": event}
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=repr)
        with self._lock:
            if self._fh is None:
                raise ValueError(f"logger {self.name!r} is closed")
            self._fh.write(line + "\n")
        return record

    def close(self) -> None:
        """Close the underlying file. Safe to call more than once."""
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run."""
        return self._fh is None

    def __enter__(self) -> "JsonlLogger":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def get_logger(name: str, directory: str | os.PathLike[str] | None = None) -> JsonlLogger:
    """Return a process-wide shared :class:`JsonlLogger` for ``name``.

    A closed cached logger is replaced by a fresh one.
    """
    directory_path = Path(directory) if directory is not None else log_dir()
    key = (directory_path / f"{name}.jsonl").resolve()
    with _LOGGERS_LOCK:
        existing = _LOGGERS.get(key)
        if existing is not None and not existing.closed:
            return existing
        logger = JsonlLogger(name, directory_path)
        _LOGGERS[key] = logger
        return logger

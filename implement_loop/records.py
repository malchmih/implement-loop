"""Private per-run records and conservative startup retention."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import LoopError

_RUN_NAME = re.compile(r"^run-([0-9]{8}T[0-9]{6}Z)-([A-Za-z0-9]{6})$")
_RUN_SUFFIX_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SECONDS_PER_DAY = 24 * 60 * 60


@dataclass(frozen=True)
class RunRecordSettings:
    """Resolved location and expiry policy for private run records."""

    directory: Path
    retention_days: int | None


class RunRecords:
    """A private run directory containing context, prompts, and process evidence."""

    def __init__(self, records_dir: Path, run_dir: Path, started_at: datetime) -> None:
        self.records_dir = records_dir
        self.run_dir = run_dir
        self.started_at = started_at
        self.active_marker = run_dir / "active.pid"

    @classmethod
    def create(
        cls,
        target_root: Path,
        settings: RunRecordSettings,
    ) -> RunRecords:
        records_dir = settings.directory.resolve()
        repository_root = target_root.resolve()
        home = Path.home().resolve()
        if (
            records_dir.is_relative_to(repository_root)
            or repository_root.is_relative_to(records_dir)
            or records_dir == Path("/")
            or records_dir == home
        ):
            raise LoopError(
                "The run-record location must be a dedicated directory outside the target "
                "checkout, not an ancestor of it, the home directory, or filesystem root. "
                "Choose an external --records-dir, records.directory, or XDG_STATE_HOME."
            )

        try:
            records_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            records_dir.chmod(0o700)
        except OSError as error:
            raise LoopError(
                f"Cannot create private run-record directory {records_dir}: {error}"
            ) from error

        started_at = datetime.now(UTC).replace(microsecond=0)
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        run_dir: Path | None = None
        for _ in range(20):
            suffix = "".join(secrets.choice(_RUN_SUFFIX_ALPHABET) for _ in range(6))
            candidate = records_dir / f"run-{stamp}-{suffix}"
            try:
                candidate.mkdir(mode=0o700)
                run_dir = candidate
                break
            except FileExistsError:
                continue
            except OSError as error:
                raise LoopError(
                    f"Cannot create private run records at {candidate}: {error}"
                ) from error
        if run_dir is None:
            raise LoopError("Cannot allocate a unique private run-record directory.")

        records = cls(records_dir=records_dir, run_dir=run_dir, started_at=started_at)
        try:
            records.write_text(records.active_marker, f"{os.getpid()}\n", exclusive=True)
        except OSError as error:
            raise LoopError(f"Cannot create the active run marker: {error}") from error
        records.prune_expired(settings.retention_days)
        return records

    def attempt_directory(self, attempt_number: int, ticket_number: int) -> Path:
        path = self.run_dir / f"attempt-{attempt_number}-ticket-{ticket_number}"
        try:
            path.mkdir(mode=0o700)
        except OSError as error:
            raise LoopError(f"Cannot create private attempt records at {path}: {error}") from error
        return path

    def write_text(self, path: Path, value: str, *, exclusive: bool = False) -> None:
        self.write_bytes(path, value.encode("utf-8"), exclusive=exclusive)

    def write_json(self, path: Path, value: Any, *, exclusive: bool = False) -> None:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.write_text(path, text, exclusive=exclusive)

    def write_bytes(self, path: Path, value: bytes, *, exclusive: bool = False) -> None:
        resolved_path = path.resolve(strict=False)
        if not resolved_path.is_relative_to(self.run_dir.resolve()):
            raise LoopError(f"Refusing to write a run record outside {self.run_dir}.")
        flags = os.O_WRONLY | os.O_CREAT
        flags |= os.O_EXCL if exclusive else os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as record:
                record.write(value)
        except OSError as error:
            raise LoopError(f"Cannot persist required run record {path}: {error}") from error

    def prune_expired(self, retention_days: int | None) -> None:
        """Remove only expired, unmarked run directories; cleanup never blocks a run."""

        if retention_days is None:
            return

        try:
            entries = list(os.scandir(self.records_dir))
        except OSError as error:
            self._warn(f"Cannot scan run records at {self.records_dir}; skipping cleanup: {error}")
            return

        cutoff = time.time() - retention_days * _SECONDS_PER_DAY
        for entry in entries:
            if not entry.name.startswith("run-") or entry.is_symlink():
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError as error:
                self._warn(f"Cannot inspect old run record {entry.path}; keeping it: {error}")
                continue
            match = _RUN_NAME.fullmatch(entry.name)
            if match is None:
                continue

            stamp = match.group(1)
            try:
                started_at = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                if started_at.strftime("%Y%m%dT%H%M%SZ") != stamp:
                    raise ValueError("timestamp did not round-trip")
            except ValueError:
                self._warn(f"Cannot read the start timestamp for {entry.path}; keeping it.")
                continue
            if started_at.timestamp() >= cutoff:
                continue

            marker = Path(entry.path) / "active.pid"
            if os.path.lexists(marker):
                self._warn(f"Keeping expired run with an active marker: {entry.path}")
                continue
            try:
                shutil.rmtree(entry.path)
            except OSError as error:
                self._warn(
                    f"Cannot remove expired run {entry.path}; a later startup will retry: {error}"
                )

    def finish(self) -> None:
        """Remove this process's active marker after the wrapper has stopped."""

        try:
            owner = self.active_marker.read_text(encoding="ascii").strip()
            if owner == str(os.getpid()):
                self.active_marker.unlink()
            elif os.path.lexists(self.active_marker):
                self._warn(f"Leaving changed active marker in {self.run_dir}.")
        except FileNotFoundError:
            return
        except OSError as error:
            self._warn(f"Cannot clear the active marker for {self.run_dir}: {error}")

    @staticmethod
    def _warn(message: str) -> None:
        print(f"Warning: {message}", file=sys.stderr)
